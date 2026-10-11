"""Report renderer (plan 5.8, spec stories 42-45): Report -> HTML (Jinja2) -> PDF (WeasyPrint).

A pure function of the Report: no model is called and nothing is computed that the Report doesn't hold,
except display conversions (₹ million -> crore) and the minimum investment (lot x upper band).

WeasyPrint is imported lazily. On macOS with python.org Python it needs Homebrew's pango, found only if
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib is set before Python starts (the launchd plist sets it, and
cli.reexec_with_renderer_env re-launches a manual run with it).
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel

from ipo_agents.calc import upper_band
from ipo_agents.contracts import MULTIPLE_LABELS, ClaimedFact, Derived, Fact, Flag, Quote, Report, SentimentSource
from ipo_agents.report import charts, fmt
from ipo_agents.report.labels import DISCLAIMER, VERDICT_LABELS
from ipo_agents.verifier import COULD_NOT_VERIFY

TEMPLATES = Path(__file__).resolve().parent / "templates"
ENV_HINT = "set DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib (see scheduler plist)"
TOP_RED_FLAGS = 3
VERDICT_CLASS = {"subscribe_long_term": "subscribe", "subscribe_listing_gains": "listing",
                 "neutral": "neutral", "avoid": "avoid"}
RED = ("warning", "error")


class RendererUnavailable(RuntimeError):
    """WeasyPrint (or the system libraries it loads) can't be imported."""


def _kind(x) -> str:
    if isinstance(x, Fact):
        return "fact"
    if isinstance(x, Derived):
        return "derived"
    if isinstance(x, ClaimedFact):
        return "claimed"
    return "none"


def _derived_ref(d: Derived) -> str:
    """'computed from p. 2, p. 3' plus exchange or third-party inputs, from Derived.inputs."""
    parts = []
    if d.pages:
        parts.append(", ".join(f"p. {p}" for p in d.pages))
    if any(i.field.startswith("ipo.") for i in d.inputs):
        parts.append("exchange price band")
    parts += sorted({f"claimed by {i.source}" for i in d.inputs if i.source})
    return "computed from " + "; ".join(parts) if parts else "computed"


def _safe_url(url: str | None) -> str | None:
    return url if url and re.match(r"https?://", url, re.I) else None


def walk(obj, path: str) -> Iterator[tuple[str, object]]:
    """Every (dotted path, value) under a model, list or dict, depth first."""
    yield path, obj
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            yield from walk(getattr(obj, name), f"{path}.{name}" if path else name)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from walk(item, f"{path}[{i}]")
    elif isinstance(obj, dict):
        for key, item in obj.items():
            yield from walk(item, f"{path}.{key}")


SECTION_FIELDS = ("offer", "financials", "risk_factors", "fine_print_legal", "business", "governance",
                  "price_basis", "other", "sentiment", "valuation")


def unverified(report: Report) -> list[tuple[str, str]]:
    """(field, why) for every RHP fact or quote with no verified value, and every section not reached."""
    found: dict[str, str] = {path: COULD_NOT_VERIFY for path in report.unverified_fields}
    for name in SECTION_FIELDS:
        section = getattr(report, name)
        if section is None:
            found[name] = "not reached" if report.status == "partial_budget" else "nothing disclosed / not reached"
            continue
        for path, value in walk(section, name):
            if isinstance(value, Fact) and value.value is None:
                found.setdefault(path, fmt.missing(value))
            elif isinstance(value, Quote) and value.verbatim_quote is None:
                found.setdefault(path, COULD_NOT_VERIFY)
    return sorted(found.items())


def sources(report: Report) -> list[dict]:
    """Third-party sources with their as-of dates, one row per source, URL and date."""
    rows: dict[tuple, dict] = {}
    for path, value in walk(report, ""):
        if isinstance(value, ClaimedFact):
            key = (value.source_name, value.source_url, value.as_of)
            what = value.label or path.rsplit(".", 1)[-1]
        elif isinstance(value, SentimentSource):
            key = (value.source_name, value.url, value.published)
            what = "article: " + value.title
        else:
            continue
        row = rows.setdefault(key, {"name": key[0], "url": key[1], "safe_url": _safe_url(key[1]), "as_of": key[2],
                                    "what": []})
        if what not in row["what"]:
            row["what"].append(what)
    return sorted(rows.values(), key=lambda r: (r["name"], str(r["as_of"])))


