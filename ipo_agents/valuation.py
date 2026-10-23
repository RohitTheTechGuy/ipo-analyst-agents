"""Peer valuation (plan 5.3; spec stories 31-34): is the offer price cheap or dear against listed peers?

One model call (task "peer_valuation") reasons about how this kind of business is valued and picks the
multiples: the standard ones (P/E, EV/EBITDA, P/B, P/S) and business-specific ones it defines on a KPI the
RHP discloses for the company and its peers (EV per subscriber, EV per bed, market cap / AUM, ...; see
kpis.py). It also picks operating KPIs to compare and may suggest up to 3 more peers, each with a reason.
It never outputs a number. Everything numeric is plain Python here:
- the IPO's market cap at the upper band (calc.py: post-issue shares x upper band),
- the IPO's multiples on its latest full fiscal year (a stub period is never annualised), every standard one
  (all_multiples) whether or not the RHP states it: EBIT and, when not disclosed, EBITDA are built from the
  RHP's P&L (fin_calc.profit_line), and EV is post-issue (market cap + debt − cash − the fresh issue's cash),
- each peer's multiples from the peer-data adapter's ClaimedFacts (each input cites source and date),
- median / min / max per multiple, and the IPO's premium or discount to the median.

The median uses the RHP's own peers. Agent-suggested peers are shown separately and only stand in for
the median when the RHP lists no peer with data. A peer the adapter can't price is listed as "data
unavailable" and left out; nothing is filled in.

A second model call (task "peer_fit") checks each peer against the IPO's business, from the RHP's own words
and each peer's Yahoo Finance profile: a verdict (strong / partial / weak), what they share and how they
differ. Words only. A weak peer is flagged (PEER_SET_WEAK), never dropped from the median.
"""
from __future__ import annotations

import logging
import re
import statistics

from ipo_agents.adapters import Adapters
from ipo_agents.calc import compute_offer, to_crore, upper_band
from ipo_agents.contracts import (MULTIPLE_LABELS, BusinessIndustry, CalcInput, ClaimedFact, Derived, Fact,
                                  FinancialPeriod, Financials, Flag, Ipo, MultipleChoice, MultipleStats,
                                  OfferObjects, OfferPriceBasis, PeerFit, PeerFitCheck, PeerQuote, PeerRow, Report,
                                  Valuation, ValuationPlan)
from ipo_agents.fin_calc import NotDerivable, full_years, profit_line
from ipo_agents.grounding import grounded_or_blank
from ipo_agents.kpis import KpiBook, Missing, operating_metric, per_kpi
from ipo_agents.peers import name_key
from ipo_agents.report.fmt import fact_value, number
from ipo_agents.verifier import COULD_NOT_VERIFY

log = logging.getLogger(__name__)

TASK = SECTION = "peer_valuation"
FIT_TASK = "peer_fit"
MAX_TOKENS = 16_000  # Sol reasons (effort high) about the business before answering
MAX_OPERATING = 6
FIT_MAX_TOKENS = 6_000  # a verdict and a few phrases per peer, up to 3 RHP peers + 3 suggested and more
MAX_SUGGESTED = 3
RICH_PREMIUM_PCT = 30.0  # premium to the peer median above which an advantage must justify it
TIMES, PCT = "×", "%"
DEFAULT_CHOICE = MultipleChoice(multiple="pe", reason="default: the model chose no multiple")
UNAVAILABLE = "data unavailable"

SYSTEM = (
    "You are an equity analyst valuing an Indian IPO against companies listed on NSE. First think about how "
    "analysts value this kind of business, then choose 2 to 4 valuation multiples, most important first, "
    "each with a one-sentence reason.\n"
    "Standard multiples: pe, ev_ebitda, ev_ebit, pb, ps. Read pb with RoE for banks, NBFCs and other lenders; "
    "use ps for a loss-maker; ev_ebitda suits capex-heavy businesses, and ev_ebit those with heavy depreciation "
    "and amortisation (telecom, power, towers), where EBITDA flatters.\n"
    "Where a business is valued on an operating measure, define a business-specific multiple: market_cap "
    "or enterprise_value divided by a KPI, e.g. EV per subscriber (telecom), EV per bed (hospitals), EV per "
    "MW (power), EV per tonne of capacity (cement, steel), market cap / AUM (asset managers, lenders), "
    "EV/GMV (marketplaces), EV per employee (IT and BPO services), EV per store (retail). Give it a short "
    "snake_case key, the label analysts write, the numerator, and the KPI name exactly as listed. Use only "
    "a KPI listed for the company and for at least two peers; never a percentage, ratio or number of days.\n"
    "Also choose 3 to 6 operating KPIs, names exactly as listed, that best show how this business compares "
    "with its peers (e.g. ARPU, utilisation, attrition, revenue per employee).\n"
    "You may suggest up to 3 more NSE-listed companies that are comparable but missing from the RHP's peer "
    "table, each with a reason; suggest none if the table is complete.\n"
    "Never write a number: market data is fetched and every multiple is computed separately."
)
FIT_SYSTEM = (
    "You check whether listed Indian companies are true peers of a company going public. For each peer, "
    "compare it with the IPO on: business line and products, customers and end market, business model "
    "(e.g. manufacturer, platform, lender, services) and geography. Give a verdict: strong (same business "
    "line, customers and model), partial (overlaps in some), weak (little in common). List 2 to 4 "
    "commonalities and up to 3 key differences, a few words each. Use the descriptions given; where a peer's "
    "profile is unavailable, rely on what is commonly known about the listed company. Return one entry per "
    "peer, with its name exactly as given. Never write a number."
)


