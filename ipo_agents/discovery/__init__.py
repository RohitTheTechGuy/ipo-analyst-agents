"""Discovery of open mainboard IPOs: NSE first, then BSE, then Chittorgarh."""
from __future__ import annotations

import logging
from datetime import date

import httpx

from ipo_agents.adapters import ExchangeSource
from ipo_agents.contracts import Ipo
from ipo_agents.discovery.bse import BseSource
from ipo_agents.discovery.chittorgarh import ChittorgarhSource
from ipo_agents.discovery.common import make_client
from ipo_agents.discovery.download import HttpRhpDownloader, RhpDownloadError
from ipo_agents.discovery.nse import NseSource

__all__ = ["BseSource", "ChittorgarhSource", "DiscoveryError", "FallbackExchange", "HttpRhpDownloader", "NseSource",
           "RhpDownloadError", "default_exchange", "make_client"]

log = logging.getLogger(__name__)


class DiscoveryError(Exception):
    """No source could list today's open IPOs."""


class FallbackExchange:
    """Asks each source in turn; the first that answers wins (an empty answer counts)."""

    def __init__(self, sources: list[ExchangeSource]):
        self._sources = sources

    def open_ipos(self, today: date) -> list[Ipo]:
        errors = []
        for source in self._sources:
            try:
                return source.open_ipos(today)
            except Exception as exc:
                log.warning("discovery source %s failed: %r", type(source).__name__, exc)
                errors.append(f"{type(source).__name__}: {exc!r}")
        raise DiscoveryError("; ".join(errors))


def default_exchange(client: httpx.Client) -> FallbackExchange:
    return FallbackExchange([NseSource(client), BseSource(client), ChittorgarhSource(client)])
