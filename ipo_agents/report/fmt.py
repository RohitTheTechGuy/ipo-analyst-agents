"""Indian number formatting for the report (plan 5.8): ₹ crore / lakh, lakh-style grouping (1,25,000).

Pure functions over the contracts; they return plain text, and the template adds the page references.
"""
from __future__ import annotations

from ipo_agents.calc import CRORE, to_crore
from ipo_agents.contracts import ClaimedFact, Derived, Fact
from ipo_agents.verifier import COULD_NOT_VERIFY

LAKH = 100_000
_RUPEE_MARKS = ("₹", "rs", "inr", "rupee")
_SCALES = ("crore", "billion", "million", "lakh")


def group_indian(digits: str) -> str:
    """'125000' -> '1,25,000': the last three digits, then groups of two."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *groups, tail])


def indian(value: float, decimals: int = 2) -> str:
    """A number with Indian grouping and at most `decimals` decimals, trailing zeros dropped."""
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    frac = frac.rstrip("0")
    out = group_indian(whole) + (f".{frac}" if frac else "")
    return f"-{out}" if value < 0 and out.strip("0.,") else out


def crore_text(cr: float) -> str:
    """₹ crore as read: '₹500 cr', '₹45.5 lakh' under a crore, plain rupees under a lakh."""
    sign, cr = ("-" if cr < 0 else ""), abs(cr)
    if cr >= 1:
        text = f"₹{indian(cr)} cr"
    elif cr * CRORE >= LAKH:
        text = f"₹{indian(cr * CRORE / LAKH)} lakh"
    else:
        text = f"₹{indian(cr * CRORE)}"
    return sign + text


def _is_rupee(unit: str) -> bool:
    low = unit.casefold()
    return any(mark in low for mark in _RUPEE_MARKS)


def number(value: float, unit: str) -> str:
    """A value with its unit, for numbers already in ₹ crore (Derived, third-party) or with no rupee scale."""
    u = unit.strip()
    low = u.casefold()
    if _is_rupee(low):
        if "crore" in low:
            return crore_text(value)
        if not any(s in low for s in _SCALES):
            # Plain rupees: a per-share figure reads as ₹12.5; an amount of a lakh or more as lakh/crore.
            return crore_text(value / CRORE) if abs(value) >= LAKH else f"₹{indian(value)}"
    if u.startswith("%"):
        return f"{indian(value)}{u}"
    if low.startswith("percentage point"):
        return f"{indian(value)} pp"
    if low in ("times", "x", "×"):
        return f"{indian(value, 1)}×"
    return f"{indian(value)} {u}".strip()


def fact_value(f: Fact) -> str:
    """A verified Fact's value; rupee amounts in ₹ million/billion/lakh are shown in crore (calc.to_crore)."""
    if _is_rupee(f.unit) and any(s in f.unit.casefold() for s in _SCALES):
        try:
            return crore_text(to_crore(f))
        except ValueError:
            pass  # an ambiguous unit: show it as written
    return number(f.value, f.unit)


def missing(f: Fact) -> str:
    return COULD_NOT_VERIFY if f.not_disclosed_reason == COULD_NOT_VERIFY else "not disclosed"


def derived_value(d: Derived) -> str:
    return number(d.value, d.unit)


def claimed_value(c: ClaimedFact) -> str:
    return number(c.value, c.unit)
