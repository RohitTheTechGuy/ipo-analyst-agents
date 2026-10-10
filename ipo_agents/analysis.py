"""Per-IPO analysis graph (spec seam B)."""
from __future__ import annotations

import dataclasses
import operator
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from ipo_agents.adapters import Adapters
from ipo_agents.budget import BudgetMeter, MeteredLLM
from ipo_agents.config import Config
from ipo_agents.contracts import Ipo, Report, SectionedDocument, Sentiment
from ipo_agents.ingestion import ingest
from ipo_agents.router import estimate_run_usd
from ipo_agents.section_agent import SectionSpec, run_section
from ipo_agents.sections import SECTIONS
from ipo_agents.sentiment import gather_sentiment


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class AnalysisState(TypedDict, total=False):
    ipo: Ipo
    rhp_pdf: str
    doc: SectionedDocument
    findings: Annotated[dict[str, BaseModel], _merge]  # section group -> verified output
    unverified: Annotated[list[str], operator.add]  # dotted paths of facts that failed verification
    sentiment: Sentiment


def build_graph(adapters: Adapters, config: Config):
    def ingest_node(state: AnalysisState):
        return {"doc": ingest(Path(state["rhp_pdf"]))}

    def section_node(spec: SectionSpec):
        def node(state: AnalysisState):
            chunks = state["doc"].sections.get(spec.group, [])
            if not chunks:
                return {}
            output, failed = run_section(spec, chunks, adapters.llm, config.default_max_tokens)
            return {"findings": {spec.group: output} if output is not None else {}, "unverified": failed}
        return node

    def sentiment_node(state: AnalysisState):
        return {"sentiment": gather_sentiment(state["ipo"], adapters, config)}

    g = StateGraph(AnalysisState)
    g.add_node("ingest", ingest_node)
    g.add_node("sentiment", sentiment_node)
    g.add_edge(START, "ingest")
    # Sentiment runs alongside the section agents; all join at END.
    for group, spec in SECTIONS.items():
        g.add_node(group, section_node(spec))
        g.add_edge("ingest", group)
        g.add_edge(group, END)
    g.add_edge("ingest", "sentiment")
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
        sentiment=state.get("sentiment"),
        unverified_fields=sorted(state.get("unverified", [])),
        **{spec.report_field: state.get("findings", {}).get(group) for group, spec in SECTIONS.items()},
        rhp_sha256=state["doc"].sha256,
    )
