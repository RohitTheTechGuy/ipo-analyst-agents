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


class Notifier(Protocol):
    def send_message(self, text: str) -> None: ...
    def send_document(self, path: Path, caption: str = "") -> None: ...


@dataclass
class Adapters:
    exchange: ExchangeSource
    downloader: RhpDownloader
    llm: LLM
    notifier: Notifier
