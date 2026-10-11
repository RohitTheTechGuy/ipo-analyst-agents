"""Derived numbers and red flags for Business & Industry, Promoters & Governance and Offer Price Basis
(plan 5.2). Plain Python over verified facts, in the same terms as calc.py / flags.py: each number is a
Derived (with its inputs, or why it is not computable) and each red flag a Flag tagged with its section.

A fact that failed verification has value None, and a Quote that failed has page None; neither can
raise a flag.
"""
from __future__ import annotations

from ipo_agents.calc import to_crore, upper_band
from ipo_agents.contracts import (BusinessIndustry, CalcInput, CitedText, Derived, Fact, Financials, Flag, Ipo,
                                  OfferPriceBasis, PromotersGovernance, Quote)
from ipo_agents.fin_calc import full_years
from ipo_agents.verifier import COULD_NOT_VERIFY

TOP1_CUSTOMER_LIMIT_PCT = 20.0
TOP10_CUSTOMERS_LIMIT_PCT = 50.0
PROMOTER_PAY_LIMIT_PCT_OF_PAT = 5.0
MAX_AUDITOR_CHANGES = 1  # in the last three years; more is "frequent"
MAX_KMP_CHANGES = 2
OFFER_TO_WACA_LIMIT = 2.0  # upper band more than this multiple of a WACA: insiders bought far cheaper

BUSINESS, GOVERNANCE, PRICE_BASIS = "business_industry", "promoters_governance", "offer_price_basis"


def compute_sections(ipo: Ipo, business: BusinessIndustry | None, governance: PromotersGovernance | None,
                     price_basis: OfferPriceBasis | None,
                     financials: Financials | None) -> tuple[dict[str, Derived], list[Flag]]:
    """(name -> Derived, flags) for whichever of these sections produced output."""
    calcs: dict[str, Derived] = {}
    flags: list[Flag] = []
    if business is not None:
        flags += business_flags(business)
    if governance is not None:
        pay = promoter_pay_vs_pat(governance, financials)
        calcs["promoter_pay_pct_of_pat"] = pay
        flags += governance_flags(governance, pay)
    if price_basis is not None:
        waca = offer_price_vs_waca(price_basis, ipo)
        calcs.update(waca)
        flags += waca_flags(waca)
    return calcs, flags


# --- Business & Industry --------------------------------------------------------

def business_flags(b: BusinessIndustry) -> list[Flag]:
    flags = []
    over = []
    top1, top10 = _pct(b.top1_customer_pct), _pct(b.top10_customers_pct)
    if top1 is not None and top1 > TOP1_CUSTOMER_LIMIT_PCT:
        over.append((f"largest customer {top1:g}% of revenue (> {TOP1_CUSTOMER_LIMIT_PCT:g}%)", b.top1_customer_pct))
    if top10 is not None and top10 > TOP10_CUSTOMERS_LIMIT_PCT:
        over.append((f"top 10 customers {top10:g}% of revenue (> {TOP10_CUSTOMERS_LIMIT_PCT:g}%)",
                     b.top10_customers_pct))
    if over:
        flags.append(Flag(code="CUSTOMER_CONCENTRATION", severity="warning", section=BUSINESS,
                          message="Customer concentration: " + "; ".join(text for text, _ in over),
                          evidence_pages=_pages(*(f for _, f in over))))
    for code, label, dep in (("SINGLE_PLANT_DEPENDENCE", "Single-plant dependence", b.single_plant),
                             ("SINGLE_GEOGRAPHY_DEPENDENCE", "Single-geography dependence", b.single_geography)):
        # The RHP must be quoted saying so: a dependence the verifier couldn't find raises nothing.
        if dep.dependent and _verified(dep.evidence):
            flags.append(Flag(code=code, severity="warning", section=BUSINESS,
                              message=f"{label}: \"{dep.evidence.verbatim_quote}\"",
                              evidence_pages=[dep.evidence.page]))
    return flags


# --- Promoters & Governance -----------------------------------------------------

def latest_full_year_pat(financials: Financials | None) -> tuple[Fact, int] | None:
    """The latest full fiscal year's verified PAT and its index in Financials.periods (stubs excluded)."""
    years = full_years(financials.periods) if financials else None
    if not years or years[-1].pat.value is None:
        return None
    latest = years[-1]
    return latest.pat, financials.periods.index(latest)


def promoter_pay_vs_pat(g: PromotersGovernance, financials: Financials | None) -> Derived:
    """The promoters' remuneration for the latest fiscal year, as % of the latest full-year PAT."""
    unit = "% of PAT"
    pays = [(i, r.amount) for i, r in enumerate(g.remuneration) if r.is_promoter]
    verified = [(i, f) for i, f in pays if f.value is not None]
    if not verified:
        why = (f"{GOVERNANCE}.remuneration[{pays[0][0]}].amount: {_why_missing(pays[0][1])}" if pays
               else f"{GOVERNANCE}.remuneration: no promoter remuneration disclosed")
        return Derived(value=None, unit=unit, not_computable_reason=why)
    pat = latest_full_year_pat(financials)
    if pat is None:
        return Derived(value=None, unit=unit,
                       not_computable_reason="financials.periods: no verified full-year PAT")
    pat_fact, j = pat
    inputs = [CalcInput(field=f"{GOVERNANCE}.remuneration[{i}].amount", page=f.page) for i, f in verified]
    inputs.append(CalcInput(field=f"financials.periods[{j}].pat", page=pat_fact.page))
    try:
        pay_cr = sum(to_crore(f) for _, f in verified)
        pat_cr = to_crore(pat_fact)
    except ValueError as exc:
        return Derived(value=None, unit=unit, inputs=inputs, not_computable_reason=str(exc))
    if pat_cr <= 0:
        return Derived(value=None, unit=unit, inputs=inputs,
                       not_computable_reason=f"PAT is a loss (₹{pat_cr:,.2f} crore); promoters were paid "
                                             f"₹{pay_cr:,.2f} crore")
    return Derived(value=100 * pay_cr / pat_cr, unit=unit, inputs=inputs,
                   basis=f"promoter pay ₹{pay_cr:,.2f} crore / PAT ₹{pat_cr:,.2f} crore")


