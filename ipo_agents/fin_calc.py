"""Financials red flags (plan 5.2), computed in Python from verified facts only. Pure functions.

A flag reads only facts that survived verification (value not None); if any input is missing or
unverified the flag is "not_computable", never guessed. Ratios use full fiscal years only: a stub
period is reported as "not comparable to the full year" and never annualised.
"""
from __future__ import annotations

import re

from ipo_agents.calc import to_crore
from ipo_agents.contracts import CalcInput, Derived, Fact, Financials, FinancialPeriod, FinancialCheck, OfferObjects
from ipo_agents.verifier import COULD_NOT_VERIFY

DAYS_IN_YEAR = 365
JUMP_MULTIPLE = 2  # latest PAT growth above this multiple of the prior average growth is a jump
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
DEBT_REPAYMENT = re.compile(r"repay|prepay|redemption|redeem", re.I)  # an object of the issue that pays down debt


def financial_flags(fin: Financials) -> list[FinancialCheck]:
    years, stubs = full_years(fin.periods), [p for p in fin.periods if _is_stub(p)]
    return [
        pat_positive_cfo_negative(years),
        free_cash_flow(years),
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


def pat_positive_cfo_negative(years: list[FinancialPeriod] | None) -> FinancialCheck:
    code = "pat_positive_cfo_negative"
    if not years:
        return _not_computable(code, "no full fiscal year with a readable year")
    latest = years[-1]
    pat, cfo = latest.pat.value, latest.cfo.value
    if pat is None or cfo is None:
        return _not_computable(code, f"{latest.label}: PAT or CFO not verified")
    detail = f"{latest.label}: PAT {pat:,.2f} vs CFO {cfo:,.2f}"
    return FinancialCheck(code=code, status="raised" if pat > 0 and cfo < 0 else "clear", detail=detail)


def pre_ipo_profit_jump(years: list[FinancialPeriod] | None) -> FinancialCheck:
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
    return FinancialCheck(code=code, status="raised" if raised else "clear", detail=detail)


def receivable_days_rising(years: list[FinancialPeriod] | None) -> FinancialCheck:
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
    return FinancialCheck(code=code, status="raised" if raised else "clear", detail=detail)


def free_cash_flow(years: list[FinancialPeriod] | None) -> FinancialCheck:
    """Free cash flow (CFO − capex) and capex as % of revenue for each full year, oldest first: raised when
    the latest year's FCF is negative. Capex is read as an outflow whatever its printed sign."""
    code = "free_cash_flow"
    if not years:
        return _not_computable(code, "no full fiscal year with a readable year")
    parts = []
    for p in years:
        cfo, capex, rev = p.cfo.value, p.capex.value, p.revenue_from_operations.value
        if cfo is None or capex is None:
            parts.append(f"{p.label}: CFO or capex not verified")
            continue
        intensity = f", capex {abs(capex) / rev:.1%} of revenue" if rev and rev > 0 else ""
        parts.append(f"{p.label}: FCF {cfo - abs(capex):,.2f} {p.cfo.unit} (CFO {cfo:,.2f} − capex {abs(capex):,.2f}{intensity})")
    latest = years[-1]
    if latest.cfo.value is None or latest.capex.value is None:
        return _not_computable(code, f"{latest.label}: CFO or capex not verified")
    raised = latest.cfo.value - abs(latest.capex.value) < 0
    return FinancialCheck(code=code, status="raised" if raised else "clear", detail="; ".join(parts))


class NotDerivable(ValueError):
    """An input of a derived figure is missing or unverified; the message says which."""


def profit_line(fin: Financials, period: FinancialPeriod, kind: str) -> tuple[float, list[CalcInput], str]:
    """EBIT or EBITDA of a period in ₹ crore, with its inputs and basis. EBIT = PBT + finance costs; EBITDA is
    the disclosed figure, else EBIT + depreciation. Never guessed: a missing input raises NotDerivable."""
    i = fin.periods.index(period)

    def crore(field: str) -> tuple[float, CalcInput]:
        f: Fact | None = getattr(period, field)
        path = f"financials.periods[{i}].{field}"
        if f is None or f.value is None:
            why = COULD_NOT_VERIFY if f is not None and f.not_disclosed_reason == COULD_NOT_VERIFY else "not disclosed"
            raise NotDerivable(f"{path}: {why}")
        try:
            return to_crore(f), CalcInput(field=path, page=f.page)
        except ValueError as exc:
            raise NotDerivable(f"{path}: {exc}") from None

    if kind == "ebitda" and period.ebitda.value is not None:
        value, inp = crore("ebitda")
        return value, [inp], "EBITDA as disclosed"
    pbt, pbt_in = crore("pbt")
    fc, fc_in = crore("finance_costs")
    if kind == "ebit":
        return pbt + fc, [pbt_in, fc_in], "EBIT = PBT + finance costs (computed from the RHP)"
    dep, dep_in = crore("depreciation")
    return pbt + fc + dep, [pbt_in, fc_in, dep_in], "EBITDA = PBT + finance costs + depreciation (computed from the RHP)"


def leverage(fin: Financials | None, offer: OfferObjects | None) -> dict[str, Derived]:
    """Net debt / EBITDA for the latest full year, and after the fresh issue repays debt as Objects of the Offer
    says (objects whose purpose names a repayment, prepayment or redemption)."""
    unit = "times"
    years = full_years(fin.periods) if fin else None
    if not years:
        nc = Derived(value=None, unit=unit, not_computable_reason="financials: no full fiscal year with a readable year")
        return {"net_debt_to_ebitda": nc, "net_debt_to_ebitda_post": nc}
    latest, i = years[-1], fin.periods.index(years[-1])

    def amount(field: str) -> tuple[float, CalcInput]:
        f = getattr(latest, field)
        path = f"financials.periods[{i}].{field}"
        if f.value is None:
            raise NotDerivable(f"{path}: {COULD_NOT_VERIFY if f.not_disclosed_reason == COULD_NOT_VERIFY else 'not disclosed'}")
        try:
            return to_crore(f), CalcInput(field=path, page=f.page)
        except ValueError as exc:
            raise NotDerivable(f"{path}: {exc}") from None

    try:
        debt, debt_in = amount("total_borrowings")
        cash, cash_in = amount("cash_and_equivalents")
        ebitda, ebitda_in, how = profit_line(fin, latest, "ebitda")
    except NotDerivable as exc:
        nc = Derived(value=None, unit=unit, not_computable_reason=str(exc))
        return {"net_debt_to_ebitda": nc, "net_debt_to_ebitda_post": nc}
    inputs = [debt_in, cash_in, *ebitda_in]
    if ebitda <= 0:
        nc = Derived(value=None, unit=unit, inputs=inputs,
                     not_computable_reason=f"{latest.label} EBITDA is zero or negative (₹{ebitda:,.2f} crore)")
        return {"net_debt_to_ebitda": nc, "net_debt_to_ebitda_post": nc}
    basis = f"{latest.label}: (borrowings ₹{debt:,.2f} crore − cash ₹{cash:,.2f} crore) / EBITDA ₹{ebitda:,.2f} crore; {how}"
    out = {"net_debt_to_ebitda": Derived(value=(debt - cash) / ebitda, unit=unit, inputs=inputs, basis=basis)}
    repaid, repaid_in = 0.0, []
    for k, o in enumerate(offer.objects if offer else []):
        if DEBT_REPAYMENT.search(o.purpose) and o.amount.value is not None:
            try:
                repaid += to_crore(o.amount)
            except ValueError:
                continue
            repaid_in.append(CalcInput(field=f"offer_objects.objects[{k}].amount", page=o.amount.page))
    if not repaid_in:
        out["net_debt_to_ebitda_post"] = Derived(value=None, unit=unit,
                                                 not_computable_reason="offer_objects: no debt repayment among the objects")
    else:
        out["net_debt_to_ebitda_post"] = Derived(
            value=(debt - repaid - cash) / ebitda, unit=unit, inputs=inputs + repaid_in,
            basis=f"{latest.label}: (borrowings ₹{debt:,.2f} crore − repaid from the issue ₹{repaid:,.2f} crore − cash "
                  f"₹{cash:,.2f} crore) / EBITDA ₹{ebitda:,.2f} crore")
    return out


def auditor_remarks(fin: Financials) -> FinancialCheck:
    code = "auditor_remarks"
    verified = [r for r in fin.auditor_remarks if r.source.page is not None]
    if verified:
        detail = "; ".join(f"{r.kind.replace('_', ' ')} (page {r.source.page}): {r.summary}" for r in verified)
        return FinancialCheck(code=code, status="raised", detail=detail)
    if fin.auditor_remarks:
        return _not_computable(code, "auditor remarks reported but none verified on their page")
    return FinancialCheck(code=code, status="clear", detail="no qualification or emphasis of matter found")


def restatement_adjustments(fin: Financials) -> FinancialCheck:
    code = "restatement_adjustments"
    r = fin.restatement
    if r is None or r.source.page is None:
        return _not_computable(code, "restatement adjustments not stated or not verified")
    status = "raised" if r.adjusted else "clear"
    return FinancialCheck(code=code, status=status, detail=f"restatement adjustments {'made' if r.adjusted else 'nil'} (page {r.source.page})")


def stub_period(stubs: list[FinancialPeriod]) -> FinancialCheck:
    if not stubs:
        return FinancialCheck(code="stub_period", status="clear", detail="no stub period")
    labels = ", ".join(p.label for p in stubs)
    return FinancialCheck(code="stub_period", status="raised",
                detail=f"{labels}: not comparable to the full year; not annualised")


def _is_stub(p: FinancialPeriod) -> bool:
    # A column labelled in months is a stub even if the model called it a full year.
    return p.kind == "stub" or "month" in p.label.casefold()


def _not_computable(code: str, why: str) -> FinancialCheck:
    return FinancialCheck(code=code, status="not_computable", detail=f"not computable: {why}")
