"""Calculator (plan 5.1): the only place derived numbers are computed. Plain Python, no model.

Every Derived records the Facts (and pages) it came from. A Derived whose input is "could not verify"
or "not disclosed" has value None and says why, so nothing downstream can flag on it. Rupee amounts
are converted to ₹ crore here; when the RHP leaves a rupee amount as [●], it is computed as
shares x the exchange's upper band price and labelled so.
"""
from __future__ import annotations

import re

from ipo_agents.contracts import CalcInput, Derived, Fact, Ipo, OfferObjects
from ipo_agents.verifier import COULD_NOT_VERIFY

CRORE = 10_000_000  # ₹1 crore = ₹10,000,000
# Rupees in one unit of each Indian/western scale.
_SCALE_RUPEES = {"crore": CRORE, "billion": 1_000_000_000, "million": 1_000_000, "lakh": 100_000}
_RUPEE_MARKS = ("₹", "rs", "inr", "rupee")
_BAND_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
PREFIX = "offer_objects"


class NotComputable(Exception):
    """An input is missing; the message is the reason shown as "not computable"."""


def upper_band(price_band: str) -> float | None:
    """The upper price from an exchange band such as "₹1,865–₹1,960"; None if it has no number."""
    prices = [float(n.replace(",", "")) for n in _BAND_NUMBER.findall(price_band)]
    return max(prices) if prices else None


def to_crore(fact: Fact) -> float:
    """A rupee Fact in ₹ crore. Raises ValueError for a unit that is not a rupee amount."""
    unit = fact.unit.casefold()
    if not any(mark in unit for mark in _RUPEE_MARKS):
        raise ValueError(f"not a rupee unit: {fact.unit!r}")
    scales = [s for s in _SCALE_RUPEES if s in unit]
    if len(scales) > 1:
        raise ValueError(f"ambiguous rupee unit: {fact.unit!r}")
    rupees = fact.value * (_SCALE_RUPEES[scales[0]] if scales else 1)
    return rupees / CRORE


def _count(fact: Fact) -> float:
    """A share count, scaled if the unit is e.g. "million equity shares"."""
    unit = fact.unit.casefold()
    if "share" not in unit:
        raise ValueError(f"not a share count: {fact.unit!r}")
    scale = next((n for s, n in _SCALE_RUPEES.items() if s in unit), 1)
    return fact.value * scale