# --- the step ------------------------------------------------------------------------------------

def run_peer_valuation(ipo: Ipo, adapters: Adapters, *, offer: OfferObjects | None, financials: Financials | None,
                       price_basis: OfferPriceBasis | None, business: BusinessIndustry | None) -> Valuation:
    years = full_years(financials.periods) if financials else None
    latest = _Year(financials, years[-1]) if years else None
    prior = _Year(financials, years[-2]) if years and len(years) > 1 else None
    unread = Derived(value=None, unit="₹ crore", not_computable_reason="offer_objects: section not read")
    calcs = compute_offer(offer, ipo) if offer is not None else {}
    mcap, fresh = calcs.get("market_cap_cr", unread), calcs.get("fresh_issue_cr", unread)

    rhp_names = [p.name for p in price_basis.peers] if price_basis else []
    book = KpiBook(price_basis)
    plan = _plan(ipo, adapters, business, rhp_names, latest, book)
    chosen = _choices(plan)
    taken = {name_key(n) for n in rhp_names}
    suggested = []
    for s in plan.additional_peers:
        if s.name.strip() and name_key(s.name) not in taken and len(suggested) < MAX_SUGGESTED:
            taken.add(name_key(s.name))
            suggested.append(s.model_copy(update={"reason": _no_numbers(s.reason)}))

    roe = any(c.multiple == "pb" for c in chosen)
    rhp_rows = [_row(n, "rhp", None, adapters, chosen, book, roe) for n in rhp_names]
    suggested_rows = [_row(s.name, "agent_suggested", s.reason, adapters, chosen, book, roe) for s in suggested]
    rhp_rows, suggested_rows = _with_fit(ipo, adapters, business, rhp_rows, suggested_rows)
    peer_set = "rhp" if any(r.quote for r in rhp_rows) or not any(r.quote for r in suggested_rows) else "agent_suggested"
    peers = rhp_rows if peer_set == "rhp" else suggested_rows

    stats = []
    for c in chosen:
        ipo_x = (ipo_multiple(c.multiple, mcap, latest, fresh) if c.kpi is None
                 else _ipo_kpi_multiple(c, mcap, latest, book, fresh))
        unit = ipo_x.unit if ipo_x.value is not None else next(
            (r.multiples[c.multiple].unit for r in peers if r.multiples.get(c.multiple) and r.multiples[c.multiple].value
             is not None), ipo_x.unit)
        median, low, high = _spread(c.multiple, peers, unit)
        stats.append(MultipleStats(multiple=c.multiple, reason=c.reason, label=c.label or "", unit=unit, kpi=c.kpi,
                                   ipo=ipo_x, median=median, min=low, max=high, premium_pct=_premium(ipo_x, median)))
    operating = [m for name in (plan.operating_metrics or [])[:MAX_OPERATING]
                 if (m := operating_metric(book, name, [r.name for r in peers])) is not None]
    checks = _checks(latest, prior, peers, price_basis)
    all_multiples = [_all_stats(m, mcap, latest, fresh, peers) for m in STANDARD]
    valuation = Valuation(ipo_market_cap=mcap, multiples=stats, rhp_peers=rhp_rows, suggested_peers=suggested_rows,
                          peer_set=peer_set, checks=checks, ipo_year=latest.period.label if latest else None,
                          ipo_figures={f: _ipo_figure(latest, f) for f in IPO_FIGURES},
                          ipo_ratios={f"{k}_pct": _ipo_ratio(latest, prior, k) for k in PEER_RATIOS},
                          operating=operating, all_multiples=all_multiples)
    return valuation.model_copy(update={"read": _read(valuation, ipo)})


def _plan(ipo, adapters, business, rhp_names, latest, book: KpiBook) -> ValuationPlan:
    if latest is None or latest.period.pat.value is None:
        earnings = "PAT not verified"
    else:
        earnings = "profit" if latest.period.pat.value > 0 else "loss"
    lines = [
        f"Company: {ipo.name}",
        f"What it sells: {business.what_it_sells if business and business.what_it_sells else 'not available'}",
        "Revenue mix: " + (", ".join(r.name for r in business.revenue_mix) if business and business.revenue_mix
                           else "not available"),
        "Competitors the RHP names: " + (", ".join(c.text for c in business.competitors)
                                         if business and business.competitors else "none"),
        "Peers in the RHP's comparison table: " + ("; ".join(rhp_names) or "none listed"),
        (f"Latest full fiscal year ({latest.period.label}): {earnings}; EBITDA "
         f"{'disclosed' if latest.period.ebitda.value is not None else 'not disclosed'}") if latest
        else "Latest full fiscal year: financials not available",
        "KPIs the RHP discloses for the company: " + ("; ".join(book.ipo_names()) or "none"),
        "KPIs the RHP discloses for its listed peers (number of peers): "
        + ("; ".join(f"{n} ({c})" for n, c in book.peer_names().items()) or "none"),
    ]
    return adapters.llm.invoke_structured(task=TASK, system=SYSTEM, user="\n".join(lines), schema=ValuationPlan,
                                          max_tokens=MAX_TOKENS).parsed


