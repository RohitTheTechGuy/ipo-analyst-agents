"""Daily updates for analysed IPOs until they close: today's GMP and subscription, plus the closing
reminder on the last two days (spec story 47), with the analysis-day advisor call.

On the closing day (config.advisor.closing_day_refresh) the advisor's call is refreshed first: the saved
report.json is reloaded, sentiment re-gathered and the advisor re-run, all on the IPO's advisor ledger
(ticket 27). Every other update is third-party numbers only, with no model calls. A failed refresh sends
the normal update."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from ipo_agents.adapters import Adapters, MarketData
from ipo_agents.advisor import refresh_advice
from ipo_agents.config import Config
from ipo_agents.contracts import CheckedAdvice, ClaimedFact, Ipo, Report, SubscriptionStatus
from ipo_agents.report.labels import CALL_LABELS, advice_text, subscription_text
from ipo_agents.archive import archive_update
from ipo_agents.store import Store

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


def update_text(ipo: Ipo, verdict_label: str, today: date, gmp: ClaimedFact | None,
                sub: SubscriptionStatus | None, advice: CheckedAdvice | None = None,
                earlier: CheckedAdvice | None = None) -> str:
    """`advice` is today's refreshed call or the analysis-day one; `earlier` the analysis-day call."""
    if ipo.close_date == today:
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
        lines.append(f"Subscription: {subscription_text(sub)} as of {sub.total.as_of:%-d %b}")
    else:
        lines.append("Subscription: not available yet")
    return "\n".join(lines)


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


def send_daily_updates(today: date, store: Store, adapters: Adapters, config: Config,
                       verdict_labels: dict[str, str]) -> list[str]:
    """One update per analysed, still-open IPO per day; returns the ipo_ids whose update failed to send.
    An IPO analysed today already got its summary (with GMP and subscription), so it is skipped."""
    failed = []
    for ipo, verdict in store.due_updates(today):
        report = _load_report(store, ipo)
        earlier = report.advice if report else None
        advice, market = earlier, None
        if report is not None and ipo.close_date == today and config.advisor.closing_day_refresh:
            try:
                fresh, sentiment = refresh_advice(report, adapters, config, today)
                advice = fresh or earlier  # no fresh call: the analysis-day one stands
                market = sentiment.gmp, sentiment.subscription
            except Exception:
                log.exception("%s: closing-day advisor refresh failed; sending the normal update", ipo.ipo_id)
        gmp, sub = market if market is not None else _market(adapters.market, ipo)
        text = update_text(ipo, verdict_labels.get(verdict, "N/A"), today, gmp, sub, advice, earlier)
        archive_update(ipo.ipo_id, text, config.reports_dir, today)
        try:
            adapters.notifier.send_message(text)
        except Exception:
            log.exception("daily update send failed for %s", ipo.ipo_id)
            failed.append(ipo.ipo_id)
            continue
        store.mark_reminded(ipo.ipo_id, today)
    return failed
