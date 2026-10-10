"""Red flags and derived numbers from verified section output, computed in Python (plan 5.1: a model
never does arithmetic). Only verified facts are used: a fact that failed verification is None here.

TODO: merge with calc.py (ticket 09) once both have landed.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from ipo_agents.contracts import (BusinessIndustry, Derived, Fact, Ipo, OfferPriceBasis, PromotersGovernance,
                                  RedFlag)

TOP1_CUSTOMER_LIMIT_PCT = 20.0
TOP10_CUSTOMERS_LIMIT_PCT = 50.0
PROMOTER_PAY_LIMIT_PCT_OF_PAT = 5.0
MAX_AUDITOR_CHANGES = 1  # in the last three years; more is "frequent"
MAX_KMP_CHANGES = 2
OFFER_TO_WACA_LIMIT = 2.0  # upper band more than this multiple of a WACA: insiders bought far cheaper
_PRICE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Rupee scale words in a Fact's unit -> rupees.
_RUPEE_SCALES = {"crore": 1e7, "billion": 1e9, "million": 1e6, "lakh": 1e5, "thousand": 1e3}


def evaluate(ipo: Ipo, findings: dict[str, BaseModel]) -> tuple[list[Derived], list[RedFlag]]:
    """(derived numbers, red flags) for every section that produced output."""
    derived: list[Derived] = []
    flags: list[RedFlag] = []
    business = findings.get("business_industry")
    if isinstance(business, BusinessIndustry):
        flags += business_flags(business)
    governance = findings.get("promoters_governance")
    if isinstance(governance, PromotersGovernance):
        pat = latest_pat(findings)
        pay = promoter_pay_vs_pat(governance, pat)
        derived.append(pay)
        flags += governance_flags(governance, pay, pat)
    price_basis = findings.get("offer_price_basis")
    if isinstance(price_basis, OfferPriceBasis):
        d, f = offer_price_vs_waca(price_basis, upper_band(ipo.price_band))
        derived += d
        flags += f
    return derived, flags


def latest_pat(findings: dict[str, BaseModel]) -> Fact | None:
    """Latest full-year PAT, verified, from the Financials output's `latest_pat` Fact.

    TODO(ticket 11): this is the contract assumed of the Financials schema; align the name on merge."""
    pat = getattr(findings.get("financials"), "latest_pat", None)
    return pat if isinstance(pat, Fact) and pat.value is not None else None


# --- Business & Industry --------------------------------------------------------

def business_flags(b: BusinessIndustry) -> list[RedFlag]:
    group = "business_industry"
    flags = []
    top1, top10 = _pct(b.top1_customer_pct), _pct(b.top10_customers_pct)
    over = []
    if top1 is not None and top1 > TOP1_CUSTOMER_LIMIT_PCT:
        over.append((f"largest customer {top1:g}% of revenue (> {TOP1_CUSTOMER_LIMIT_PCT:g}%)", b.top1_customer_pct))
    if top10 is not None and top10 > TOP10_CUSTOMERS_LIMIT_PCT:
        over.append((f"top 10 customers {top10:g}% of revenue (> {TOP10_CUSTOMERS_LIMIT_PCT:g}%)",
                     b.top10_customers_pct))
    if over:
        flags.append(RedFlag(section=group, code="customer_concentration",
                             message="Customer concentration: " + "; ".join(text for text, _ in over),
                             pages=_pages(*(f for _, f in over))))
    for code, label, dep in (("single_plant_dependence", "Single-plant dependence", b.single_plant),
                             ("single_geography_dependence", "Single-geography dependence", b.single_geography)):
        if dep.dependent:
            evidence = dep.evidence
            flags.append(RedFlag(section=group, code=code,
                                 message=f"{label}: {evidence.text}" if evidence else label,
                                 pages=[evidence.page] if evidence else []))
    return flags


# --- Promoters & Governance -----------------------------------------------------

def promoter_pay_vs_pat(g: PromotersGovernance, pat: Fact | None) -> Derived:
    """The promoters' remuneration for the latest fiscal year, as % of PAT."""
    name, unit = "promoter_pay_pct_of_pat", "% of PAT"
    pays = _promoter_pay(g)
    if not pays:
        return Derived(name=name, value=None, unit=unit, note="not computable: no verified promoter remuneration")
    if pat is None:
        return Derived(name=name, value=None, unit=unit, note="not computable: PAT not available from Financials")
    pay_rs, pat_rs = [_rupees(f) for f in pays], _rupees(pat)
    if None in pay_rs or pat_rs is None:
        return Derived(name=name, value=None, unit=unit, note="not computable: a rupee unit could not be read")
    total = sum(pay_rs)
    if pat_rs <= 0:
        return Derived(name=name, value=None, unit=unit,
                       note=f"not meaningful: promoters paid ₹{total / 1e6:,.2f} million in a loss-making year")
    return Derived(name=name, value=100 * total / pat_rs, unit=unit,
                   note=f"promoter pay ₹{total / 1e6:,.2f} million / PAT ₹{pat_rs / 1e6:,.2f} million")