def _choices(plan: ValuationPlan) -> list[MultipleChoice]:
    """The model's multiples, deduplicated: a standard key is the standard multiple whatever else it says; a
    business-specific one needs a KPI (its numerator defaults to EV) and gets a key of its own."""
    seen, out = set(), []
    for c in plan.multiples_to_use:
        update = {"reason": _no_numbers(c.reason)}
        if c.multiple in MULTIPLE_LABELS:
            update.update(label=MULTIPLE_LABELS[c.multiple], numerator=None, kpi=None)
        elif c.kpi and c.kpi.strip():
            key = "kpi_" + (re.sub(r"[^a-z0-9]+", "_", c.multiple.casefold()).strip("_") or "multiple")
            numerator = c.numerator or "enterprise_value"
            label = _no_numbers((c.label or "").strip()) or f"{'EV' if numerator == 'enterprise_value' else 'Market cap'} / {c.kpi}"
            update.update(multiple=key, label=label, numerator=numerator)
        else:
            log.warning("peer valuation: dropped the multiple %r: neither standard nor on a KPI", c.multiple)
            continue
        if update.get("multiple", c.multiple) not in seen:
            seen.add(update.get("multiple", c.multiple))
            out.append(c.model_copy(update=update))
    return out or [DEFAULT_CHOICE]


def _no_numbers(reason: str) -> str:
    """The model is told never to write a number, and none of its numbers is verified: a reason with one
    is dropped (prose grounding, ticket 26)."""
    if kept := grounded_or_blank(reason, set()):
        return kept
    if reason:
        log.warning("peer valuation: dropped the reason %r: it holds a number", reason)
    return ""


def _with_fit(ipo, adapters, business, *row_sets: list[PeerRow]) -> tuple[list[PeerRow], ...]:
    """Each row with its peer-fit verdict; rows unchanged if the check fails (a peer-fit failure costs the
    verdicts, never the valuation)."""
    rows = [r for rs in row_sets for r in rs]
    if not rows:
        return row_sets
    try:
        check = adapters.llm.invoke_structured(task=FIT_TASK, system=FIT_SYSTEM, user=_fit_prompt(ipo, business, rows),
                                               schema=PeerFitCheck, max_tokens=FIT_MAX_TOKENS).parsed
    except Exception:
        log.warning("%s: peer-fit check failed; peers shown without it", ipo.ipo_id, exc_info=True)
        return row_sets
    fits = {}
    for f in check.peers:
        fits.setdefault(name_key(f.name), f.model_copy(update={
            "commonalities": [t for t in map(_no_numbers, f.commonalities) if t],
            "differences": [t for t in map(_no_numbers, f.differences) if t]}))
    return tuple([r.model_copy(update={"fit": fits.get(name_key(r.name))}) for r in rs] for rs in row_sets)


def _fit_prompt(ipo: Ipo, business: BusinessIndustry | None, rows: list[PeerRow]) -> str:
    def listed(items):
        return "; ".join(i for i in items if i) or "not available"
    b = business
    lines = [
        f"IPO: {ipo.name}",
        f"What it sells: {b.what_it_sells if b and b.what_it_sells else 'not available'}",
        f"Revenue mix: {listed(r.name for r in b.revenue_mix) if b else 'not available'}",
        f"Competitors the RHP names: {listed(c.text for c in b.competitors) if b else 'not available'}",
        f"Strengths it claims: {listed(m.text for m in b.moats) if b else 'not available'}",
        "", "Peers:",
    ]
    for r in rows:
        q = r.quote
        where = (f"NSE {q.symbol}; sector {q.sector or 'unknown'}; industry {q.industry or 'unknown'}"
                 if q else "no market data found")
        lines.append(f"- {r.name} ({where})")
        lines.append(f"  Profile: {q.business_summary if q and q.business_summary else 'profile unavailable'}")
        if r.reason:
            lines.append(f"  Suggested because: {r.reason}")
    return "\n".join(lines)


def _row(name: str, origin: str, reason: str | None, adapters: Adapters, chosen: list[MultipleChoice],
         book: KpiBook, roe: bool) -> PeerRow:
    try:
        quote = adapters.peers.quote(name)
    except Exception as exc:  # a third-party source, optional: a failure means this peer has no data
        log.warning("peer quote failed for %r", name, exc_info=True)
        return PeerRow(name=name, origin=origin, reason=reason, unavailable=f"{UNAVAILABLE}: {type(exc).__name__}")
    if quote is None:
        return PeerRow(name=name, origin=origin, reason=reason,
                       unavailable=f"{UNAVAILABLE}: not found on NSE or Yahoo Finance")
    multiples = {c.multiple: peer_multiple(quote, c.multiple) if c.kpi is None
                 else _peer_kpi_multiple(c, quote, name, book) for c in chosen}
    if roe:
        multiples["roe_pct"] = peer_multiple(quote, "roe_pct")
    return PeerRow(name=name, origin=origin, reason=reason, quote=quote, multiples=multiples,
                   ratios={f"{kind}_pct": _peer_ratio(quote, kind) for kind in PEER_RATIOS})