def governance_flags(g: PromotersGovernance, pay: Derived) -> list[Flag]:
    flags = []
    loss = pay.value is None and (pay.not_computable_reason or "").startswith("PAT is a loss")
    if loss or (pay.value is not None and pay.value > PROMOTER_PAY_LIMIT_PCT_OF_PAT):
        detail = (pay.not_computable_reason if loss else
                  f"{pay.value:.1f}% of PAT (> {PROMOTER_PAY_LIMIT_PCT_OF_PAT:g}%): {pay.basis}")
        flags.append(Flag(code="PROMOTER_PAY_VS_PAT", severity="warning", section=GOVERNANCE,
                          message=f"Promoter pay vs PAT: {detail}", evidence_pages=pay.pages))
    conflicts = [c for c in g.group_companies if c.same_line_of_business and _verified(c.source)]
    if conflicts:
        flags.append(Flag(code="GROUP_COMPANY_CONFLICT", severity="warning", section=GOVERNANCE,
                          message="Group companies in the same line of business: "
                                  + ", ".join(c.name for c in conflicts),
                          evidence_pages=sorted({c.source.page for c in conflicts})))
    pledged = g.promoter_shares_pledged
    if pledged.value is not None and pledged.value > 0:
        flags.append(Flag(code="PROMOTER_PLEDGE", severity="warning", section=GOVERNANCE,
                          message=f"Promoter shares pledged: {pledged.value:,g} {pledged.unit}",
                          evidence_pages=_pages(pledged)))
    for code, severity, label, changes, limit in (
            ("FREQUENT_AUDITOR_CHANGES", "warning", "statutory auditor", g.auditor_changes, MAX_AUDITOR_CHANGES),
            ("FREQUENT_KMP_CHANGES", "info", "KMP", g.kmp_changes, MAX_KMP_CHANGES)):
        verified = _verified_texts(changes)
        if len(verified) > limit:
            flags.append(Flag(code=code, severity=severity, section=GOVERNANCE,
                              message=f"{len(verified)} {label} changes in the last three years",
                              evidence_pages=sorted({c.source.page for c in verified})))
    return flags


# --- Offer Price Basis ----------------------------------------------------------

def offer_price_vs_waca(pb: OfferPriceBasis, ipo: Ipo) -> dict[str, Derived]:
    """The exchange's upper band as a multiple of each WACA (primary, secondary)."""
    band = upper_band(ipo.price_band)
    out = {}
    for kind, w in (("primary", pb.waca_primary.waca), ("secondary", pb.waca_secondary.waca)):
        path, unit = f"{PRICE_BASIS}.waca_{kind}.waca", "times"
        if band is None:
            out[f"cap_price_to_waca_{kind}"] = Derived(value=None, unit=unit,
                                                       not_computable_reason="ipo.price_band: no upper band price")
        elif w.value is None:
            out[f"cap_price_to_waca_{kind}"] = Derived(value=None, unit=unit,
                                                       not_computable_reason=f"{path}: {_why_missing(w)}")
        elif w.value <= 0:
            out[f"cap_price_to_waca_{kind}"] = Derived(value=None, unit=unit,
                                                       not_computable_reason=f"{path}: zero or negative")
        else:
            out[f"cap_price_to_waca_{kind}"] = Derived(
                value=band / w.value, unit=unit,
                inputs=[CalcInput(field="ipo.price_band"), CalcInput(field=path, page=w.page)],
                basis=f"upper band ₹{band:,g} / {kind} WACA ₹{w.value:,g}")
    return out


def waca_flags(waca: dict[str, Derived]) -> list[Flag]:
    over = [(kind, d) for kind in ("primary", "secondary")
            if (d := waca[f"cap_price_to_waca_{kind}"]).value is not None and d.value > OFFER_TO_WACA_LIMIT]
    if not over:
        return []
    return [Flag(code="OFFER_PRICE_FAR_ABOVE_WACA", severity="warning", section=PRICE_BASIS,
                 message="Insiders bought far cheaper: " + "; ".join(
                     f"{d.basis} = {d.value:.1f}x (> {OFFER_TO_WACA_LIMIT:g}x)" for _, d in over),
                 evidence_pages=sorted({p for _, d in over for p in d.pages}))]


# --- helpers --------------------------------------------------------------------

def _verified(q: Quote | None) -> bool:
    return q is not None and q.page is not None


def _verified_texts(items: list[CitedText]) -> list[CitedText]:
    return [c for c in items if _verified(c.source)]


def _why_missing(f: Fact) -> str:
    return COULD_NOT_VERIFY if f.not_disclosed_reason == COULD_NOT_VERIFY else "not disclosed"


def _pct(f: Fact) -> float | None:
    """The value of a verified percentage fact, else None."""
    return f.value if f.value is not None and "%" in f.unit else None


def _pages(*facts: Fact) -> list[int]:
    return sorted({f.page for f in facts if f.page is not None})
