"""Daily updates for analysed IPOs until they close: today's GMP and subscription, plus the closing
reminder on the last two days (spec story 47). Third-party numbers only; no model calls."""
from __future__ import annotations

import logging
from datetime import date

from ipo_agents.adapters import MarketData, Notifier
from ipo_agents.contracts import ClaimedFact, Ipo, SubscriptionStatus
from ipo_agents.report.labels import subscription_text
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


def update_text(ipo: Ipo, verdict_label: str, today: date, gmp: ClaimedFact | None,
                sub: SubscriptionStatus | None) -> str:
    if ipo.close_date == today:
        head = f"⏰ {ipo.name} IPO closes TODAY"
    elif (ipo.close_date - today).days == 1:
        head = f"⏰ {ipo.name} IPO closes TOMORROW"
    else:
        head = f"📊 {ipo.name} IPO update · closes {ipo.close_date:%a %-d %b}"
    lines = [head, f"Verdict was: {verdict_label}"]
    if gmp is not None and gmp.value is not None:
        lines.append(f"GMP ₹{gmp.value:,g} (unofficial, {gmp.source_name}, as of {gmp.as_of:%-d %b})")
    else:
        lines.append("GMP: not available")
    if sub is not None and sub.total.value is not None:
        lines.append(f"Subscription: {subscription_text(sub)} as of {sub.total.as_of:%-d %b}")
    else:
        lines.append("Subscription: not available yet")
    return "\n".join(lines)


def send_daily_updates(today: date, store: Store, notifier: Notifier, market: MarketData | None,
                       verdict_labels: dict[str, str]) -> list[str]:
    """One update per analysed, still-open IPO per day; returns the ipo_ids whose update failed to send.
    An IPO analysed today already got its summary (with GMP and subscription), so it is skipped."""
    failed = []
    for ipo, verdict in store.due_updates(today):
        gmp, sub = _market(market, ipo)
        try:
            notifier.send_message(update_text(ipo, verdict_labels.get(verdict, "N/A"), today, gmp, sub))
        except Exception:
            log.exception("daily update send failed for %s", ipo.ipo_id)
            failed.append(ipo.ipo_id)
            continue
        store.mark_reminded(ipo.ipo_id, today)
    return failed
