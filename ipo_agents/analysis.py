"""Per-IPO analysis graph (spec seam B).

Resume (spec stories 9, 53): the graph is checkpointed in SQLite (checkpoints.db next to db_path), one
thread per IPO and RHP file. A run that raised part-way is resumed by the next analyse_rhp call for the
same IPO and RHP: nodes that finished are not run again, so their model calls aren't repeated.

LangGraph checkpoints per superstep, and the section nodes and sentiment share one superstep. When a
node in it raises, the writes of siblings that had already returned are saved (pending writes) and are
not re-run on resume. Siblings still running at that moment are abandoned: LangGraph stops waiting for
them, so their output is lost and they run again on resume (their spend is logged either way, since the
meter writes llm_calls itself). The node that raised re-runs from its start: progress inside a node
(e.g. a section's map parts) is not checkpointed.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import operator
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from ipo_agents.adapters import Adapters
from ipo_agents.budget import BudgetExceeded, BudgetMeter, MeteredLLM
from ipo_agents.calc import compute_offer
from ipo_agents.config import Config
from ipo_agents.contracts import CheckedSynthesis, Ipo, Report, SectionedDocument, Sentiment, Valuation
from ipo_agents.cross_checks import cross_checks
from ipo_agents.fin_calc import financial_flags
from ipo_agents.flags import offer_flags
from ipo_agents.ingestion import ingest
from ipo_agents.legal_calc import legal_checks
from ipo_agents.report.render import RendererUnavailable, render_pdf
from ipo_agents.router import estimate_run_usd
from ipo_agents.section_agent import SectionSpec, run_section, section_chunks
from ipo_agents.section_calc import compute_sections
from ipo_agents.sections import SECTIONS
from ipo_agents.sentiment import gather_sentiment
from ipo_agents.synthesizer import synthesize
from ipo_agents.valuation import run_peer_valuation, valuation_flags

log = logging.getLogger(__name__)

CHECKPOINT_DB = "checkpoints.db"


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class AnalysisState(TypedDict, total=False):
    ipo: Ipo
    rhp_pdf: str
    doc: SectionedDocument
    findings: Annotated[dict[str, BaseModel], _merge]  # section group -> verified output
    unverified: Annotated[list[str], operator.add]  # dotted paths of facts that failed verification
    sentiment: Sentiment
    valuation: Valuation
    synthesis: CheckedSynthesis | None


def build_graph(adapters: Adapters, config: Config, checkpointer: SqliteSaver | None = None):
    def ingest_node(state: AnalysisState):
        return {"doc": ingest(Path(state["rhp_pdf"]))}

    def section_node(spec: SectionSpec):
        def node(state: AnalysisState):
            chunks = section_chunks(spec, state["doc"])
            if not chunks:
                return {}
            output, failed = run_section(spec, chunks, adapters.llm, spec.max_tokens or config.default_max_tokens)
            return {"findings": {spec.group: output} if output is not None else {}, "unverified": failed}
        return node

    def sentiment_node(state: AnalysisState):
        return {"sentiment": gather_sentiment(state["ipo"], adapters, config)}

    def valuation_node(state: AnalysisState):
        found = {spec.report_field: state.get("findings", {}).get(group) for group, spec in SECTIONS.items()}
        return {"valuation": run_peer_valuation(
            state["ipo"], adapters, offer=found.get("offer"), financials=found.get("financials"),
            price_basis=found.get("price_basis"), business=found.get("business"))}

    def synthesizer_node(state: AnalysisState):
        # A graph node, not a step after the graph: its inputs are checkpointed, so a crash here
        # resumes at the synthesizer without reading any section again.
        report = _report(state["ipo"], state, "complete", state["doc"].sha256)
        return {"synthesis": synthesize(report, adapters.llm)}

    g = StateGraph(AnalysisState)
    g.add_node("ingest", ingest_node)
    g.add_node("sentiment", sentiment_node)
    g.add_node("synthesizer", synthesizer_node)
    g.add_edge(START, "ingest")
    # Sentiment runs alongside the section agents; the synthesizer waits for all of them (and for peer
    # valuation, which itself waits for every section).
    for group, spec in SECTIONS.items():
        g.add_node(group, section_node(spec))
        g.add_edge("ingest", group)
    g.add_edge("ingest", "sentiment")
    if adapters.peers is not None:
        # Peer valuation needs Offer, Financials, Price Basis and Business: it waits for every section.
        g.add_node("peer_valuation", valuation_node)
        g.add_edge(list(SECTIONS), "peer_valuation")
        g.add_edge(["peer_valuation", "sentiment"], "synthesizer")
    else:
        g.add_edge([*SECTIONS, "sentiment"], "synthesizer")
    g.add_edge("synthesizer", END)
    return g.compile(checkpointer=checkpointer)


def _state_types() -> list[tuple[str, str]]:
    """Our Pydantic models, which a checkpoint may hold; LangGraph deserializes only listed types."""
    found, todo = set(), [BaseModel]
    while todo:
        for sub in todo.pop().__subclasses__():
            todo.append(sub)
            if sub.__module__.startswith("ipo_agents."):
                found.add((sub.__module__, sub.__name__))
    return sorted(found)


@contextmanager
def _checkpointer(config: Config) -> Iterator[SqliteSaver]:
    path = Path(config.db_path).with_name(CHECKPOINT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Parallel nodes save their writes from LangGraph's worker threads.
    with closing(sqlite3.connect(path, check_same_thread=False)) as conn:
        yield SqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=_state_types()))


def _report(ipo: Ipo, state: dict, status: str, rhp_sha256: str) -> Report:
    findings = state.get("findings", {})
    synthesis = state.get("synthesis")
    report = Report(
        ipo=ipo,
        status=status,
        verdict=synthesis.verdict if synthesis else None,  # None: the synthesizer did not run or had nothing
        synthesis=synthesis,
        sentiment=state.get("sentiment"),
        valuation=state.get("valuation"),
        unverified_fields=sorted(state.get("unverified", [])),
        **{spec.report_field: findings.get(group) for group, spec in SECTIONS.items()},
        financial_flags=financial_flags(findings["financials"]) if findings.get("financials") else [],
        rhp_sha256=rhp_sha256,
    )
    # Derived numbers and flags are plain Python over the verified facts (calc.py, flags.py, section_calc.py).
    calcs = compute_offer(report.offer, ipo)
    checks, legal_flags = legal_checks(report.fine_print_legal, report.financials) if report.fine_print_legal else ([], [])
    section_calcs, section_flags = compute_sections(ipo, report.business, report.governance, report.price_basis,
                                                    report.financials)
    x_checks, x_flags = cross_checks(report)
    return report.model_copy(update={"calculations": {**calcs, **section_calcs}, "legal_checks": checks,
                                     "cross_checks": x_checks,
                                     "flags": offer_flags(report.offer, calcs) + section_flags + legal_flags
                                              + valuation_flags(report.valuation) + x_flags})


def analyse_rhp(ipo: Ipo, rhp_pdf: Path, adapters: Adapters, config: Config) -> Report:
    """Runs the analysis graph for this IPO and RHP, or resumes it if an earlier run stopped part-way.

    Raises BudgetExceeded if a call would push this IPO's spend over the cap; the exception's `report`
    is then a partial_budget Report of the facts verified before the stop."""
    # Every agent call goes through the IPO's meter; spend already logged for it carries over.
    meter = BudgetMeter(ipo.ipo_id, config)
    meter.estimate_usd = estimate_run_usd(rhp_pdf, config)  # a large RHP starts degraded
    adapters = dataclasses.replace(adapters, llm=MeteredLLM(adapters.llm, meter))
    rhp_sha256 = hashlib.sha256(Path(rhp_pdf).read_bytes()).hexdigest()  # as ingest computes it
    thread_id = f"{ipo.ipo_id}:{rhp_sha256}"
    thread = {"configurable": {"thread_id": thread_id}}
    with _checkpointer(config) as saver:
        graph = build_graph(adapters, config, saver)
        earlier = graph.get_state(thread)
        if earlier.next:
            # An earlier run stopped part-way: carry on from its last checkpoint and saved writes.
            log.info("%s: resuming the analysis at %s", ipo.ipo_id, ", ".join(earlier.next))
            run_input = None
        else:
            if earlier.values:
                # A finished run (forced re-run): start clean, or the reducers would add to its findings.
                saver.delete_thread(thread_id)
            run_input = {"ipo": ipo, "rhp_pdf": str(rhp_pdf)}
        try:
            state = graph.invoke(run_input, thread)
        except BudgetExceeded as exc:
            # The checkpoint's state includes every node that finished, siblings of the stopped one too.
            exc.report = _with_pdf(_report(ipo, graph.get_state(thread).values, "partial_budget", rhp_sha256), config)
            raise
        # A finished run never resumes; drop its checkpoints so they don't pile up (several MB per IPO).
        saver.delete_thread(thread_id)
    return _with_pdf(_report(ipo, state, "complete", rhp_sha256), config)


def _with_pdf(report: Report, config: Config) -> Report:
    """The report with its PDF rendered into work_dir/<ipo_id>/. A renderer failure is logged and the
    report goes out without a PDF: a renderer bug must never lose the analysis."""
    try:
        path = render_pdf(report, Path(config.work_dir) / report.ipo.ipo_id)
    except RendererUnavailable as exc:
        log.warning("%s: no PDF report: %s", report.ipo.ipo_id, exc)
        return report
    except Exception:
        log.exception("%s: rendering the PDF report failed; sending the summary without it", report.ipo.ipo_id)
        return report
    return report.model_copy(update={"pdf_path": str(path)})