def governance_flags(g: PromotersGovernance, pay: Derived, pat: Fact | None) -> list[RedFlag]:
    group = "promoters_governance"
    flags = []
    over = pay.value is not None and pay.value > PROMOTER_PAY_LIMIT_PCT_OF_PAT
    if over or pay.note.startswith("not meaningful"):
        detail = f"{pay.value:.1f}% of PAT (> {PROMOTER_PAY_LIMIT_PCT_OF_PAT:g}%); {pay.note}" if over else pay.note
        flags.append(RedFlag(section=group, code="promoter_pay_vs_pat", message=f"Promoter pay vs PAT: {detail}",
                             pages=_pages(*_promoter_pay(g), *([pat] if pat else []))))
    conflicts = [c for c in g.group_companies if c.same_line_of_business]
    if conflicts:
        flags.append(RedFlag(section=group, code="group_company_conflict",
                             message="Group companies in the same line of business: "
                                     + ", ".join(c.name for c in conflicts),
                             pages=sorted({c.page for c in conflicts})))
    pledged = g.promoter_shares_pledged
    if pledged.value is not None and pledged.value > 0:
        flags.append(RedFlag(section=group, code="promoter_pledge",
                             message=f"Promoter shares pledged: {pledged.value:,g} {pledged.unit}",
                             pages=_pages(pledged)))
    for code, label, changes, limit in (
            ("frequent_auditor_changes", "statutory auditor", g.auditor_changes, MAX_AUDITOR_CHANGES),
            ("frequent_kmp_changes", "KMP", g.kmp_changes, MAX_KMP_CHANGES)):
        if len(changes) > limit:
            flags.append(RedFlag(section=group, code=code,
                                 message=f"{len(changes)} {label} changes in the last three years",
                                 pages=sorted({c.page for c in changes})))
    return flags


def _promoter_pay(g: PromotersGovernance) -> list[Fact]:
    return [r.amount for r in g.remuneration if r.is_promoter and r.amount.value is not None]


# --- Offer Price Basis ----------------------------------------------------------

def upper_band(price_band: str) -> float | None:
    """The upper end of the exchange's price band, e.g. "₹100–₹105" -> 105.0; None if no number."""
    prices = [float(n.replace(",", "")) for n in _PRICE.findall(price_band)]
    return max(prices) if prices else None


def offer_price_vs_waca(pb: OfferPriceBasis, upper: float | None) -> tuple[list[Derived], list[RedFlag]]:
    """Upper band as a multiple of each WACA; flags any above OFFER_TO_WACA_LIMIT ("insiders bought far cheaper")."""
    derived, over = [], []
    for kind, w in (("primary", pb.waca_primary.waca), ("secondary", pb.waca_secondary.waca)):
        name, unit = f"cap_price_to_waca_{kind}", "times"
        if upper is None:
            derived.append(Derived(name=name, value=None, unit=unit, note="not computable: no upper price band"))
        elif w.value is None or w.value <= 0:
            derived.append(Derived(name=name, value=None, unit=unit,
                                   note=f"not computable: {kind} WACA not disclosed or not positive"))
        else:
            ratio = upper / w.value
            derived.append(Derived(name=name, value=ratio, unit=unit,
                                   note=f"upper band ₹{upper:g} / {kind} WACA ₹{w.value:g}"))
            if ratio > OFFER_TO_WACA_LIMIT:
                over.append((f"{ratio:.1f}x the {kind} WACA of ₹{w.value:g}", w))
    flags = []
    if over:
        flags.append(RedFlag(section="offer_price_basis", code="offer_price_far_above_waca",
                             message=f"Insiders bought far cheaper: upper band ₹{upper:g} is "
                                     + " and ".join(text for text, _ in over),
                             pages=_pages(*(f for _, f in over))))
    return derived, flags


# --- helpers --------------------------------------------------------------------

def _rupees(f: Fact) -> float | None:
    """A rupee Fact's value in rupees, from the scale word in its unit; None if the unit isn't rupees."""
    unit = f.unit.casefold()
    for word, scale in _RUPEE_SCALES.items():
        if word in unit:
            return f.value * scale
    return f.value if any(m in unit for m in ("₹", "rs", "rupee", "inr")) else None


def _pct(f: Fact) -> float | None:
    """The value of a verified percentage fact, else None."""
    return f.value if f.value is not None and "%" in f.unit else None


def _pages(*facts: Fact) -> list[int]:
    return sorted({f.page for f in facts if f.page is not None})
