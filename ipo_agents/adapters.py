"""The system's outer boundary, swappable as a set (real or fake)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from ipo_agents.contracts import ClaimedFact, Ipo, PeerQuote, SubscriptionStatus
from ipo_agents.llm import LLM


class ExchangeSource(Protocol):
    def open_ipos(self, today: date) -> list[Ipo]: ...


class RhpDownloader(Protocol):
    def download(self, url: str, dest_dir: Path) -> Path: ...


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    source_name: str
    published: date | None  # None when the provider can't date it; such hits are never used
    content: str  # the page's text, so no separate fetch is needed


class WebSearch(Protocol):
    def search(self, query: str, *, max_results: int, published_after: date) -> list[SearchHit]: ...


GMP_LABEL = "GMP (unofficial)"  # grey-market premium: unregulated, never a basis for the verdict


class MarketData(Protocol):
    """Third-party market data for one IPO; None when the source has none."""

    def gmp(self, ipo: Ipo) -> ClaimedFact | None: ...
    def subscription(self, ipo: Ipo) -> SubscriptionStatus | None: ...


class PeerData(Protocol):
    """A listed peer's price, market cap and latest annual financials, by NSE symbol or company name.
    None when the peer can't be found or the source has nothing for it."""

    def quote(self, symbol_or_name: str) -> PeerQuote | None: ...


class Notifier(Protocol):
    def send_message(self, text: str) -> None: ...
    def send_document(self, path: Path, caption: str = "") -> None: ...


@dataclass
class Adapters:
    exchange: ExchangeSource
    downloader: RhpDownloader
    llm: LLM
    notifier: Notifier
    search: WebSearch | None = None  # None: sentiment is reported as unavailable
    market: MarketData | None = None  # None: GMP and subscription are reported as not available
    peers: PeerData | None = None  # None: peer valuation is not run (Report.valuation stays None)
