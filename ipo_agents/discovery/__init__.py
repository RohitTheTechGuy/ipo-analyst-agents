"""Discovery of open mainboard IPOs: NSE first, then BSE, then Chittorgarh."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date

import httpx

from ipo_agents.contracts import Ipo
from ipo_agents.discovery.bse import BseSource
from ipo_agents.discovery.chittorgarh import ChittorgarhSource
from ipo_agents.discovery.common import SOURCE_BUDGET_S, Source, describe, make_client
from ipo_agents.discovery.download import HttpRhpDownloader, RhpDownloadError
from ipo_agents.discovery.nse import NseSource

__all__ = ["BseSource", "ChittorgarhSource", "DiscoveryError", "FallbackExchange", "HttpRhpDownloader",
           "NseSource", "RhpDownloadError", "default_exchange", "make_client"]

log = logging.getLogger(__name__)


class DiscoveryError(Exception):
    """No source could list today's open IPOs. str() is a per-source summary for the alert."""


class FallbackExchange:
    """Asks each source in turn and returns the first non-empty answer.

    An empty answer moves on to the next source too: only when every source that answered
    found nothing is it a quiet day. If every source failed, raises DiscoveryError.
    """

    def __init__(self, sources: list[Source]):
        self._sources = sources

    def open_ipos(self, today: date) -> list[Ipo]:
        errors, answered = [], False
        for source in self._sources:
            try:
                ipos = source.open_ipos(today)
            except Exception as exc:
                log.warning("discovery source %s failed: %r", source.name, exc)
                errors.append(f"{source.name}: {describe(exc)}")
                continue
            if ipos:
                return ipos
            answered = True
        if answered:
            return []
        raise DiscoveryError("; ".join(errors))


def default_exchange(client: httpx.Client, *, clock: Callable[[], float] = time.monotonic,
                     source_budget_s: float = SOURCE_BUDGET_S) -> FallbackExchange:
    kwargs = {"clock": clock, "budget_s": source_budget_s}
    return FallbackExchange([NseSource(client, **kwargs), BseSource(client, **kwargs),
                             ChittorgarhSource(client, **kwargs)])
