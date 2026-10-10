"""Fakes for the system's outer boundary only (spec: Testing Decisions)."""
from __future__ import annotations

import typing
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel

from ipo_agents.adapters import SearchHit
from ipo_agents.config import load_config
from ipo_agents.contracts import ClaimedFact, Fact, Ipo, OfferObjects, SubscriptionStatus
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
    `errors` scripts a failure per call, in order (None = that call succeeds). Every call, failed
    or not, is recorded in `calls` with the model key it was sent to. A schema with no scripted
    response gets `found_nothing(schema)`, so a test scripts only the sections it is about.
    """

    responses: dict[type, object]
    models: Mapping[str, object] = field(default_factory=lambda: _configured_models())
    usage: list[tuple[int, int, int]] = field(default_factory=list)
    errors: list[Exception | None] = field(default_factory=list)
    input_tokens: int = 1_000
    cached_tokens: int = 0
    output_tokens: int = 200
    calls: list[dict] = field(default_factory=list)

    def invoke_structured(self, *, model, system, user, schema, max_tokens, task=""):
        if model not in self.models:
            raise KeyError(f"{model!r} is not a configured model key")
        self.calls.append({"task": task, "model": model, "system": system, "user": user, "schema": schema, "max_tokens": max_tokens})
        n = len(self.calls) - 1
        if n < len(self.errors) and self.errors[n] is not None:
            raise self.errors[n]
        response = self.responses[schema] if schema in self.responses else found_nothing(schema)
        if callable(response):
            response = response(user)
        if isinstance(response, Exception):
            raise response
        input_tokens, cached_tokens, output_tokens = (
            self.usage[n] if n < len(self.usage) else (self.input_tokens, self.cached_tokens, self.output_tokens)
        )
        return LLMResult(parsed=response, input_tokens=input_tokens,
                         cached_tokens=cached_tokens, output_tokens=output_tokens)


class ProviderError(Exception):
    """A provider HTTP error; like openai's and groq's APIStatusError it carries `status_code`."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


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
class FakeMarketData:
    """Returns the scripted GMP and subscription (None: not available); `error` makes every call raise."""

    gmp_fact: ClaimedFact | None = None
    subscription_status: SubscriptionStatus | None = None
    error: Exception | None = None

    def gmp(self, ipo):
        if self.error is not None:
            raise self.error
        return self.gmp_fact

    def subscription(self, ipo):
        if self.error is not None:
            raise self.error
        return self.subscription_status


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


NOT_DISCLOSED = Fact(value=None, unit="", page=None, verbatim_quote=None,
                     not_disclosed_reason="not disclosed in provided pages")


def found_nothing(schema: type[BaseModel]) -> BaseModel:
    """What a model that found nothing in its pages returns: every Fact not disclosed, every list
    empty, every optional field None."""
    def empty(f):
        if f.annotation is Fact:
            return NOT_DISCLOSED
        if typing.get_origin(f.annotation) is list:
            return []
        if type(None) in typing.get_args(f.annotation):
            return None
        raise TypeError(f"{schema.__name__}: no empty value for {f.annotation}; script this schema")
    return schema(**{name: empty(f) for name, f in schema.model_fields.items()})


def offer_objects(**fields) -> OfferObjects:
    """An OfferObjects with the given fields; every other fact not disclosed and every list empty."""
    defaults = {name: NOT_DISCLOSED if f.annotation is Fact else [] for name, f in OfferObjects.model_fields.items()}
    return OfferObjects(**{**defaults, **fields})
