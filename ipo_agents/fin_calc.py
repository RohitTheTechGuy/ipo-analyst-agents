"""Financials red flags (plan 5.2), computed in Python from verified facts only. Pure functions.

A flag reads only facts that survived verification (value not None); if any input is missing or
unverified the flag is "not_computable", never guessed. Ratios use full fiscal years only: a stub
period is reported as "not comparable to the full year" and never annualised.
"""
from __future__ import annotations

import re

from ipo_agents.contracts import Financials, FinancialPeriod, Flag

DAYS_IN_YEAR = 365
JUMP_MULTIPLE = 2  # latest PAT growth above this multiple of the prior average growth is a jump
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def financial_flags(fin: Financials) -> list[Flag]:
    years, stubs = full_years(fin.periods), [p for p in fin.periods if _is_stub(p)]
    return [
        pat_positive_cfo_negative(years),
        pre_ipo_profit_jump(years),
        receivable_days_rising(years),
        auditor_remarks(fin),
        restatement_adjustments(fin),
        stub_period(stubs),
    ]


def full_years(periods: list[FinancialPeriod]) -> list[FinancialPeriod] | None:
    """Full fiscal years, oldest first, ordered by the year printed in each end date (else label).
    None if any full year has no readable year, or two share one: their order can't be trusted."""
    keyed = []
    for p in periods:
        if _is_stub(p):
            continue
        years = _YEAR.findall(p.end_date) or _YEAR.findall(p.label)
        if not years:
            return None
        keyed.append((int(years[-1]), p))
    if len({y for y, _ in keyed}) != len(keyed):
        return None
    return [p for _, p in sorted(keyed, key=lambda yp: yp[0])]


def receivable_days(receivables: float, revenue: float) -> float:
    return receivables / revenue * DAYS_IN_YEAR


def growth(new: float, old: float) -> float:
    return new / old - 1


def pat_positive_cfo_negative(years: list[FinancialPeriod] | None) -> Flag:
    code = "pat_positive_cfo_negative"
    if not years:
        return _not_computable(code, "no full fiscal year with a readable year")
    latest = years[-1]
    pat, cfo = latest.pat.value, latest.cfo.value
    if pat is None or cfo is None:
        return _not_computable(code, f"{latest.label}: PAT or CFO not verified")
    detail = f"{latest.label}: PAT {pat:,.2f} vs CFO {cfo:,.2f}"
    return Flag(code=code, status="raised" if pat > 0 and cfo < 0 else "clear", detail=detail)


def pre_ipo_profit_jump(years: list[FinancialPeriod] | None) -> Flag:
    """Latest full year's PAT growth vs the mean growth of up to two years before it. With the usual
    3 fiscal years that mean is the one prior year's growth. Growth off a loss or zero is undefined."""
    code = "pre_ipo_profit_jump"
    if not years or len(years) < 3:
        return _not_computable(code, "needs 3 full fiscal years")
    pats = [p.pat.value for p in years[-4:]]
    if any(v is None for v in pats):
        return _not_computable(code, "PAT not verified for every year")
    if any(v <= 0 for v in pats[:-1]):
        return _not_computable(code, "PAT growth is undefined off a loss or zero")
    rates = [growth(new, old) for old, new in zip(pats, pats[1:])]
    latest, prior = rates[-1], rates[:-1]
    baseline = sum(prior) / len(prior)
    detail = f"{years[-1].label}: PAT growth {latest:.1%} vs prior average {baseline:.1%}"
    raised = latest > 0 and latest > JUMP_MULTIPLE * baseline
    return Flag(code=code, status="raised" if raised else "clear", detail=detail)


def receivable_days_rising(years: list[FinancialPeriod] | None) -> Flag:
    """Receivable days (receivables / revenue x 365) of the last two full years: raised when they
    rise, and by more than revenue grew."""
    code = "receivable_days_rising"
    if not years or len(years) < 2:
        return _not_computable(code, "needs 2 full fiscal years")
    prev, latest = years[-2], years[-1]
    values = [prev.trade_receivables.value, prev.revenue_from_operations.value,
              latest.trade_receivables.value, latest.revenue_from_operations.value]
    if any(v is None for v in values):
        return _not_computable(code, "receivables or revenue not verified")
    recv0, rev0, recv1, rev1 = values
    if rev0 <= 0 or rev1 <= 0:
        return _not_computable(code, "revenue is zero or negative")
    days0, days1 = receivable_days(recv0, rev0), receivable_days(recv1, rev1)
    days_growth = growth(days1, days0) if days0 else float("inf")  # from zero receivables
    rev_growth = growth(rev1, rev0)
    detail = (f"receivable days {days0:.1f} ({prev.label}) -> {days1:.1f} ({latest.label}), "
              f"{days_growth:+.1%} vs revenue {rev_growth:+.1%}")
    raised = days1 > days0 and days_growth > rev_growth
    return Flag(code=code, status="raised" if raised else "clear", detail=detail)


def auditor_remarks(fin: Financials) -> Flag:
    code = "auditor_remarks"
    verified = [r for r in fin.auditor_remarks if r.source.page is not None]
    if verified:
        detail = "; ".join(f"{r.kind.replace('_', ' ')} (page {r.source.page}): {r.summary}" for r in verified)
        return Flag(code=code, status="raised", detail=detail)
    if fin.auditor_remarks:
        return _not_computable(code, "auditor remarks reported but none verified on their page")
    return Flag(code=code, status="clear", detail="no qualification or emphasis of matter found")


def restatement_adjustments(fin: Financials) -> Flag:
    code = "restatement_adjustments"
    r = fin.restatement
    if r is None or r.source.page is None:
        return _not_computable(code, "restatement adjustments not stated or not verified")
    status = "raised" if r.adjusted else "clear"
    return Flag(code=code, status=status, detail=f"restatement adjustments {'made' if r.adjusted else 'nil'} (page {r.source.page})")


def stub_period(stubs: list[FinancialPeriod]) -> Flag:
    if not stubs:
        return Flag(code="stub_period", status="clear", detail="no stub period")
    labels = ", ".join(p.label for p in stubs)
    return Flag(code="stub_period", status="raised",
                detail=f"{labels}: not comparable to the full year; not annualised")


def _is_stub(p: FinancialPeriod) -> bool:
    # A column labelled in months is a stub even if the model called it a full year.
    return p.kind == "stub" or "month" in p.label.casefold()


def _not_computable(code: str, why: str) -> Flag:
    return Flag(code=code, status="not_computable", detail=f"not computable: {why}")
