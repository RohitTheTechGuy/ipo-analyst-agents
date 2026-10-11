"""Fine Print & Legal checks (plan 5.2), computed in Python from verified facts only. Pure functions.

Like fin_calc, a check reads only facts that survived verification; if an input it needs is unverified
it is "not_computable", never guessed. Net worth and revenue come from Financials' latest full fiscal
year. Each raised check is also a warning Flag, so it shows in the summary.
"""
from __future__ import annotations

import re

from ipo_agents.contracts import FinancialCheck, FinancialPeriod, Financials, FinePrintLegal, Flag
from ipo_agents.fin_calc import full_years

CONTINGENT_MAX_PCT = 25.0  # of net worth
RPT_MAX_PCT = 15.0  # related-party sales or purchases, of revenue
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def legal_checks(legal: FinePrintLegal, fin: Financials | None) -> tuple[list[FinancialCheck], list[Flag]]:
    """(every check, a warning Flag per raised check)."""
    latest = _latest_year(fin)
    results = [
        contingent_vs_net_worth(legal, latest),
        rpt_vs_revenue(legal, latest),
        loans_to_promoter_entities(legal),
        criminal_cases_against_promoters(legal),
        critical_approvals_pending(legal),
    ]
    flags = [Flag(code=check.code.upper(), severity="warning", message=check.detail, evidence_pages=sorted(pages))
             for check, pages in results if check.status == "raised"]
    return [check for check, _ in results], flags


Result = tuple[FinancialCheck, set[int]]  # the check and the pages its inputs are printed on


def contingent_vs_net_worth(legal: FinePrintLegal, latest: FinancialPeriod | None) -> Result:
    code = "contingent_liabilities_vs_net_worth"
    cl = legal.contingent_liabilities
    if cl.value is None:
        return _not_computable(code, "contingent liabilities not verified")
    if latest is None or latest.net_worth.value is None:
        return _not_computable(code, "net worth of the latest full fiscal year not verified")
    nw = latest.net_worth.value
    if nw <= 0:
        return _not_computable(code, f"{latest.label}: net worth is zero or negative")
    pct = cl.value / nw * 100
    detail = (f"contingent liabilities {cl.value:,.2f} are {pct:.1f}% of {latest.label} net worth {nw:,.2f} "
              f"(over {CONTINGENT_MAX_PCT:g}% is a flag)")
    return _status(code, pct > CONTINGENT_MAX_PCT, detail), {cl.page, latest.net_worth.page}


def rpt_vs_revenue(legal: FinePrintLegal, latest: FinancialPeriod | None) -> Result:
    """Related-party sales and purchases of the latest full fiscal year (lines summed per category)
    against that year's revenue from operations."""
    code = "rpt_vs_revenue"
    if latest is None or latest.revenue_from_operations.value is None:
        return _not_computable(code, "revenue of the latest full fiscal year not verified")
    revenue = latest.revenue_from_operations.value
    if revenue <= 0:
        return _not_computable(code, f"{latest.label}: revenue is zero or negative")
    year = _year(latest)
    lines = [l for l in legal.related_party if l.category in ("sales", "purchases") and year in _YEAR.findall(l.period)]
    if any(l.amount.value is None for l in lines):
        return _not_computable(code, f"a related-party sale or purchase of {latest.label} is not verified")
    pcts = {cat: sum(l.amount.value for l in lines if l.category == cat) / revenue * 100
            for cat in ("sales", "purchases")}
    detail = (f"{latest.label}: related-party sales {pcts['sales']:.1f}%, purchases {pcts['purchases']:.1f}% of "
              f"revenue {revenue:,.2f} (over {RPT_MAX_PCT:g}% is a flag)")
    pages = {l.amount.page for l in lines} | {latest.revenue_from_operations.page}
    return _status(code, max(pcts.values()) > RPT_MAX_PCT, detail), pages


def loans_to_promoter_entities(legal: FinePrintLegal) -> Result:
    code = "loans_to_promoter_entities"
    loans = [l for l in legal.related_party if l.category == "loans" and l.promoter_entity]
    given = [l for l in loans if l.amount.value is not None and l.amount.value > 0]
    if given:
        detail = "; ".join(f"{l.counterparty} {l.amount.value:,.2f} {l.amount.unit} ({l.period})" for l in given)
        return _status(code, True, f"loans to promoter entities: {detail}"), {l.amount.page for l in given}
    if any(l.amount.value is None for l in loans):
        return _not_computable(code, "a loan to a promoter entity is not verified")
    return _status(code, False, "no loan to a promoter entity found"), set()


def criminal_cases_against_promoters(legal: FinePrintLegal) -> Result:
    code = "criminal_cases_against_promoters"
    lines = [l for l in legal.litigation if l.party == "promoters" and l.case_type == "criminal"
             and l.direction == "against"]
    cases = [l for l in lines if l.count.value is not None and l.count.value > 0]
    if cases:
        n = sum(l.count.value for l in cases)
        return _status(code, True, f"{n:g} criminal case(s) against the promoters"), {l.count.page for l in cases}
    if any(l.count.value is None for l in lines):
        return _not_computable(code, "the count of criminal cases against the promoters is not verified")
    return _status(code, False, "no criminal case against the promoters found"), set()


def critical_approvals_pending(legal: FinePrintLegal) -> Result:
    code = "critical_approvals_pending"
    critical = [a for a in legal.approvals if a.critical]
    verified = [a for a in critical if a.source.page is not None]
    if verified:
        detail = "; ".join(f"{a.name} ({a.status}, page {a.source.page})" for a in verified)
        return _status(code, True, f"critical approvals pending or expired: {detail}"), {a.source.page for a in verified}
    if critical:
        return _not_computable(code, "critical approvals reported but none verified on their page")
    return _status(code, False, "no critical approval pending or expired found"), set()


def _latest_year(fin: Financials | None) -> FinancialPeriod | None:
    years = full_years(fin.periods) if fin else None
    return years[-1] if years else None


def _year(p: FinancialPeriod) -> str:
    years = _YEAR.findall(p.end_date) or _YEAR.findall(p.label)
    return years[-1]  # full_years only returns periods with a readable year


def _status(code: str, raised: bool, detail: str) -> FinancialCheck:
    return FinancialCheck(code=code, status="raised" if raised else "clear", detail=detail)


def _not_computable(code: str, why: str) -> Result:
    return FinancialCheck(code=code, status="not_computable", detail=f"not computable: {why}"), set()