# --- IPO side ------------------------------------------------------------------------------------

class _Year:
    """One full fiscal year of the verified financials, with its index for input paths."""

    def __init__(self, financials: Financials, period: FinancialPeriod):
        self.financials = financials
        self.period = period
        self.index = financials.periods.index(period)

    def amount(self, field: str) -> tuple[float, list[CalcInput], str]:
        """Like crore, for any field, plus "ebit" and "ebitda" built from the P&L when not disclosed
        (fin_calc.profit_line); the basis says which."""
        if field in ("ebit", "ebitda"):
            try:
                return profit_line(self.financials, self.period, field)
            except NotDerivable as exc:
                raise _Missing(str(exc)) from None
        value, inp = self.crore(field)
        return value, [inp], ""

    def crore(self, field: str) -> tuple[float, CalcInput]:
        """The field in ₹ crore and its input; raises _Missing with the reason."""
        f: Fact = getattr(self.period, field)
        path = f"financials.periods[{self.index}].{field}"
        if f.value is None:
            raise _Missing(f"{path}: {COULD_NOT_VERIFY if f.not_disclosed_reason == COULD_NOT_VERIFY else 'not disclosed'}")
        try:
            return to_crore(f), CalcInput(field=path, page=f.page)
        except ValueError as exc:
            raise _Missing(f"{path}: {exc}") from None


_Missing = Missing


# multiple -> (denominator field on FinancialPeriod / PeerQuote, its name in reasons)
_DENOMINATOR = {"pe": ("pat", "PAT"), "ps": ("revenue", "revenue"), "pb": ("net_worth", "net worth"),
                "ev_ebitda": ("ebitda", "EBITDA"), "ev_ebit": ("ebit", "EBIT")}
_IPO_FIELD = {"pat": "pat", "revenue": "revenue_from_operations", "net_worth": "net_worth", "ebitda": "ebitda",
              "ebit": "ebit"}
STANDARD = ("pe", "ev_ebitda", "ev_ebit", "pb", "ps")  # Valuation.all_multiples, in this order
EV_MULTIPLES = ("ev_ebitda", "ev_ebit")


def _ipo_ev(mcap: Derived, latest: _Year, fresh: Derived | None) -> tuple[float, list[CalcInput], str]:
    """Post-issue EV in ₹ crore: market cap (post-issue shares at the upper band) + debt − cash − the fresh
    issue, whose cash the company holds once the new shares are issued (proceeds that repay debt cut debt and
    cash alike). Pre-issue cash, said so, when the fresh issue is not computable."""
    debt, debt_in = latest.crore("total_borrowings")
    cash, cash_in = latest.crore("cash_and_equivalents")
    inputs = list(mcap.inputs) + [debt_in, cash_in]
    if fresh is not None and fresh.value is not None:
        return (mcap.value + debt - cash - fresh.value, inputs + fresh.inputs,
                f"post-issue EV: market cap ₹{mcap.value:,.2f} crore + debt − cash − fresh issue ₹{fresh.value:,.2f} crore")
    why = fresh.not_computable_reason if fresh is not None else "not given"
    return (mcap.value + debt - cash, inputs,
            f"EV: market cap ₹{mcap.value:,.2f} crore + debt − pre-issue cash (fresh issue not computable: {why})")


def ipo_multiple(m: str, mcap: Derived, latest: _Year | None, fresh: Derived | None = None) -> Derived:
    """The IPO's multiple at the upper band on its latest full fiscal year. P/B is on post-issue net worth
    (latest full-year net worth + the fresh issue, ₹ crore), falling back to pre-issue net worth, said so in
    `basis`, when the fresh issue is not computable."""
    if mcap.value is None:
        return _nc(TIMES, f"market cap: {mcap.not_computable_reason}")
    if latest is None:
        return _nc(TIMES, "financials: no full fiscal year with a readable year")
    field, label = _DENOMINATOR[m]
    try:
        denom, d_in, how = latest.amount(_IPO_FIELD[field])
        numerator, inputs, ev_basis = mcap.value, list(mcap.inputs), ""
        if m in EV_MULTIPLES:
            numerator, inputs, ev_basis = _ipo_ev(mcap, latest, fresh)
    except _Missing as exc:
        return _nc(TIMES, str(exc))
    inputs += d_in
    if denom <= 0:
        return _nc(TIMES, f"{latest.period.label} {label} is {'a loss' if denom < 0 else 'zero'} "
                          f"(₹{denom:,.2f} crore): {MULTIPLE_LABELS[m]} is not meaningful", inputs)
    if m == "pb":
        if fresh is not None and fresh.value is not None:
            if denom + fresh.value <= 0:
                return _nc(TIMES, f"post-issue net worth (₹{denom:,.2f} crore + fresh issue ₹{fresh.value:,.2f} "
                                  "crore) is zero or negative: P/B is not meaningful", inputs + fresh.inputs)
            # Proceeds of the fresh issue go to the company, so the book it lists with includes them.
            at = "at upper band" if "upper band" in fresh.basis else "as stated in the RHP"
            return Derived(value=numerator / (denom + fresh.value), unit=TIMES, inputs=inputs + fresh.inputs,
                           basis=f"post-issue net worth: FY net worth + fresh issue {at}")
        why = fresh.not_computable_reason if fresh is not None else "not given"
        return Derived(value=numerator / denom, unit=TIMES, inputs=inputs,
                       basis=f"pre-issue net worth (fresh issue not computable: {why})")
    basis = f"({ev_basis}) / " if m in EV_MULTIPLES else f"market cap ₹{mcap.value:,.2f} crore at the upper band / "
    return Derived(value=numerator / denom, unit=TIMES, inputs=inputs,
                   basis=basis + f"{latest.period.label} {label} ₹{denom:,.2f} crore" + (f"; {how}" if how else ""))


