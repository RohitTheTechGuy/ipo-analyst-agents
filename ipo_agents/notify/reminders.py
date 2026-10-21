"""Daily updates for analysed IPOs until they list: today's GMP and subscription, plus the closing
reminder on the last two days (spec story 47), with the analysis-day advisor call. After close, an update
goes out each weekday through the expected listing day (store.expected_listing, T+3): GMP and the final
subscription, with the latest call (the closing-day refresh when there was one). No model calls.

On the closing day (config.advisor.closing_day_refresh) the advisor's call is refreshed first: the saved
report.json is reloaded, sentiment re-gathered and the advisor re-run, all on the IPO's advisor ledger
(ticket 27). Then the demand check (demand_check.py) judges that call against the final subscription, on the
same ledger. An IPO analysed on its closing day gets the demand check right after its summary
(closing_day_check). Every other update is third-party numbers only, with no model calls. A failed refresh
or check sends the normal update."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from ipo_agents.adapters import Adapters, MarketData
from ipo_agents.advisor import advisor_llm, refresh_advice
from ipo_agents.config import Config
from ipo_agents.contracts import CheckedAdvice, CheckedDemand, ClaimedFact, Ipo, Report, SubscriptionStatus
from ipo_agents.demand_check import check_demand
from ipo_agents.report.labels import CALL_LABELS, advice_text, subscription_text
from ipo_agents.archive import archive_update
from ipo_agents.store import Store, expected_listing

log = logging.getLogger(__name__)


def _market(market: MarketData | None, ipo: Ipo) -> tuple[ClaimedFact | None, SubscriptionStatus | None]:
    """Today's GMP and subscription; a missing or failing source reads as not available, never an error."""
    if market is None:
        return None, None
    gmp = sub = None
    try:
        gmp = market.gmp(ipo)
    except Exception:
        log.warning("GMP unavailable for %s", ipo.ipo_id, exc_info=True)
    try:
        sub = market.subscription(ipo)
    except Exception:
        log.warning("subscription unavailable for %s", ipo.ipo_id, exc_info=True)
    return gmp, sub


def _advice_lines(advice: CheckedAdvice | None, earlier: CheckedAdvice | None) -> list[str]:
    """The call refreshed today (with any change from `earlier`, the analysis-day call), or the
    analysis-day call when there was no refresh; nothing if the advisor never gave one."""
    if advice is None:
        return []
    if advice is earlier:
        lines = [f"🧭 Advisor ({advice.as_of:%-d %b}): {advice_text(advice)}"]
    else:
        lines = [f"🧭 Advisor (refreshed today): {advice_text(advice)}"]
        if earlier is None:
            pass  # no analysis-day call to compare with
        elif advice.call != earlier.call:
            lines.append(f"Change: was {CALL_LABELS[earlier.call]} → now {CALL_LABELS[advice.call]}")
        else:
            lines.append(f"Same call as on {earlier.as_of:%-d %b}")
    if advice.headline:
        lines.append(advice.headline)
    return lines


DEMAND_LABELS = {"hot": "HIGH demand", "normal": "Normal demand", "weak": "WEAK demand"}


def _demand_lines(check: CheckedDemand | None) -> list[str]:
    """The demand check's reading of the subscription (on the line above) and of the call; nothing without
    a check."""
    if check is None:
        return []
    lines = [f"🔎 Demand check: {DEMAND_LABELS[check.level]}", *(f"• {r.text}" for r in check.demand_reasons)]
    if check.retail_reading:
        lines.append(f"Retail: {check.retail_reading}")
    why = f" {check.verdict_vs_demand}" if check.verdict_vs_demand else ""
    against = f"{DEMAND_LABELS[check.level]} vs {CALL_LABELS[check.call]}"
    if check.contradicts and check.call_holds:
        lines.append(f"⚠️ {against}: the call still holds.{why}")
    elif check.contradicts:
        lines.append(f"❗ {against}: the check thinks demand weakens the call.{why}")
    elif not check.call_holds:
        lines.append(f"❗ The check questions the call.{why}")
    else:
        lines.append(f"✅ Demand fits the call.{why}")
    return lines