def _red_flags(report: Report) -> list[dict]:
    """Top red flags for page 1: the synthesizer's, else Python's warning/error flags."""
    if report.synthesis and report.synthesis.top_red_flags:
        return [{"text": p.text, "pages": []} for p in report.synthesis.top_red_flags[:TOP_RED_FLAGS]]
    red = sorted((f for f in report.flags if f.severity in RED), key=lambda f: f.severity != "error")
    return [{"text": f.message, "pages": f.evidence_pages} for f in red[:TOP_RED_FLAGS]]


def _tiles(report: Report) -> list[dict]:
    offer, calcs, v, s = report.offer, report.calculations, report.valuation, report.sentiment
    if offer and offer.issue_size.value is not None:
        issue = offer.issue_size
    else:
        issue = calcs.get("issue_size_cr") or (offer.issue_size if offer else None)
    pe = None
    if v:
        pe = next((m for m in v.multiples if m.multiple == "pe"), None) or v.headline
    return [
        {"label": "Issue size", "value": issue},
        {"label": "OFS share of issue", "value": calcs.get("ofs_pct")},
        {"label": f"{MULTIPLE_LABELS[pe.multiple]} at upper band vs peer median" if pe else "P/E vs peer median",
         "multiple": pe},
        {"label": "GMP (unofficial)", "value": s.gmp if s else None},
    ]


def _min_investment(report: Report) -> dict | None:
    band = upper_band(report.ipo.price_band)
    if band is None:
        return None
    return {"text": fmt.number(band * report.ipo.lot_size, "₹"),
            "basis": f"lot {fmt.indian(report.ipo.lot_size)} × upper band ₹{fmt.indian(band)}"}


def _section_flags(report: Report) -> dict[str, list[Flag]]:
    out: dict[str, list[Flag]] = {}
    for f in report.flags:
        out.setdefault(f.section or "", []).append(f)
    return out


def _environment() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True, undefined=StrictUndefined,
                      trim_blocks=True, lstrip_blocks=True)
    env.filters.update(fval=fmt.fact_value, missing=fmt.missing, dval=fmt.derived_value, cval=fmt.claimed_value,
                       dref=_derived_ref, kind=_kind, indian=fmt.indian, safe_url=_safe_url, crore=fmt.crore_text)
    return env


def render_html(report: Report) -> str:
    """The report as one self-contained HTML page (CSS and charts inline)."""
    synthesis = report.synthesis
    return _environment().get_template("report.html").render(
        r=report,
        ipo=report.ipo,
        partial=report.status == "partial_budget",
        css=(TEMPLATES / "report.css").read_text(encoding="utf-8"),
        verdict_label=VERDICT_LABELS[synthesis.verdict] if synthesis else "VERDICT NOT REACHED",
        verdict_class=VERDICT_CLASS[synthesis.verdict] if synthesis else "none",
        red_flags=_red_flags(report),
        tiles=_tiles(report),
        min_investment=_min_investment(report),
        section_flags=_section_flags(report),
        multiple_labels=MULTIPLE_LABELS,
        charts={"trend": charts.financial_trend(report.financials), "cash": charts.pat_vs_cfo(report.financials),
                "peers": charts.peer_multiple(report.valuation)},
        unverified=unverified(report),
        sources=sources(report),
        disclaimer=DISCLAIMER,
    )


def _weasyprint():
    try:
        import weasyprint
    except OSError as exc:  # pango/gobject not found: the library path isn't set
        raise RendererUnavailable(f"WeasyPrint can't load its libraries ({exc}); {ENV_HINT}") from exc
    return weasyprint


def pdf_name(report: Report) -> str:
    key = report.ipo.symbol or report.ipo.ipo_id
    return f"report_{re.sub(r'[^A-Za-z0-9_-]', '_', key)}.pdf"


def render_pdf(report: Report, out_dir: Path) -> Path:
    """Writes report_<symbol>.pdf into out_dir and returns its path. Raises RendererUnavailable when
    WeasyPrint can't be loaded."""
    weasyprint = _weasyprint()  # fail before the (slower) HTML and charts
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / pdf_name(report)
    # No base_url: the page is self-contained, so nothing is fetched while rendering.
    weasyprint.HTML(string=render_html(report)).write_pdf(path)
    return path