def _ipo_kpi_multiple(c: MultipleChoice, mcap: Derived, latest: _Year | None, book: KpiBook,
                      fresh: Derived | None = None) -> Derived:
    """The IPO's market cap or EV at the upper band per unit of an RHP KPI, latest full fiscal year."""
    if mcap.value is None:
        return _nc(TIMES, f"market cap: {mcap.not_computable_reason}")
    numerator, inputs, what = mcap.value, list(mcap.inputs), "market cap at the upper band"
    try:
        if c.numerator == "enterprise_value":
            if latest is None:
                raise _Missing("financials: no full fiscal year for debt and cash")
            numerator, inputs, _ = _ipo_ev(mcap, latest, fresh)
            what = "post-issue EV at the upper band"
        kpi = book.ipo(c.kpi)
    except _Missing as exc:
        return _nc(TIMES, str(exc), inputs)
    return per_kpi(numerator, inputs, kpi, f"{what} ₹{numerator:,.2f} crore")


def _peer_kpi_multiple(c: MultipleChoice, q: PeerQuote, name: str, book: KpiBook) -> Derived:
    try:
        numerator, m_in = _claim(q, "market_cap")
        inputs, what = [m_in], f"{q.symbol}: market cap"
        if c.numerator == "enterprise_value":
            debt, debt_in = _claim(q, "debt")
            cash, cash_in = _claim(q, "cash")
            numerator, inputs, what = numerator + debt - cash, inputs + [debt_in, cash_in], f"{q.symbol}: EV"
        kpi = book.peer(name, c.kpi)
    except _Missing as exc:
        return _nc(TIMES, str(exc))
    return per_kpi(numerator, inputs, kpi, what)


# --- peer side -----------------------------------------------------------------------------------

def _claim(q: PeerQuote, field: str) -> tuple[float, CalcInput]:
    c: ClaimedFact | None = getattr(q, field)
    if c is None or c.value is None:
        raise _Missing(f"peers.{q.symbol}.{field}: not available from the source")
    return c.value, CalcInput(field=f"peers.{q.symbol}.{field}", source=f"{c.source_name}, as of {c.as_of.isoformat()}")


def peer_multiple(q: PeerQuote, m: str) -> Derived:
    """A peer's multiple (or RoE %, "roe_pct") from its market cap and latest annual figures."""
    unit = PCT if m == "roe_pct" else TIMES
    try:
        if m == "roe_pct":
            pat, pat_in = _claim(q, "pat")
            nw, nw_in = _claim(q, "net_worth")
            if nw <= 0:
                return _nc(unit, f"{q.symbol}: net worth is zero or negative", [pat_in, nw_in])
            return Derived(value=100 * pat / nw, unit=unit, inputs=[pat_in, nw_in], basis="PAT / net worth")
        numerator, inputs = _claim(q, "market_cap")
        inputs = [inputs]
        if m in EV_MULTIPLES:
            debt, debt_in = _claim(q, "debt")
            cash, cash_in = _claim(q, "cash")
            numerator += debt - cash
            inputs += [debt_in, cash_in]
        field, label = _DENOMINATOR[m]
        denom, d_in = _claim(q, field)
    except _Missing as exc:
        return _nc(unit, str(exc))
    inputs.append(d_in)
    if denom <= 0:
        return _nc(unit, f"{q.symbol}: {label} is zero or negative; {MULTIPLE_LABELS[m]} is not meaningful", inputs)
    return Derived(value=numerator / denom, unit=unit, inputs=inputs, basis=f"{q.symbol}: {MULTIPLE_LABELS[m]}")


def _spread(m: str, peers: list[PeerRow], unit: str = TIMES) -> tuple[Derived, Derived, Derived]:
    got = [(r, r.multiples[m]) for r in peers
           if r.multiples.get(m) and r.multiples[m].value is not None and r.multiples[m].unit == unit]
    if not got:
        why = f"no peer with data for {MULTIPLE_LABELS.get(m, m)}"
        return _nc(unit, why), _nc(unit, why), _nc(unit, why)
    values = [d.value for _, d in got]
    inputs = [i for _, d in got for i in d.inputs]
    names = ", ".join(r.quote.symbol for r, _ in got)

    def stat(value, what):
        return Derived(value=value, unit=unit, inputs=inputs, basis=f"{what} of {len(got)} peers ({names})")
    return stat(statistics.median(values), "median"), stat(min(values), "min"), stat(max(values), "max")


