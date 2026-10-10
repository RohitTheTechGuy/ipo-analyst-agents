"""Fakes for the system's outer boundary only (spec: Testing Decisions)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ipo_agents.contracts import Ipo
from ipo_agents.llm import LLMResult


@dataclass
class ScriptedLLM:
    """Returns a scripted object per schema, with fixed token usage."""

    responses: dict[type, object]
    input_tokens: int = 1_000
    output_tokens: int = 200
    calls: list[dict] = field(default_factory=list)

    def invoke_structured(self, *, model, system, user, schema, max_tokens, task=""):
        self.calls.append({"task": task, "model": model, "system": system, "user": user, "schema": schema, "max_tokens": max_tokens})
        return LLMResult(
            parsed=self.responses[schema],
            input_tokens=self.input_tokens,
            cached_tokens=0,
            output_tokens=self.output_tokens,
        )


@dataclass
class FakeExchange:
    ipos: list[Ipo]

    def open_ipos(self, today):
        return list(self.ipos)


@dataclass
class FakeDownloader:
    pdf: Path

    def download(self, url, dest_dir):
        return self.pdf


@dataclass
class RecordingNotifier:
    messages: list[str] = field(default_factory=list)
    documents: list[Path] = field(default_factory=list)

    def send_message(self, text):
        self.messages.append(text)

    def send_document(self, path, caption=""):
        self.documents.append(Path(path))


def make_ipo() -> Ipo:
    return Ipo(ipo_id="ACME-2026-10-13", name="Acme Ltd", symbol="ACME",
               open_date=date(2026, 10, 13), close_date=date(2026, 10, 15),
               price_band="₹100–₹105", lot_size=142, rhp_url="https://example.test/rhp.pdf")
