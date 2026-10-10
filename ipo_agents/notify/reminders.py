"""Closing reminders for analysed IPOs (spec story 47). No model calls."""
from __future__ import annotations

import logging
from datetime import date

from ipo_agents.adapters import Notifier
from ipo_agents.store import Store

log = logging.getLogger(__name__)


def send_closing_reminders(today: date, store: Store, notifier: Notifier, verdict_labels: dict[str, str]) -> list[str]:
    """Send at most one reminder per IPO per day; returns the ipo_ids whose reminder failed to send."""
    failed = []
    for ipo_id, name, close_date, verdict in store.due_reminders(today):
        when = "TODAY" if close_date == today else "TOMORROW"
        text = f"⏰ {name} IPO closes {when}. Verdict was: {verdict_labels.get(verdict, 'N/A')}"
        try:
            notifier.send_message(text)
        except Exception:
            log.exception("reminder send failed for %s", ipo_id)
            failed.append(ipo_id)
            continue
        store.mark_reminded(ipo_id, today)
    return failed
