"""The watch list: companies whose offer document is not public yet.

A confidential draft is not something this system can analyse: every fact in a report has to be quoted
from the RHP with a page number. So a watched company is checked once per daily run (never by the
every-few-minute picks job) and one Telegram line goes out when its state changes: on the IPO calendar,
a draft filed, or the final RHP published. No model call and no spend; the analysis still waits for the
user to name the IPO.
"""
from __future__ import annotations

import logging
from datetime import date

from ipo_agents.adapters import Adapters
from ipo_agents.config import Config, WatchItem
from ipo_agents.discovery.common import ListedIssue
from ipo_agents.store import Store

log = logging.getLogger(__name__)

STATE_KEY = "watch:{}"


def best(hits: list[ListedIssue]) -> ListedIssue | None:
    """The most advanced of today's rows for one company: the final RHP beats dates, which beat a draft."""
    def rank(hit: ListedIssue) -> tuple[bool, bool, bool]:
        return (hit.rhp_url is not None, hit.open_date is not None, hit.draft_url is not None)
    return max(hits, key=rank) if hits else None


def _fingerprint(hit: ListedIssue | None) -> str:
    """What was told, so the same news is not sent twice: the dates and the document links."""
    if hit is None:
        return "absent"
    return "|".join([
        hit.open_date.isoformat() if hit.open_date else "",
        hit.close_date.isoformat() if hit.close_date else "",
        hit.rhp_url or "",
        hit.draft_url or "",
    ])


def _dates(hit: ListedIssue) -> str:
    if hit.open_date and hit.close_date:
        return f"{hit.open_date:%-d %b}–{hit.close_date:%-d %b}"
    if hit.open_date:
        return f"from {hit.open_date:%-d %b}"
    return ""


def message(item: WatchItem, hit: ListedIssue | None) -> str | None:
    """The one message a change deserves; None while the company is not listed at all (silence is normal)."""
    if hit is None:
        return None  # nothing to say: the source has not listed the company yet
    if hit.rhp_url:
        lines = [f"👀 {hit.name}: the final RHP is public"]
        if dates := _dates(hit):
            lines.append(f"Bidding {dates}")
        # Only an open IPO can be picked, so point at the daily list rather than at a reply that would
        # be read as an RHP question (four words or more) or not understood ("analyse" is no pick word).
        lines += [hit.rhp_url,
                  "Nothing analysed, nothing spent. Once bidding opens it appears in the 10:30 list: "
                  "reply with its number or symbol there to analyse it."]
        return "\n".join(lines)
    if dates := _dates(hit):
        return (f"👀 {hit.name} is on the IPO calendar: {dates}\n"
                "No final RHP linked yet; the analysis starts only once it is public.")
    if hit.draft_url:
        return (f"👀 {hit.name}: a draft RHP is filed, the final RHP is not public yet.\n"
                "A draft is never analysed; I'll tell you when the final RHP appears.")
    return f"👀 {hit.name} now appears in the IPO list, with no dates or RHP yet."


def state_text(hit: ListedIssue | None) -> str:
    """A short line for the dry run, where no message is sent."""
    if hit is None:
        return "not listed by the sources yet"
    if hit.rhp_url:
        return f"final RHP public: {hit.rhp_url}"
    if dates := _dates(hit):
        return f"on the calendar ({dates}), no final RHP yet"
    if hit.draft_url:
        return "draft RHP filed, no final RHP yet"
    return "listed, no dates or RHP yet"


def check(today: date, adapters: Adapters, config: Config,
          store: Store) -> tuple[list[str], list[str], list[str]]:
    """(names told about, names whose check failed, names whose message failed to send): at most one
    message per change, per day.

    A failed check or send leaves the stored state untouched, so the next run tries again."""
    find = getattr(adapters.exchange, "listed", None)
    if not config.watch or find is None:
        return [], [], []
    sent, check_failed, failed = [], [], []
    for item in config.watch:
        terms = [item.name, *item.aliases]
        try:
            hit = best(find(terms, today))
        except Exception:
            log.exception("watch check failed for %s", item.name)
            check_failed.append(item.name)
            continue
        key = STATE_KEY.format(item.name.casefold())
        state = _fingerprint(hit)
        if store.state(key) == state:
            continue
        text = message(item, hit)
        if text is not None:
            try:
                adapters.notifier.send_message(text)
            except Exception:
                log.exception("watch message failed for %s", item.name)
                failed.append(item.name)
                continue
            sent.append(item.name)
        store.set_state(key, state)
    return sent, check_failed, failed
