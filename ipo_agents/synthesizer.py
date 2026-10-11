"""Synthesizer (plan 5.6, spec stories 37, 40, 41): the verdict, from a digest of verified facts only.

The model sees a compact JSON digest, never raw RHP text and never an unverified value. Every digest item
has a stable evidence ID:

  E:ipo                       exchange data (name, band, lot, dates)
  F:<group>.<path>            a verified Fact, e.g. F:offer_objects.issue_size, F:financials.periods[0].pat
  Q:<group>.<path>            a verified Quote (text checked on its page), e.g. Q:risk_factors.risks[3].title
  N:<group>                   which fields of a section were not disclosed / could not be verified (no values)
  D:<name>                    a derived calculation with its basis, e.g. D:ofs_pct
  G:<CODE>                    a red flag, e.g. G:OFS_HEAVY (a repeated code gets #2, #3...)
  C:<code>                    a Financials or Fine Print check, e.g. C:pat_positive_cfo_negative
  X:<code>                    a cross-section check, e.g. X:debt_repayment_vs_borrowings
  V:read                      the peer valuation read
  S:tally, S:gmp, S:subscription   sentiment tally; GMP and subscription (third-party, unofficial)

The model makes one call. Python then keeps only the points whose IDs are all in the digest and whose
numbers all appear in the items they cite, and applies the GMP rule (GMP never the sole basis).
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel

from ipo_agents.contracts import (CheckedSynthesis, ClaimedFact, EvidencedPoint, Fact, Quote, Report, Synthesis)
from ipo_agents.cross_checks import corroborated_risks
from ipo_agents.llm import LLM
from ipo_agents.sections import SECTIONS
from ipo_agents.verifier import COULD_NOT_VERIFY

log = logging.getLogger(__name__)

TASK = "synthesizer"
MAX_TOKENS = 2_000
MAX_REASONS = MAX_RED_FLAGS = MAX_POSITIVES = 5
MIN_REASONS = 2  # fewer surviving reasons than this: confidence is low
MAX_QUOTE_CHARS = 200
MARKET_ONLY = {"S:gmp", "S:subscription"}

SYSTEM = (
    "You give the final call on an Indian mainboard IPO. Your only input is a JSON digest of verified facts "
    "from the red herring prospectus (RHP), numbers computed from them, red flags and checks, the peer "
    "valuation read, and third-party sentiment. Each item has an ID.\n"
    "Rules:\n"
    "- Extract, don't invent. Use only the items in the digest; inventing a number or a fact is a failure.\n"
    "- Every reason, red flag and positive lists in `evidence` the IDs of the items it rests on.\n"
    "- Never compute. Every number is already in the digest: copy it exactly as it appears in the items you "
    "cite. Do not add, convert, round or annualise.\n"
    "- GMP (S:gmp) and subscription status (S:subscription) are unofficial market data. They are never the "
    "sole basis for a verdict: a subscribe verdict needs reasons resting on RHP facts.\n"
    "- 'not disclosed' and 'could not verify' (N: items) are facts in themselves: opacity about something "
    "material is a red flag.\n"
    "- verdict: subscribe_long_term (sound business at a fair price), subscribe_listing_gains (weak long-term "
    "case but likely demand), neutral, or avoid. confidence: low, medium or high, by how much verified "
    "evidence supports it.\n"
    "- Give 3-5 reasons that drove the verdict, up to 5 red flags ranked most serious first, up to 5 "
    "positives, and one short paragraph per section key in the digest. Write plain sentences; don't put "
    "the IDs in the text."
)

_ID = re.compile(r"\b[EFQNDGCXSV]:[\w.\[\]#-]+")
_NUMBER = re.compile(r"(?<![A-Za-z\d,])\d[\d,]*(?:\.\d+)?")


# --- the digest ------------------------------------------------------------------------------

def build_digest(report: Report) -> dict[str, dict]:
    """Evidence ID -> item. Verified values only: a fact that failed verification appears only by name."""
    items: dict[str, dict] = {}
    ipo = report.ipo
    items["E:ipo"] = {"name": ipo.name, "price_band": ipo.price_band, "lot_size": ipo.lot_size,
                      "open": ipo.open_date.isoformat(), "close": ipo.close_date.isoformat()}
    unverified = set(report.unverified_fields)
    for group, spec in SECTIONS.items():
        section = getattr(report, spec.report_field)
        if section is None:
            continue
        if group == "risk_factors":
            _risks(report, items)
        else:
            _walk(section, group, {}, items)
        missing = _missing(section, group, unverified)
        if missing:
            items[f"N:{group}"] = missing
    for name, d in report.calculations.items():
        if d.value is not None:
            items[f"D:{name}"] = {"value": d.value, "unit": d.unit, "basis": d.basis, "pages": d.pages}
    checks = {c.code for c in report.financial_flags + report.legal_checks + report.cross_checks}
    for c in report.financial_flags + report.legal_checks:
        items[f"C:{c.code}"] = {"status": c.status, "detail": c.detail}
    for c in report.cross_checks:
        items[f"X:{c.code}"] = {"status": c.status, "detail": c.detail}
    for f in report.flags:
        if f.code.lower() in checks:
            continue  # already in the digest as its C:/X: check
        key, n = f"G:{f.code}", 2
        while key in items:
            key, n = f"G:{f.code}#{n}", n + 1
        items[key] = {"severity": f.severity, "message": f.message, "pages": f.evidence_pages}
    if report.valuation and report.valuation.read:
        items["V:read"] = {"text": report.valuation.read}
    if s := report.sentiment:
        items["S:tally"] = {"available": s.available, "tally": {k: v for k, v in s.tally.items() if v}}
        if s.gmp and s.gmp.value is not None:
            items["S:gmp"] = {**_claimed(s.gmp), "note": "unofficial grey-market premium; never the sole basis"}
        if s.subscription and s.subscription.total.value is not None:
            sub = s.subscription
            items["S:subscription"] = {name: f.value for name, f in (("total", sub.total), ("qib", sub.qib),
                                                                       ("nii", sub.nii), ("retail", sub.retail))
                                       if f and f.value is not None}
            items["S:subscription"].update(unit="times subscribed", source=sub.total.source_name,
                                           as_of=sub.total.as_of.isoformat())
    return items


def _walk(obj, path: str, context: dict, items: dict[str, dict]) -> None:
    """Adds each verified Fact (F:) and Quote (Q:) under obj, with its list item's scalar fields as context."""
    if isinstance(obj, Fact):
        if obj.value is not None:
            items[f"F:{path}"] = {"value": obj.value, "unit": obj.unit, "page": obj.page, **context}
        return
    if isinstance(obj, Quote):
        if obj.page is not None and obj.verbatim_quote:
            items[f"Q:{path}"] = {"quote": obj.verbatim_quote[:MAX_QUOTE_CHARS], "page": obj.page, **context}
        return
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            _walk(getattr(obj, name), f"{path}.{name}", context, items)
        return
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk(item, f"{path}[{i}]", {**context, **_scalars(item)}, items)


