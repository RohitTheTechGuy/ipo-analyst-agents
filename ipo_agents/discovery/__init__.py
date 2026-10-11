"""Discovery of open mainboard IPOs from NSE, BSE and Chittorgarh, merged in that order."""
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
from ipo_agents.store import same_company

__all__ = ["BseSource", "ChittorgarhSource", "DiscoveryError", "FallbackExchange", "HttpRhpDownloader",
           "NseSource", "RhpDownloadError", "default_exchange", "make_client"]

log = logging.getLogger(__name__)


class DiscoveryError(Exception):
    """No source could list today's open IPOs. str() is a per-source summary for the alert."""


class FallbackExchange:
    """Asks every source and merges their answers: one source can list an IPO before the others do.

    An IPO listed by several sources (same company, same dates) is kept as the first source has it.
    Only when every source that answered found nothing is it a quiet day. If every source failed,
    raises DiscoveryError.
    """

    def __init__(self, sources: list[Source]):
        self._sources = sources

    def open_ipos(self, today: date) -> list[Ipo]:
        errors, answered, found = [], False, []
        for source in self._sources:
            try:
                ipos = source.open_ipos(today)
            except Exception as exc:
                log.warning("discovery source %s failed: %r", source.name, exc)
                errors.append(f"{source.name}: {describe(exc)}")
                continue
            answered = True
            found += [i for i in ipos if not any(_same_issue(i, f) for f in found)]
        if answered:
            return found
        raise DiscoveryError("; ".join(errors))


def _same_issue(a: Ipo, b: Ipo) -> bool:
    return a.open_date == b.open_date and a.close_date == b.close_date and same_company(a.name, b.name)


def default_exchange(client: httpx.Client, *, clock: Callable[[], float] = time.monotonic,
                     source_budget_s: float = SOURCE_BUDGET_S) -> FallbackExchange:
    kwargs = {"clock": clock, "budget_s": source_budget_s}
    return FallbackExchange([NseSource(client, **kwargs), BseSource(client, **kwargs),
                             ChittorgarhSource(client, **kwargs)])