def _premium(ipo_x: Derived, median: Derived) -> Derived:
    unit = "% vs peer median"
    if ipo_x.value is None:
        return _nc(unit, f"IPO: {ipo_x.not_computable_reason}")
    if median.value is None or median.value <= 0:
        return _nc(unit, f"peer median: {median.not_computable_reason or 'zero or negative'}")
    return Derived(value=100 * (ipo_x.value / median.value - 1), unit=unit, inputs=ipo_x.inputs + median.inputs,
                   basis=f"{_fmt(ipo_x.value, ipo_x.unit, 2)} vs peer median {_fmt(median.value, median.unit, 2)}")


# --- growth and margin (VALUATION_RICH) ----------------------------------------------------------

def _checks(latest: _Year | None, prior: _Year | None, peers: list[PeerRow],
            price_basis: OfferPriceBasis | None) -> dict[str, Derived]:
    """Revenue growth, PAT margin and RoE, the IPO vs its peers' median. The peers' RoE is the RHP's own peer
    table (RoNW) where it gives one, else market data."""
    rhp_roe = _rhp_peer_roe(price_basis)
    return {"ipo_revenue_growth_pct": _ipo_ratio(latest, prior, "growth"),
            "ipo_pat_margin_pct": _ipo_ratio(latest, None, "margin"),
            "peer_median_revenue_growth_pct": _peer_median(peers, "growth"),
            "peer_median_pat_margin_pct": _peer_median(peers, "margin"),
            "ipo_roe_pct": _ipo_ratio(latest, None, "roe"),
            "peer_median_roe_pct": rhp_roe if rhp_roe.value is not None else _peer_median(peers, "roe")}


def _rhp_peer_roe(pb: OfferPriceBasis | None) -> Derived:
    got = [p for p in (pb.peers if pb else []) if p.ronw_pct.value is not None]
    if not got:
        return _nc(PCT, "the RHP's peer table gives no verified RoNW")
    return Derived(value=statistics.median(p.ronw_pct.value for p in got), unit=PCT,
                   inputs=[CalcInput(field=f"offer_price_basis.peers[{p.name}].ronw_pct", page=p.ronw_pct.page) for p in got],
                   basis=f"median RoNW of {len(got)} peers in the RHP's peer table ({', '.join(p.name for p in got)})")


def _all_stats(m: str, mcap: Derived, latest: _Year | None, fresh: Derived | None, peers: list[PeerRow]) -> MultipleStats:
    """One standard multiple for Valuation.all_multiples: the IPO from its RHP financials against the same
    peers' median, computed whether or not the model chose it."""
    ipo_x = ipo_multiple(m, mcap, latest, fresh)
    rows = [r.model_copy(update={"multiples": {m: peer_multiple(r.quote, m)}}) for r in peers if r.quote]
    median, low, high = _spread(m, rows, TIMES)
    return MultipleStats(multiple=m, reason="", unit=TIMES, ipo=ipo_x, median=median, min=low, max=high,
                         premium_pct=_premium(ipo_x, median))


# Valuation.ipo_figures: the IPO's latest full-year figures (FinancialPeriod fields), in ₹ crore
IPO_FIGURES = ("revenue_from_operations", "ebitda", "pat", "net_worth")


def _ipo_figure(latest: _Year | None, field: str) -> Derived:
    if latest is None:
        return _nc("₹ crore", "financials: no full fiscal year with a readable year")
    try:
        value, inputs, how = latest.amount(field)
    except _Missing as exc:
        return _nc("₹ crore", str(exc))
    return Derived(value=value, unit="₹ crore", inputs=inputs,
                   basis=f"{latest.period.label} {field.replace('_', ' ')}" + (f"; {how}" if how else ""))


def _ipo_ratio(latest: _Year | None, prior: _Year | None, kind: str) -> Derived:
    if latest is None:
        return _nc(PCT, "financials: no full fiscal year with a readable year")
    try:
        if kind in ("growth", "revenue_growth"):
            if prior is None:
                return _nc(PCT, "financials: needs 2 full fiscal years")
            new, new_in = latest.crore("revenue_from_operations")
            old, old_in = prior.crore("revenue_from_operations")
            num, den, inputs, basis = new - old, old, [new_in, old_in], f"{latest.period.label} vs {prior.period.label} revenue"
        else:
            top = "ebitda" if kind == "ebitda_margin" else "pat"
            bottom = "net_worth" if kind == "roe" else "revenue_from_operations"
            num, num_in = latest.crore(top)
            den, den_in = latest.crore(bottom)
            inputs = [num_in, den_in]
            basis = f"{latest.period.label} {'EBITDA' if top == 'ebitda' else 'PAT'} / {'net worth' if kind == 'roe' else 'revenue'}"
    except _Missing as exc:
        return _nc(PCT, str(exc))
    if den <= 0:
        return _nc(PCT, "denominator is zero or negative", inputs)
    return Derived(value=100 * num / den, unit=PCT, inputs=inputs, basis=basis)


