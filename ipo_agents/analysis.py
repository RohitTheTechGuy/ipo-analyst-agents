"""Per-IPO analysis graph (spec seam B)."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ipo_agents.adapters import Adapters
from ipo_agents.budget import BudgetMeter, MeteredLLM
from ipo_agents.config import Config
from ipo_agents.contracts import Ipo, OfferObjects, Report, SectionedDocument, Sentiment
from ipo_agents.ingestion import ingest
from ipo_agents.router import estimate_run_usd
from ipo_agents.sentiment import gather_sentiment

EXTRACT_RULES = (
    "Use ONLY the text provided. Every number must cite the page it appears on and an exact quote "
    "from that page. If a value is not present, return null with not_disclosed_reason "
    "'not disclosed in provided pages'. Never calculate; only extract."
)


class AnalysisState(TypedDict, total=False):
    ipo: Ipo
    rhp_pdf: str
    doc: SectionedDocument
    offer: OfferObjects
    sentiment: Sentiment


def _pages_text(doc: SectionedDocument) -> str:
    chunks = sorted((c for s in doc.sections.values() for c in s), key=lambda c: c.page_start)
    return "\n\n".join(c.text for c in chunks)  # chunk text already tags each page "[page N]"


def build_graph(adapters: Adapters, config: Config):
    def ingest_node(state: AnalysisState):
        return {"doc": ingest(Path(state["rhp_pdf"]))}

    def offer_node(state: AnalysisState):
        result = adapters.llm.invoke_structured(
            system=f"You extract the Offer & Objects section of an Indian IPO RHP. {EXTRACT_RULES}",
            user=_pages_text(state["doc"]),
            schema=OfferObjects,
            max_tokens=config.default_max_tokens,
            task="offer_objects",
        )
        return {"offer": result.parsed}

    def sentiment_node(state: AnalysisState):
        return {"sentiment": gather_sentiment(state["ipo"], adapters, config)}

    g = StateGraph(AnalysisState)
    g.add_node("ingest", ingest_node)
    g.add_node("offer_objects", offer_node)
    g.add_node("sentiment", sentiment_node)
    g.add_edge(START, "ingest")
    # Sentiment runs alongside the section agents; both join at END.
    g.add_edge("ingest", "offer_objects")
    g.add_edge("ingest", "sentiment")
    g.add_edge("offer_objects", END)
    g.add_edge("sentiment", END)
    return g.compile()


def analyse_rhp(ipo: Ipo, rhp_pdf: Path, adapters: Adapters, config: Config) -> Report:
    """Raises BudgetExceeded if a call would push this IPO's spend over the cap."""
    # Every agent call goes through the IPO's meter; spend already logged for it carries over.
    meter = BudgetMeter(ipo.ipo_id, config)
    meter.estimate_usd = estimate_run_usd(rhp_pdf, config)  # a large RHP starts degraded
    adapters = dataclasses.replace(adapters, llm=MeteredLLM(adapters.llm, meter))
    state = build_graph(adapters, config).invoke({"ipo": ipo, "rhp_pdf": str(rhp_pdf)})
    return Report(
        ipo=ipo,
        status="complete",
        verdict=None,  # set by the synthesizer (ticket 20)
        offer=state["offer"],
        sentiment=state.get("sentiment"),
        rhp_sha256=state["doc"].sha256,
    )