def _scalars(obj) -> dict:
    """A list item's own words (label, purpose, name, period...) that say what its facts are."""
    if not isinstance(obj, BaseModel):
        return {}
    fields = {**{n: getattr(obj, n) for n in type(obj).model_fields},
              **{n: getattr(obj, n) for n in type(obj).model_computed_fields}}
    return {n: v for n, v in fields.items() if isinstance(v, (str, bool, int)) and v not in ("", None)}


def _risks(report: Report, items: dict[str, dict]) -> None:
    """The top company-specific risks only (the full list can run to a hundred)."""
    risks = report.risk_factors.risks
    corroborated = set(corroborated_risks(report))
    for r in report.risk_factors.top_company_specific:
        i = risks.index(r)
        path = f"risk_factors.risks[{i}]"
        _walk(r, path, _scalars(r), items)
        if f"F:{path}.figure" in items:
            items[f"F:{path}.figure"]["corroborated"] = i in corroborated


def _missing(section: BaseModel, group: str, unverified: set[str]) -> dict:
    """Field paths (relative to the section) that were not disclosed or could not be verified."""
    not_disclosed: list[str] = []

    def walk(obj, path):
        if isinstance(obj, Fact):
            if obj.value is None and obj.not_disclosed_reason != COULD_NOT_VERIFY:
                not_disclosed.append(path)
        elif isinstance(obj, BaseModel):
            for name in type(obj).model_fields:
                walk(getattr(obj, name), f"{path}.{name}" if path else name)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, f"{path}[{i}]")

    if group != "risk_factors":  # every unquantified risk would be listed
        walk(section, "")
    failed = sorted(p.removeprefix(f"{group}.") for p in unverified if p.startswith(f"{group}."))
    out = {}
    if not_disclosed:
        out["not_disclosed"] = not_disclosed
    if failed:
        out["could_not_verify"] = failed
    return out


