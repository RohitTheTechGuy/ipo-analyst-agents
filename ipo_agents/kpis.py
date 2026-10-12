"""The RHP's KPI tables, read for peer valuation: the company's own KPIs and its listed peers' (SEBI makes
every RHP compare its KPIs with listed peers in the Basis for Offer Price). Plain Python, no model.

The model names a KPI; this module finds it, takes its latest full fiscal year (a stub period is never
used) and turns its unit into an amount: ₹ crore for a rupee KPI (GMV, AUM), a plain count for anything
counted (subscribers, beds, MW, employees). A KPI that is a percentage, a ratio or a number of days is
not an amount and can't be divided by. Nothing missing is ever filled in.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from ipo_agents.calc import to_crore
from ipo_agents.contracts import CalcInput, Derived, Fact, OfferPriceBasis, OperatingMetric, PeriodFact
from ipo_agents.peers import name_key

_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
# An interim column: "Three months ended June 30, 2026", "Fiscal 2027 June 30th", "half year". Indian
# fiscal years end in March (some in December), never June or September.
_STUB = re.compile(r"month|quarter|half|\bjune\b|\bsept", re.I)
_FOOTNOTE = re.compile(r"\(\s*\d+\s*\)|[*^#†]+")
_RUPEE = re.compile(r"₹|\b(?:rs|inr|rupees?)\b", re.I)
_NOT_AN_AMOUNT = re.compile(r"%|percent|\bdays?\b|ratio|\btimes\b|×", re.I)
_COUNT_SCALES = {"crore": 10_000_000, "billion": 1_000_000_000, "million": 1_000_000, "mn": 1_000_000,
                 "lakh": 100_000, "thousand": 1_000, "'000": 1_000}
MIN_PREFIX = 4  # "Eclerx" matches "eClerx Services Limited"; a shorter key matches nothing by prefix


class Missing(Exception):
    """A number a computation needs is not there; the message says which and why."""


def kpi_key(name: str) -> str:
    """'PAT Margin (7)' -> 'patmargin': a KPI name without footnote markers, as name_key compares names."""
    return name_key(_FOOTNOTE.sub(" ", name))


def display_name(name: str) -> str:
    """'No. of employees (20)' -> 'No. of employees': the KPI name without its footnote marker."""
    return " ".join(_FOOTNOTE.sub(" ", name).split())


def _same_company(a: str, b: str) -> bool:
    ka, kb = name_key(a), name_key(b)
    short, long_ = sorted((ka, kb), key=len)
    return ka == kb or (len(short) >= MIN_PREFIX and long_.startswith(short))


def latest_full_year(values: list[PeriodFact]) -> PeriodFact | None:
    """The verified value of the latest full fiscal year; None if there is none."""
    full = [v for v in values if v.value.value is not None and not _STUB.search(v.period)]
    dated = [(int(y[-1]), v) for v in full if (y := _YEAR.findall(v.period))]
    if dated:
        return max(dated, key=lambda yv: yv[0])[1]
    return full[0] if len(full) == 1 else None  # undated columns can't be ordered


@dataclass
class KpiValue:
    period: str
    fact: Fact
    input: CalcInput

    def amount(self) -> tuple[float, bool]:
        """(value, is_rupees): ₹ crore for a rupee KPI, else a count scaled by its unit ('million users')."""
        unit = self.fact.unit or ""
        if _RUPEE.search(unit):
            try:
                return to_crore(self.fact), True
            except ValueError as exc:
                raise Missing(f"{self.input.field}: {exc}") from None
        if _NOT_AN_AMOUNT.search(unit):
            raise Missing(f"{self.input.field}: '{unit}' is a rate, not an amount to divide by")
        scale = next((n for word, n in _COUNT_SCALES.items() if word in unit.casefold()), 1)
        return self.fact.value * scale, False


class KpiBook:
    """The company's KPIs and its peers' from the RHP's KPI tables, looked up by name."""

    def __init__(self, basis: OfferPriceBasis | None):
        self._ipo = [(i, k) for i, k in enumerate(basis.kpis)] if basis else []
        self._peers = [(i, k) for i, k in enumerate(basis.peer_kpis or [])] if basis else []

    def ipo_names(self) -> list[str]:
        return [k.name for _, k in self._ipo if latest_full_year(k.values)]

    def peer_names(self) -> dict[str, int]:
        """KPI name -> how many peers disclose it, most widely disclosed first."""
        companies: dict[str, tuple[str, set[str]]] = {}
        for _, k in self._peers:
            if latest_full_year(k.values):
                name, seen = companies.setdefault(kpi_key(k.kpi), (k.kpi, set()))
                seen.add(name_key(k.company))
        return dict(sorted(((n, len(s)) for n, s in companies.values()), key=lambda ns: -ns[1]))

    def knows(self, kpi: str) -> bool:
        key = kpi_key(kpi)
        return any(kpi_key(k.name) == key for _, k in self._ipo) or any(kpi_key(k.kpi) == key for _, k in self._peers)

    def ipo(self, kpi: str) -> KpiValue:
        for i, k in self._ipo:
            if kpi_key(k.name) == kpi_key(kpi):
                return self._value(k.values, f"price_basis.kpis[{i}]", f"KPI '{kpi}' for the company")
        raise Missing(f"KPI '{kpi}' is not among the company's KPIs in the RHP")

    def peer(self, company: str, kpi: str) -> KpiValue:
        for i, k in self._peers:
            if kpi_key(k.kpi) == kpi_key(kpi) and _same_company(k.company, company):
                return self._value(k.values, f"price_basis.peer_kpis[{i}]", f"KPI '{kpi}' for {company}")
        raise Missing(f"KPI '{kpi}' is not disclosed for {company} in the RHP's peer KPI table")

    @staticmethod
    def _value(values: list[PeriodFact], path: str, what: str) -> KpiValue:
        pf = latest_full_year(values)
        if pf is None:
            raise Missing(f"{what}: no verified full fiscal year value")
        return KpiValue(period=pf.period, fact=pf.value, input=CalcInput(field=f"{path}.{pf.period}", page=pf.value.page))


