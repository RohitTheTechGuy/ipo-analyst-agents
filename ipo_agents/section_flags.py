"""Red flags and derived numbers from verified section output, computed in Python (plan 5.1: a model
never does arithmetic). Only verified facts are used: a fact that failed verification is None here.

TODO: merge with calc.py (ticket 09) once both have landed.
"""
from __future__ import annotations

from pydantic import BaseModel

from ipo_agents.contracts import BusinessIndustry, Derived, Fact, Ipo, RedFlag

TOP1_CUSTOMER_LIMIT_PCT = 20.0
TOP10_CUSTOMERS_LIMIT_PCT = 50.0


def evaluate(ipo: Ipo, findings: dict[str, BaseModel]) -> tuple[list[Derived], list[RedFlag]]:
    """(derived numbers, red flags) for every section that produced output."""
    derived: list[Derived] = []
    flags: list[RedFlag] = []
    business = findings.get("business_industry")
    if isinstance(business, BusinessIndustry):
        flags += business_flags(business)
    return derived, flags


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


def _pct(f: Fact) -> float | None:
    """The value of a verified percentage fact, else None."""
    return f.value if f.value is not None and "%" in f.unit else None


def _pages(*facts: Fact) -> list[int]:
    return sorted({f.page for f in facts if f.page is not None})
