"""The system's outer boundary, swappable as a set (real or fake)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from ipo_agents.contracts import Ipo
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
