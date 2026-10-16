"""Stopping a running analysis from Telegram ("stop" or "cancel" while the picks job analyses).

The picks job watches Telegram in a background thread while it analyses (notify/picks.py). A stop reply
calls request(); every model call checks first (budget.MeteredLLM) and raises Cancelled, so the analysis
stops at its next call, within about a poll interval. Progress up to there is checkpointed: picking the IPO
again resumes it. Spend so far stays on its ledger.

One process-wide flag: the picks job runs one analysis run at a time (launchd never starts a second copy).
"""
from __future__ import annotations

import threading

_requested = threading.Event()


class Cancelled(BaseException):
    """A stop asked for from Telegram. A BaseException, like KeyboardInterrupt, so the fail-soft
    `except Exception` handlers (sentiment, advisor, summary...) never swallow it."""


def request() -> None:
    _requested.set()


def clear() -> None:
    _requested.clear()


def requested() -> bool:
    return _requested.is_set()


def check() -> None:
    if _requested.is_set():
        raise Cancelled("stop requested from Telegram")