def _claimed(c: ClaimedFact) -> dict:
    return {"value": c.value, "unit": c.unit, "label": c.label, "source": c.source_name,
            "as_of": c.as_of.isoformat()}


# --- the call and the checks -----------------------------------------------------------------

def synthesize(report: Report, llm: LLM) -> CheckedSynthesis | None:
    """One synthesizer call over the digest, then Python's checks. None when nothing was verified."""
    items = build_digest(report)
    if not any(k.startswith(("F:", "Q:")) for k in items):
        log.info("%s: no verified fact; no verdict", report.ipo.ipo_id)
        return None
    digest = json.dumps({"items": [{"id": k, **v} for k, v in items.items()]}, ensure_ascii=False,
                        separators=(",", ":"), default=str)
    result = llm.invoke_structured(task=TASK, system=SYSTEM, user=f"Digest:\n{digest}", schema=Synthesis,
                                   max_tokens=MAX_TOKENS)
    return check_synthesis(result.parsed, items)


def check_synthesis(s: Synthesis, items: dict[str, dict]) -> CheckedSynthesis:
    notes: list[str] = []
    reasons = _keep(s.reasons, items, "reason", notes)[:MAX_REASONS]
    red_flags = _keep(s.top_red_flags, items, "red flag", notes)[:MAX_RED_FLAGS]
    positives = _keep(s.top_positives, items, "positive", notes)[:MAX_POSITIVES]
    everything = set().union(*(_numbers_in(k, v) for k, v in items.items()))
    summaries = []
    for summary in s.section_summaries:
        bad = _unsupported(summary.text, everything)
        if bad:
            notes.append(f"dropped the {summary.section} summary: {', '.join(bad)} not in the digest")
        else:
            summaries.append(summary)
    verdict, confidence = s.verdict, s.confidence
    if verdict.startswith("subscribe") and all(set(r.evidence) <= MARKET_ONLY for r in reasons):
        verdict = "neutral"
        why = ("GMP and subscription are unofficial and can't be the sole basis for a verdict" if reasons
               else "no reason survived the checks")
        notes.append(f"downgraded {s.verdict} to neutral: {why}")
    if len(reasons) < MIN_REASONS and confidence != "low":
        confidence = "low"
        notes.append(f"confidence set to low: {len(reasons)} reason(s) survived the checks")
    for n in notes:
        log.warning("synthesizer: %s", n)
    return CheckedSynthesis(verdict=verdict, confidence=confidence, reasons=reasons, top_red_flags=red_flags,
                            top_positives=positives, section_summaries=summaries, notes=notes)


def _keep(points: list[EvidencedPoint], items: dict[str, dict], kind: str, notes: list[str]) -> list[EvidencedPoint]:
    kept = []
    for p in points:
        unknown = [e for e in p.evidence if e not in items]
        if not p.evidence or unknown:
            notes.append(f"dropped {kind} \"{p.text}\": " +
                         (f"cites {', '.join(unknown)}, not in the digest" if unknown else "cites no evidence"))
            continue
        bad = _unsupported(p.text, set().union(*(_numbers_in(e, items[e]) for e in p.evidence)))
        if bad:
            notes.append(f"dropped {kind} \"{p.text}\": {', '.join(bad)} not in its cited evidence")
            continue
        kept.append(p)
    return kept


def _unsupported(text: str, allowed: set[float]) -> list[str]:
    """The numbers in the text that match none of the allowed values at the precision written."""
    bad = []
    for raw in _NUMBER.findall(_ID.sub(" ", text)):
        number = float(raw.replace(",", ""))
        decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
        if not any(abs(round(a, decimals) - number) < 1e-9 for a in allowed):
            bad.append(raw)
    return bad


def _numbers_in(key: str, value) -> set[float]:
    """Every number an item holds: its values, the numbers in its text (and in its ID, e.g. top10)."""
    found = {abs(float(n.replace(",", ""))) for n in _NUMBER.findall(key)}

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            found.add(abs(float(v)))
        elif isinstance(v, str):
            found.update(float(n.replace(",", "")) for n in _NUMBER.findall(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(value)
    return found
