"""Per-IPO analysis graph (spec seam B)."""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ipo_agents.adapters import Adapters
from ipo_agents.config import Config
from ipo_agents.contracts import Ipo, OfferObjects, Report, SectionedDocument
from ipo_agents.ingestion import ingest

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


def _pages_text(doc: SectionedDocument) -> str:
    chunks = sorted((c for s in doc.sections.values() for c in s), key=lambda c: c.page_start)
    return "\n\n".join(f"[page {c.page_start}]\n{c.text}" for c in chunks)


def build_graph(adapters: Adapters, config: Config):
    def ingest_node(state: AnalysisState):
        return {"doc": ingest(Path(state["rhp_pdf"]))}

    def offer_node(state: AnalysisState):
        result = adapters.llm.invoke_structured(
            model=config.routing["offer_objects"],
            system=f"You extract the Offer & Objects section of an Indian IPO RHP. {EXTRACT_RULES}",
            user=_pages_text(state["doc"]),
            schema=OfferObjects,
            max_tokens=config.default_max_tokens,
            task="offer_objects",
        )
        return {"offer": result.parsed}

    g = StateGraph(AnalysisState)
    g.add_node("ingest", ingest_node)
    g.add_node("offer_objects", offer_node)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "offer_objects")
    g.add_edge("offer_objects", END)
    return g.compile()


def analyse_rhp(ipo: Ipo, rhp_pdf: Path, adapters: Adapters, config: Config) -> Report:
    state = build_graph(adapters, config).invoke({"ipo": ipo, "rhp_pdf": str(rhp_pdf)})
    return Report(
        ipo=ipo,
        status="complete",
        verdict=None,  # set by the synthesizer (ticket 20)
        offer=state["offer"],
        rhp_sha256=state["doc"].sha256,
    )