def update_text(ipo: Ipo, verdict_label: str, today: date, gmp: ClaimedFact | None,
                sub: SubscriptionStatus | None, advice: CheckedAdvice | None = None,
                earlier: CheckedAdvice | None = None, demand: CheckedDemand | None = None) -> str:
    """`advice` is today's refreshed call or the analysis-day one; `earlier` the analysis-day call."""
    closed = today > ipo.close_date
    listing = expected_listing(ipo.close_date)
    if closed and today >= listing:
        head = f"🔔 {ipo.name} is expected to list TODAY"
    elif closed:
        head = f"📈 {ipo.name} IPO · bidding closed {ipo.close_date:%a %-d %b}, expected to list {listing:%a %-d %b}"
    elif ipo.close_date == today:
        head = f"⏰ {ipo.name} IPO closes TODAY"
    elif (ipo.close_date - today).days == 1:
        head = f"⏰ {ipo.name} IPO closes TOMORROW"
    else:
        head = f"📊 {ipo.name} IPO update · closes {ipo.close_date:%a %-d %b}"
    lines = [head, *_advice_lines(advice, earlier), f"Verdict was: {verdict_label}"]
    if gmp is not None and gmp.value is not None:
        lines.append(f"GMP ₹{gmp.value:,g} (unofficial, {gmp.source_name}, as of {gmp.as_of:%-d %b})")
    else:
        lines.append("GMP: not available")
    if sub is not None and sub.total.value is not None:
        lines.append(f"{'Final subscription' if closed else 'Subscription'}: {subscription_text(sub)} "
                     f"as of {sub.total.as_of:%-d %b}")
    else:
        lines.append("Subscription: not available" if closed else "Subscription: not available yet")
    return "\n".join([*lines, *_demand_lines(demand)])


def _load_report(store: Store, ipo: Ipo) -> Report | None:
    """The report saved on analysis day; None if there is none or it can't be read."""
    path = store.report_path(ipo.ipo_id)
    if not path:
        return None
    try:
        return Report.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except Exception:
        log.warning("%s: could not load %s", ipo.ipo_id, path, exc_info=True)
        return None


def _latest_advice(config: Config, ipo_id: str, today: date, earlier: CheckedAdvice | None) -> CheckedAdvice | None:
    """The newest call saved up to today (the closing-day refresh writes advice_<date>.json), else `earlier`."""
    saved = sorted(p for p in (Path(config.work_dir) / ipo_id).glob("advice_*.json")
                   if p.stem.removeprefix("advice_") <= f"{today:%Y-%m-%d}")
    if not saved:
        return earlier
    try:
        return CheckedAdvice.model_validate_json(saved[-1].read_text(encoding="utf-8"))
    except Exception:
        log.warning("%s: could not load %s", ipo_id, saved[-1], exc_info=True)
        return earlier


def closing_day_check(report: Report, adapters: Adapters, config: Config, today: date) -> str | None:
    """For an IPO analysed on its closing day: its summary already has today's call and subscription, so
    only the demand check is missing. Returns the message, or None without a check (fail-soft)."""
    ipo = report.ipo
    sentiment = report.sentiment
    demand = check_demand(report, report.advice, sentiment, advisor_llm(adapters.llm, ipo.ipo_id, config),
                          config, today)
    if demand is None:
        return None
    sub = sentiment.subscription
    text = "\n".join([f"⏰ {ipo.name} IPO closes TODAY",
                      f"Subscription: {subscription_text(sub)} as of {sub.total.as_of:%-d %b}",
                      *_demand_lines(demand)])
    archive_update(ipo.ipo_id, text, config.reports_dir, today)
    return text


def send_daily_updates(today: date, store: Store, adapters: Adapters, config: Config,
                       verdict_labels: dict[str, str]) -> tuple[list[str], list[str]]:
    """One update per analysed IPO per day, while open and after close until it lists (store.due_updates);
    returns (ipo_ids updated, ipo_ids whose update failed to send). An IPO analysed today already got its summary (with
    GMP and subscription), so it is skipped."""
    sent, failed = [], []
    for ipo, verdict in store.due_updates(today):
        report = _load_report(store, ipo)
        earlier = report.advice if report else None
        advice, market, demand = earlier, None, None
        if today > ipo.close_date:
            advice = _latest_advice(config, ipo.ipo_id, today, earlier)
            earlier = advice  # nothing refreshed today: shown with its own date
        elif report is not None and ipo.close_date == today and config.advisor.closing_day_refresh:
            try:
                fresh, sentiment = refresh_advice(report, adapters, config, today)
                advice = fresh or earlier  # no fresh call: the analysis-day one stands
                market = sentiment.gmp, sentiment.subscription
                demand = check_demand(report, advice, sentiment, advisor_llm(adapters.llm, ipo.ipo_id, config),
                                      config, today)
            except Exception:
                log.exception("%s: closing-day advisor refresh failed; sending the normal update", ipo.ipo_id)
        gmp, sub = market if market is not None else _market(adapters.market, ipo)
        text = update_text(ipo, verdict_labels.get(verdict, "N/A"), today, gmp, sub, advice, earlier, demand)
        archive_update(ipo.ipo_id, text, config.reports_dir, today)
        try:
            adapters.notifier.send_message(text)
        except Exception:
            log.exception("daily update send failed for %s", ipo.ipo_id)
            failed.append(ipo.ipo_id)
            continue
        store.mark_reminded(ipo.ipo_id, today)
        sent.append(ipo.ipo_id)
    return sent, failed