# PeerRow.ratios: "<kind>_pct" for each kind, as _peer_ratio names them
PEER_RATIOS = ("revenue_growth", "ebitda_margin", "pat_margin", "roe")
# kind -> (numerator field, denominator field, basis); "growth"/"margin" are the checks' names for two of them
_RATIO = {"growth": ("revenue", "revenue_prior_year", "revenue growth"),
          "revenue_growth": ("revenue", "revenue_prior_year", "revenue growth"),
          "margin": ("pat", "revenue", "PAT / revenue"), "pat_margin": ("pat", "revenue", "PAT / revenue"),
          "ebitda_margin": ("ebitda", "revenue", "EBITDA / revenue"), "roe": ("pat", "net_worth", "PAT / net worth")}


def _peer_ratio(q: PeerQuote, kind: str) -> Derived:
    num_field, den_field, basis = _RATIO[kind]
    try:
        num, num_in = _claim(q, num_field)
        den, den_in = _claim(q, den_field)
        inputs = [num_in, den_in]
    except _Missing as exc:
        return _nc(PCT, str(exc))
    if den <= 0:
        return _nc(PCT, "denominator is zero or negative", inputs)
    if num_field == "revenue" and den_field == "revenue_prior_year":
        num -= den
    return Derived(value=100 * num / den, unit=PCT, inputs=inputs, basis=f"{q.symbol}: {basis}")


def _peer_median(peers: list[PeerRow], kind: str) -> Derived:
    got = [(r.quote.symbol, d) for r in peers if r.quote and (d := _peer_ratio(r.quote, kind)).value is not None]
    if not got:
        return _nc(PCT, f"no peer with data for {kind}")
    return Derived(value=statistics.median(d.value for _, d in got), unit=PCT,
                   inputs=[i for _, d in got for i in d.inputs],
                   basis=f"median of {len(got)} peers ({', '.join(s for s, _ in got)})")


def _advantage(checks: dict[str, Derived], kind: str) -> bool | None:
    ipo, peer = checks.get(f"ipo_{kind}_pct"), checks.get(f"peer_median_{kind}_pct")
    if not ipo or not peer or ipo.value is None or peer.value is None:
        return None
    return ipo.value > peer.value


# --- flags, the read, the summary line ----------------------------------------------------------

def valuation_flags(v: Valuation | None) -> list[Flag]:
    return _rich_flags(v) + peer_fit_flags(v)


def _rich_flags(v: Valuation | None) -> list[Flag]:
    """VALUATION_RICH: over RICH_PREMIUM_PCT to the peer median on the headline multiple, with neither revenue
    growth nor PAT margin above the peer median. A warning when both were compared; info when either
    comparison couldn't be computed (and the other showed no advantage)."""
    h = v.headline if v else None
    if h is None or h.premium_pct.value <= RICH_PREMIUM_PCT:
        return []
    growth, margin = _advantage(v.checks, "revenue_growth"), _advantage(v.checks, "pat_margin")
    if growth or margin:
        return []
    head = (f"Priced at {_fmt(h.ipo.value, h.unit)} {h.title} vs peer median {_fmt(h.median.value, h.unit)}, a "
            f"{h.premium_pct.value:.0f}% premium")
    if growth is False and margin is False:
        c = v.checks
        return [Flag(code="VALUATION_RICH", severity="warning", section=SECTION, evidence_pages=h.ipo.pages,
                     message=f"{head}, without faster growth or fatter margins: revenue growth "
                             f"{c['ipo_revenue_growth_pct'].value:.1f}% vs peer median "
                             f"{c['peer_median_revenue_growth_pct'].value:.1f}%, PAT margin "
                             f"{c['ipo_pat_margin_pct'].value:.1f}% vs {c['peer_median_pat_margin_pct'].value:.1f}%")]
    missing = "; ".join(d.not_computable_reason for k, d in v.checks.items()
                        if d.value is None and ("growth" in k or "margin" in k))
    return [Flag(code="VALUATION_RICH", severity="info", section=SECTION, evidence_pages=h.ipo.pages,
                 message=f"{head}; whether growth or margins justify it could not be checked ({missing})")]


def peer_fit_flags(v: Valuation | None) -> list[Flag]:
    """PEER_SET_WEAK (info): the peer-fit check rates most of the peers behind the median weak, so the
    median may say little about this business. The peers stay in the median; this only says so."""
    peers = [r for r in (v.rhp_peers if v.peer_set == "rhp" else v.suggested_peers) if r.quote] if v else []
    weak = [r.name for r in peers if r.fit and r.fit.verdict == "weak"]
    if not peers or 2 * len(weak) <= len(peers):
        return []
    return [Flag(code="PEER_SET_WEAK", severity="info", section=SECTION,
                 message=f"{len(weak)} of the {len(peers)} peers behind the median are weak matches for this "
                         f"business ({', '.join(weak)}): read the peer median with care")]