class _Calc:
    """Holds the offer and band; each method returns (value, inputs, at_upper_band) or raises NotComputable."""

    def __init__(self, offer: OfferObjects, ipo: Ipo):
        self.offer = offer
        self.band = upper_band(ipo.price_band)

    def fact(self, name: str, convert=None):
        f: Fact = getattr(self.offer, name)
        path = f"{PREFIX}.{name}"
        if f.value is None:
            why = COULD_NOT_VERIFY if f.not_disclosed_reason == COULD_NOT_VERIFY else "not disclosed"
            raise NotComputable(f"{path}: {why}")
        try:
            value = convert(f) if convert else f.value
        except ValueError as exc:
            raise NotComputable(f"{path}: {exc}") from None
        return value, [CalcInput(field=path, page=f.page)], False

    def band_input(self):
        if self.band is None or self.band <= 0:  # no band, or a zero band: never a divisor
            raise NotComputable("ipo.price_band: no upper band price")
        return [CalcInput(field="ipo.price_band")]

    @staticmethod
    def positive(value: float, path: str) -> float:
        """A divisor: a verified 0 ("Nil") or a negative makes the ratio not computable."""
        if value <= 0:
            raise NotComputable(f"{path}: zero or negative")
        return value

    @staticmethod
    def first(*attempts):
        """The first attempt that computes; else the last one's reason."""
        for attempt in attempts:
            try:
                return attempt()
            except NotComputable as exc:
                last = exc
        raise last

    # --- share counts ---------------------------------------------------------
    def fresh_shares_from_counts(self):
        pre, pre_in, _ = self.fact("pre_issue_shares", _count)
        post, post_in, _ = self.fact("post_issue_shares", _count)
        return post - pre, post_in + pre_in, False

    def fresh_shares(self):
        def from_rupees():
            cr, inputs, _ = self.fresh_cr_stated()
            band = self.band_input()  # raises before the division if there is no band
            return cr * CRORE / self.band, inputs + band, True
        return self.first(self.fresh_shares_from_counts, from_rupees)

    def post_shares(self):
        def from_pre():
            pre, pre_in, _ = self.fact("pre_issue_shares", _count)
            fresh, fresh_in, upper = self.fresh_shares()
            return pre + fresh, pre_in + fresh_in, upper
        return self.first(lambda: self.fact("post_issue_shares", _count), from_pre)

    # --- rupee amounts (₹ crore) ---------------------------------------------------
    def fresh_cr_stated(self):
        return self.fact("fresh_issue", to_crore)

    def fresh_cr(self):
        def at_band():
            shares, inputs, _ = self.fresh_shares_from_counts()
            band = self.band_input()  # first: a missing band must not reach the arithmetic
            return shares * self.band / CRORE, inputs + band, True
        return self.first(self.fresh_cr_stated, at_band)

    def ofs_cr(self):
        def at_band():
            shares, inputs, _ = self.fact("ofs_shares", _count)
            band = self.band_input()  # first: a missing band must not reach the arithmetic
            return shares * self.band / CRORE, inputs + band, True
        return self.first(lambda: self.fact("ofs_amount", to_crore), at_band)

    def issue_cr(self):
        def from_parts():
            fresh, fresh_in, fresh_up = self.fresh_cr()
            ofs, ofs_in, ofs_up = self.ofs_cr()
            return fresh + ofs, fresh_in + ofs_in, fresh_up or ofs_up
        return self.first(lambda: self.fact("issue_size", to_crore), from_parts)

    def ofs_pct(self):
        def rupees():
            ofs, ofs_in, _ = self.fact("ofs_amount", to_crore)
            issue, issue_in, _ = self.fact("issue_size", to_crore)
            return 100 * ofs / self.positive(issue, f"{PREFIX}.issue_size"), ofs_in + issue_in, False
        return self.first(rupees, lambda: self._share_of_issue(ofs=True))

    def fresh_pct(self):
        def rupees():
            fresh, fresh_in, _ = self.fact("fresh_issue", to_crore)
            issue, issue_in, _ = self.fact("issue_size", to_crore)
            return 100 * fresh / self.positive(issue, f"{PREFIX}.issue_size"), fresh_in + issue_in, False
        return self.first(rupees, lambda: self._share_of_issue(ofs=False))

    def _share_of_issue(self, ofs: bool):
        # Fresh + OFS, both in ₹ crore (stated, or at the upper band), so the split adds to 100%.
        fresh_cr, fresh_in, fresh_up = self.fresh_cr()
        ofs_cr, ofs_in, ofs_up = self.ofs_cr()
        if fresh_cr + ofs_cr <= 0:
            raise NotComputable("offer_objects: fresh issue + OFS is zero")
        part = ofs_cr if ofs else fresh_cr
        return 100 * part / (fresh_cr + ofs_cr), fresh_in + ofs_in, fresh_up or ofs_up

    # --- ratios -------------------------------------------------------------------
    def dilution_pct(self):
        fresh, fresh_in, fresh_up = self.fresh_shares()
        post, post_in, post_up = self.post_shares()
        if post <= 0:
            raise NotComputable("offer_objects.post_issue_shares: zero")
        return 100 * fresh / post, fresh_in + post_in, fresh_up or post_up

    def promoter_change_pp(self):
        pre, pre_in, _ = self.fact("promoter_holding_pre_pct")
        post, post_in, _ = self.fact("promoter_holding_post_pct")
        return post - pre, pre_in + post_in, False

    def market_cap_cr(self):
        """Post-issue shares x the upper band: the IPO's market cap at the top of the band (₹ crore)."""
        band = self.band_input()
        shares, inputs, _ = self.post_shares()
        return shares * self.band / CRORE, inputs + band, True

    def gcp_pct_of_fresh(self):
        gcp, gcp_in, _ = self.fact("gcp_amount", to_crore)
        fresh, fresh_in, upper = self.fresh_cr()
        if fresh <= 0:
            raise NotComputable("offer_objects.fresh_issue: no fresh issue")
        return 100 * gcp / fresh, fresh_in + gcp_in, upper


# name -> (method, unit)
_DERIVED = {
    "issue_size_cr": ("issue_cr", "₹ crore"),
    "fresh_issue_cr": ("fresh_cr", "₹ crore"),
    "ofs_amount_cr": ("ofs_cr", "₹ crore"),
    "ofs_pct": ("ofs_pct", "% of issue"),
    "fresh_pct": ("fresh_pct", "% of issue"),
    "dilution_pct": ("dilution_pct", "% of post-issue shares"),
    "promoter_change_pp": ("promoter_change_pp", "percentage points"),
    "gcp_pct_of_fresh": ("gcp_pct_of_fresh", "% of fresh issue"),
    "market_cap_cr": ("market_cap_cr", "₹ crore"),
}


def compute_offer(offer: OfferObjects | None, ipo: Ipo) -> dict[str, Derived]:
    """Every offer calculation, each with its inputs, or not computable with the reason."""
    if offer is None:
        return {}
    calc = _Calc(offer, ipo)
    out = {}
    for name, (method, unit) in _DERIVED.items():
        try:
            value, inputs, at_band = getattr(calc, method)()
        except NotComputable as exc:
            out[name] = Derived(value=None, unit=unit, not_computable_reason=str(exc))
            continue
        basis = f"computed at upper band ₹{calc.band:,g}" if at_band else "computed from RHP figures"
        out[name] = Derived(value=value, unit=unit, inputs=_dedupe(inputs), basis=basis)
    return out


def _dedupe(inputs: list[CalcInput]) -> list[CalcInput]:
    seen, out = set(), []
    for i in inputs:
        if (i.field, i.page) not in seen:
            seen.add((i.field, i.page))
            out.append(i)
    return out