def per_kpi(numerator: float, inputs: list[CalcInput], kpi: KpiValue, what: str) -> Derived:
    """Market cap or EV (₹ crore) per unit of the KPI: × for a rupee KPI, ₹ per unit for a counted one."""
    try:
        amount, rupees = kpi.amount()
    except Missing as exc:
        return Derived(value=None, unit="×", inputs=inputs, not_computable_reason=str(exc))
    inputs = inputs + [kpi.input]
    unit = "×" if rupees else "₹"
    if amount <= 0:
        return Derived(value=None, unit=unit, inputs=inputs,
                       not_computable_reason=f"{what}: the KPI is zero or negative ({kpi.period})")
    value = numerator / amount if rupees else numerator * 10_000_000 / amount
    return Derived(value=value, unit=unit, inputs=inputs, basis=f"{what} / {kpi.period} {kpi.fact.unit}".strip())


def operating_metric(book: KpiBook, name: str, peers: list[str]) -> OperatingMetric | None:
    """The KPI for the company and each peer, and the peers' median in the company's unit (else the first
    peer's); None when no peer has it (nothing to compare with)."""
    try:
        ipo = book.ipo(name)
    except Missing:
        ipo = None
    found = {}
    for p in peers:
        try:
            found[p] = book.peer(p, name)
        except Missing:
            continue
    if not found:
        return None
    unit = (ipo.fact.unit if ipo else next(iter(found.values())).fact.unit) or ""
    rupees = bool(_RUPEE.search(unit))

    def comparable(v: KpiValue) -> float | None:
        """The value in the median's unit: ₹ crore for rupee KPIs (whatever their scale), else the same unit."""
        if rupees:
            try:
                return to_crore(v.fact) if _RUPEE.search(v.fact.unit or "") else None
            except ValueError:
                return None
        return v.fact.value if (v.fact.unit or "").casefold().strip() == unit.casefold().strip() else None

    same = [(p, v, x) for p, v in found.items() if (x := comparable(v)) is not None]
    out_unit = "₹ crore" if rupees else unit
    if same:
        median = Derived(value=statistics.median(x for _, _, x in same), unit=out_unit,
                         inputs=[v.input for _, v, _ in same],
                         basis=f"median of {len(same)} peers ({', '.join(p for p, _, _ in same)})")
    else:
        median = Derived(value=None, unit=out_unit, not_computable_reason="no peer discloses it in the company's unit")
    return OperatingMetric(name=display_name(name), ipo=ipo.fact if ipo else None, ipo_period=ipo.period if ipo else None,
                           peers={p: v.fact for p, v in found.items()}, median=median)
