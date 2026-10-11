"""Cross-section consistency checks (plan 5.6, spec story 30), computed in Python from verified facts only.

Like fin_calc and legal_calc, a check reads only facts that survived verification. An input that failed
verification ("could not verify") makes the check "not_computable", never guessed. Rupee amounts are
compared in ₹ crore (calc.to_crore), so a ₹ million table and a ₹ crore table can be checked together.
Each raised check is also a warning Flag.
"""
from __future__ import annotations

import math
import re

from ipo_agents.calc import to_crore
from ipo_agents.contracts import Fact, FinancialCheck, FinancialPeriod, Financials, Flag, Report
from ipo_agents.fin_calc import full_years
from ipo_agents.verifier import COULD_NOT_VERIFY

SECTION = "cross_checks"
LITIGATION_MAX_MULTIPLE = 2.0  # litigation against the company over this multiple of contingent liabilities
KPI_TOLERANCE = 0.01  # a KPI and the financials' same-period value may differ by 1%
RISK_FIGURE_TOLERANCE = 0.005  # a risk's figure matches another fact within 0.5% (rounding in the text)

_DEBT_PURPOSE = re.compile(r"\b(?:re-?pay|pre-?pay)\w*\b.*\b(?:borrowing|debt|loan)s?\b|"
                           r"\b(?:borrowing|debt|loan)s?\b.*\b(?:re-?pay|pre-?pay)\w*", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"), start=1)}
# KPI name -> the Financials metric it shares a name with. A ratio, margin or growth KPI is not the metric.
_KPI_METRICS = (
    (re.compile(r"\bebitda\b"), "ebitda"),
    (re.compile(r"\bpat\b|profit after tax|profit for the (?:year|period)|net profit"), "pat"),
    (re.compile(r"\brevenue\b"), "revenue_from_operations"),
)
_NOT_THE_METRIC = re.compile(r"margin|growth|%|\bper\b|ratio|cagr|\bto\b|days|adjusted", re.IGNORECASE)

Result = tuple[FinancialCheck, set[int]]


def cross_checks(report: Report) -> tuple[list[FinancialCheck], list[Flag]]:
    """(every check, a warning Flag per raised check)."""
    results = [
        debt_repayment_vs_borrowings(report),
        litigation_vs_contingent_liabilities(report),
        kpis_vs_financials(report),
        risk_figures_corroborated(report),
    ]
    flags = [Flag(code=c.code.upper(), severity="warning", message=c.detail, evidence_pages=sorted(pages),
                  section=SECTION) for c, pages in results if c.status == "raised"]
    return [c for c, _ in results], flags


def debt_repayment_vs_borrowings(report: Report) -> Result:
    """Debt repayment named in the objects of the offer can't exceed the borrowings in the latest period."""
    code = "debt_repayment_vs_borrowings"
    if report.offer is None:
        return _not_computable(code, "offer section not read")
    debt = [o for o in report.offer.objects if _DEBT_PURPOSE.search(o.purpose)]
    if not debt:
        return _status(code, False, "no repayment of borrowings among the objects of the offer"), set()
    if any(o.amount.value is None for o in debt):
        return _not_computable(code, "a debt repayment amount in the objects is not verified")
    latest = _latest_period(report.financials)
    if latest is None or latest.total_borrowings.value is None:
        return _not_computable(code, "total borrowings of the latest period not verified")
    try:
        repay = sum(to_crore(o.amount) for o in debt)
        borrowings = to_crore(latest.total_borrowings)
    except ValueError as e:
        return _not_computable(code, str(e))
    detail = (f"debt repayment from the proceeds ₹{repay:,.2f} crore vs total borrowings ₹{borrowings:,.2f} crore "
              f"at {latest.end_date or latest.label}")
    pages = {o.amount.page for o in debt} | {latest.total_borrowings.page}
    return _status(code, repay > borrowings, detail), pages


def litigation_vs_contingent_liabilities(report: Report) -> Result:
    """Amounts claimed against the company should show in contingent liabilities: raised when they are
    more than LITIGATION_MAX_MULTIPLE times the contingent liabilities."""
    code = "litigation_vs_contingent_liabilities"
    legal = report.fine_print_legal
    if legal is None:
        return _not_computable(code, "fine print & legal section not read")
    lines = [l for l in legal.litigation if l.party == "company" and l.direction == "against"]
    if any(_unverified(l.amount) for l in lines):
        return _not_computable(code, "a litigation amount against the company is not verified")
    stated = [l for l in lines if l.amount.value is not None]  # amounts not disclosed are not counted
    if not stated:
        return _status(code, False, "no litigation amount stated against the company"), set()
    cl = legal.contingent_liabilities
    if cl.value is None:
        return _not_computable(code, "contingent liabilities not verified")
    try:
        litigation = sum(to_crore(l.amount) for l in stated)
        contingent = to_crore(cl)
    except ValueError as e:
        return _not_computable(code, str(e))
    detail = (f"litigation against the company ₹{litigation:,.2f} crore vs contingent liabilities "
              f"₹{contingent:,.2f} crore (over {LITIGATION_MAX_MULTIPLE:g}× is a flag)")
    pages = {l.amount.page for l in stated} | {cl.page}
    return _status(code, litigation > LITIGATION_MAX_MULTIPLE * contingent, detail), pages


def kpis_vs_financials(report: Report) -> Result:
    """A KPI named like a Financials metric (revenue, PAT, EBITDA) must match the same period within 1%."""
    code = "kpis_vs_financials"
    kpis = [(k, metric) for k in (report.price_basis.kpis if report.price_basis else [])
            if (metric := _kpi_metric(k.name))]
    if not kpis:
        return _status(code, False, "no KPI shares a name with revenue, PAT or EBITDA"), set()
    if report.financials is None:
        return _not_computable(code, "financials section not read")
    compared, mismatches, pages, skipped = 0, [], set(), False
    for kpi, metric in kpis:
        for pv in kpi.values:
            period = _same_period(pv.period, report.financials.periods)
            if period is None:
                continue
            fin = getattr(period, metric)
            if pv.value.value is None or fin.value is None:
                skipped = skipped or _unverified(pv.value) or _unverified(fin)
                continue
            try:
                a, b = _comparable(pv.value, fin)
            except ValueError:
                continue
            compared += 1
            if not math.isclose(a, b, rel_tol=KPI_TOLERANCE):
                mismatches.append(f"{kpi.name} {pv.period}: KPI {pv.value.value:,.2f} {pv.value.unit} "
                                  f"vs financials {fin.value:,.2f} {fin.unit}")
                pages |= {pv.value.page, fin.page}
    if mismatches:
        return _status(code, True, "KPIs differ from the financials by more than 1%: " + "; ".join(mismatches)), pages
    if skipped and not compared:
        return _not_computable(code, "a KPI or the matching financials value is not verified")
    if not compared:
        return _not_computable(code, "no KPI period matches a financials period")
    return _status(code, False, f"{compared} KPI value(s) match the financials within 1%"), set()


def corroborated_risks(report: Report) -> list[int]:
    """Indexes into report.risk_factors.risks of the quantified risks whose verified figure is also a
    verified fact in Financials or Fine Print litigation (count, amount or contingent liabilities)."""
    if report.risk_factors is None:
        return []
    elsewhere = _other_facts(report)
    found = []
    for i, r in enumerate(report.risk_factors.risks):
        if r.quantified and r.figure is not None and r.figure.value is not None and any(
                _same_number(r.figure, f) for f in elsewhere):
            found.append(i)
    return found


def risk_figures_corroborated(report: Report) -> Result:
    """Marks which quantified risks are corroborated; informational, never raised."""
    code = "risk_figures_corroborated"
    risks = report.risk_factors.risks if report.risk_factors else []
    quantified = [i for i, r in enumerate(risks) if r.quantified and r.figure is not None
                  and r.figure.value is not None]
    if not quantified:
        return _not_computable(code, "no quantified risk with a verified figure")
    yes = set(corroborated_risks(report))
    named = lambda ids: ", ".join(risks[i].topic or f"risk {i + 1}" for i in ids) or "none"  # noqa: E731
    detail = (f"corroborated in Financials or Litigation: {named(sorted(yes))}; "
              f"not found there: {named([i for i in quantified if i not in yes])}")
    return _status(code, False, detail), set()


# --- helpers ---------------------------------------------------------------------------------

def _latest_period(fin: Financials | None) -> FinancialPeriod | None:
    """The period with the latest end date (a stub after the last fiscal year counts)."""
    if not fin or not fin.periods:
        return None
    keyed = [(k, p) for p in fin.periods if (k := _date_key(p.end_date)) is not None]
    if len(keyed) == len(fin.periods):
        return max(keyed, key=lambda kp: kp[0])[1]
    years = full_years(fin.periods)
    return years[-1] if years else None


def _date_key(text: str) -> tuple[int, int] | None:
    years = _YEAR.findall(text)
    month = next((n for m, n in _MONTHS.items() if m in text.casefold()), None)
    return (int(years[-1]), month) if years and month else None


def _kpi_metric(name: str) -> str | None:
    norm = name.casefold()
    if _NOT_THE_METRIC.search(norm):
        return None
    return next((metric for pattern, metric in _KPI_METRICS if pattern.search(norm)), None)


def _same_period(label: str, periods: list[FinancialPeriod]) -> FinancialPeriod | None:
    """The Financials period a KPI's period label names: same year, and a stub only for a stub label."""
    years = _YEAR.findall(label)
    if not years:
        return None
    stub = "month" in label.casefold()
    matches = [p for p in periods
               if years[-1] in (_YEAR.findall(p.end_date) or _YEAR.findall(p.label))
               and (p.kind == "stub" or "month" in p.label.casefold()) == stub]
    return matches[0] if len(matches) == 1 else None


def _comparable(a: Fact, b: Fact) -> tuple[float, float]:
    """Both values in one unit: ₹ crore if both are rupee amounts, else as printed if the units agree."""
    try:
        return to_crore(a), to_crore(b)
    except ValueError:
        if a.unit.strip().casefold() == b.unit.strip().casefold():
            return a.value, b.value
        raise


def _other_facts(report: Report) -> list[Fact]:
    facts: list[Fact] = []
    if report.financials:
        for p in report.financials.periods:
            facts += [getattr(p, name) for name, f in type(p).model_fields.items() if f.annotation is Fact]
    if report.fine_print_legal:
        facts += [f for l in report.fine_print_legal.litigation for f in (l.count, l.amount)]
        facts.append(report.fine_print_legal.contingent_liabilities)
    return [f for f in facts if f.value is not None]


def _same_number(a: Fact, b: Fact) -> bool:
    try:
        x, y = _comparable(a, b)
    except ValueError:
        return False  # different kinds of number (a % and a count) never corroborate each other
    return math.isclose(x, y, rel_tol=RISK_FIGURE_TOLERANCE)


def _unverified(f: Fact) -> bool:
    return f.value is None and f.not_disclosed_reason == COULD_NOT_VERIFY


def _status(code: str, raised: bool, detail: str) -> FinancialCheck:
    return FinancialCheck(code=code, status="raised" if raised else "clear", detail=detail)


def _not_computable(code: str, why: str) -> Result:
    return FinancialCheck(code=code, status="not_computable", detail=f"not computable: {why}"), set()