def _read(v: Valuation, ipo: Ipo) -> str:
    band = upper_band(ipo.price_band)
    whose = "RHP peer" if v.peer_set == "rhp" else "agent-suggested peer (the RHP lists none with data)"
    parts = []
    if v.ipo_market_cap.value is not None:
        parts.append(f"Market cap at the upper band ₹{band:,g}: ₹{v.ipo_market_cap.value:,.0f} crore.")
    else:
        parts.append(f"Market cap at the upper band not computable ({v.ipo_market_cap.not_computable_reason}).")
    for m in v.multiples:
        label = m.title
        if m.premium_pct.value is not None:
            word = "premium" if m.premium_pct.value >= 0 else "discount"
            parts.append(f"Priced at {_fmt(m.ipo.value, m.unit)} {label} vs {whose} median {_fmt(m.median.value, m.unit)} "
                         f"(range {_fmt(m.min.value, m.unit)}–{_fmt(m.max.value, m.unit)}) → "
                         f"{abs(m.premium_pct.value):.0f}% {word}.")
        else:
            parts.append(f"{label}: not computable ({m.premium_pct.not_computable_reason}).")
    c = v.checks
    for kind, label in (("revenue_growth", "Revenue growth"), ("pat_margin", "PAT margin"), ("roe", "RoE")):
        ipo_d, peer_d = c.get(f"ipo_{kind}_pct"), c.get(f"peer_median_{kind}_pct")
        if ipo_d and peer_d and ipo_d.value is not None and peer_d.value is not None:
            parts.append(f"{label} {ipo_d.value:.1f}% vs peer median {peer_d.value:.1f}%.")
    fitted = [r for r in v.rhp_peers + v.suggested_peers if r.fit]
    if fitted:
        counts = [f"{n} {verdict}" for verdict in ("strong", "partial", "weak")
                  if (n := sum(r.fit.verdict == verdict for r in fitted))]
        weak = [r.name for r in fitted if r.fit.verdict == "weak"]
        parts.append(f"Peer fit: {', '.join(counts)}" + (f" (weak: {', '.join(weak)})." if weak else "."))
    missing = [r.name for r in v.rhp_peers + v.suggested_peers if r.unavailable]
    if missing:
        parts.append(f"Data unavailable for: {', '.join(missing)}.")
    sources = sorted({f"{r.quote.market_cap.source_name} {r.quote.market_cap.as_of:%d %b %Y}"
                      for r in v.rhp_peers + v.suggested_peers if r.quote and r.quote.market_cap})
    if sources:
        parts.append(f"Peer market caps from {', '.join(sources)}.")
    return " ".join(parts)


def summary_line(report: Report) -> str | None:
    """E.g. "Valuation: P/E 30.0× vs peer median 25.0× (20% premium)"; None when nothing was computed."""
    h = report.valuation.headline if report.valuation else None
    if h is None:
        return None
    word = "premium" if h.premium_pct.value >= 0 else "discount"
    return (f"Valuation: {h.title} {_fmt(h.ipo.value, h.unit)} vs peer median {_fmt(h.median.value, h.unit)} "
            f"({abs(h.premium_pct.value):.0f}% {word})")


def operating_line(report: Report) -> str | None:
    """E.g. "Operating: ARPU ₹208 vs peer median ₹190 · Beds 1,200 vs 2,400"; None with nothing to compare."""
    v = report.valuation
    parts = [f"{m.name} {fact_value(m.ipo)} vs peer median {number(m.median.value, m.median.unit)}"
             for m in (v.operating if v else []) if m.ipo and m.ipo.value is not None and m.median.value is not None]
    return "Operating: " + " · ".join(parts) if parts else None


VERDICT_MARK = {"strong": "✅ strong peer", "partial": "🟡 partial peer", "weak": "⚠️ weak peer"}
SUMMARY_PEERS = 6


def peer_lines(report: Report) -> list[str]:
    """One line per peer with data for the summary: verdict, multiples, revenue and growth, PAT margin and
    what it shares with the IPO; the peers behind the median first. Peers with no data are named after."""
    v = report.valuation
    if v is None:
        return []
    rows = v.rhp_peers + v.suggested_peers if v.peer_set == "rhp" else v.suggested_peers + v.rhp_peers
    lines = []
    for r in [r for r in rows if r.quote][:SUMMARY_PEERS]:
        parts = [f"{r.name} ({r.quote.symbol})" + (" [suggested]" if r.origin == "agent_suggested" else "")]
        if r.fit:
            parts.append(VERDICT_MARK[r.fit.verdict])
        parts += [f"{m.title} {number(d.value, d.unit)}" for m in v.multiples
                  if (d := r.multiples.get(m.multiple)) and d.value is not None]
        if r.quote.revenue:
            growth = r.ratios.get("revenue_growth_pct")
            parts.append(f"Rev {number(r.quote.revenue.value, r.quote.revenue.unit)}"
                         + (f" ({growth.value:+.0f}%)" if growth and growth.value is not None else ""))
        if (margin := r.ratios.get("pat_margin_pct")) and margin.value is not None:
            parts.append(f"PAT margin {margin.value:.1f}%")
        if r.fit and r.fit.commonalities:
            parts.append("common: " + ", ".join(r.fit.commonalities))
        lines.append(" · ".join(parts))
    missing = [r.name for r in rows if not r.quote]
    if missing:
        lines.append(f"No market data: {', '.join(missing)}")
    return lines


def _fmt(value: float, unit: str, decimals: int = 1) -> str:
    """30.0× for a ratio (as the read has always written it); ₹ amounts per unit as the report writes them."""
    return f"{value:.{decimals}f}×" if unit == TIMES else number(value, unit)


def _nc(unit: str, why: str, inputs: list[CalcInput] | None = None) -> Derived:
    return Derived(value=None, unit=unit, inputs=inputs or [], not_computable_reason=why)
