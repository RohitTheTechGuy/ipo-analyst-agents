"""Fakes for the system's outer boundary only (spec: Testing Decisions)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping

from ipo_agents.adapters import SearchHit
from ipo_agents.config import load_config
from ipo_agents.contracts import Ipo
from ipo_agents.llm import LLMResult


def _configured_models():
    return load_config(Path(__file__).resolve().parent.parent / "config.yaml").models


@dataclass
class ScriptedLLM:
    """Returns a scripted object per schema plus token usage.

    `models` is the configured model table (default: the repo's config.yaml); a call naming
    any other model key fails, so a wrong routing key can't pass. `usage` scripts (input, cached, output) tokens per call, in
    order; calls beyond it use the defaults. A scripted response that is an exception is raised; one
    that is a function is called with the prompt's user text, so per-call answers can key on it.
    """

    responses: dict[type, object]
    models: Mapping[str, object] = field(default_factory=lambda: _configured_models())
    usage: list[tuple[int, int, int]] = field(default_factory=list)
    input_tokens: int = 1_000
    cached_tokens: int = 0
    output_tokens: int = 200
    calls: list[dict] = field(default_factory=list)

    def invoke_structured(self, *, model, system, user, schema, max_tokens, task=""):
        if model not in self.models:
            raise KeyError(f"{model!r} is not a configured model key")
        self.calls.append({"task": task, "model": model, "system": system, "user": user, "schema": schema, "max_tokens": max_tokens})
        response = self.responses[schema]
        if callable(response):
            response = response(user)
        if isinstance(response, Exception):
            raise response
        n = len(self.calls) - 1
        input_tokens, cached_tokens, output_tokens = (
            self.usage[n] if n < len(self.usage) else (self.input_tokens, self.cached_tokens, self.output_tokens)
        )
        return LLMResult(parsed=response, input_tokens=input_tokens,
                         cached_tokens=cached_tokens, output_tokens=output_tokens)


@dataclass
class FakeSearch:
    """Returns the scripted hits per exact query (none for others); `error` makes every search raise."""

    results: dict[str, list[SearchHit]] = field(default_factory=dict)
    error: Exception | None = None
    queries: list[dict] = field(default_factory=list)

    def search(self, query, *, max_results, published_after):
        self.queries.append({"query": query, "max_results": max_results, "published_after": published_after})
        if self.error is not None:
            raise self.error
        return list(self.results.get(query, []))[:max_results]


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
