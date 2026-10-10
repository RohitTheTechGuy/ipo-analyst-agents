"""Data contracts shared across the pipeline (spec: Data contracts)."""
from __future__ import annotations

from datetime import date
from typing import Literal

import math

from pydantic import BaseModel, Field, model_validator


class Fact(BaseModel):
    """A number taken from the RHP. No citation, no number."""

    value: float | None
    unit: str
    page: int | None = Field(ge=1)
    verbatim_quote: str | None
    not_disclosed_reason: str | None = None

    @model_validator(mode="after")
    def _cited_or_not_disclosed(self):
        if self.value is None:
            if not self.not_disclosed_reason:
                raise ValueError("a null value needs not_disclosed_reason")
        elif not math.isfinite(self.value):
            raise ValueError("value must be finite")
        elif self.page is None or not self.verbatim_quote:
            raise ValueError("a value needs page and verbatim_quote")
        return self


class ClaimedFact(BaseModel):
    """A number claimed by a third party; never mixed with RHP facts."""

    value: float | None
    unit: str
    source_url: str
    source_name: str
    as_of: date
    label: str | None = None  # what the number is, e.g. "GMP"; rendered "claimed by <source_name>"


class Ipo(BaseModel):
    ipo_id: str
    name: str
    symbol: str
    open_date: date
    close_date: date
    price_band: str
    lot_size: int
    rhp_url: str | None = None
    is_draft: bool = False


class Chunk(BaseModel):
    section: str
    page_start: int
    page_end: int
    text: str


class SectionedDocument(BaseModel):
    page_count: int
    sha256: str
    sections: dict[str, list[Chunk]]
    boilerplate_pages: list[int] = []

    @property
    def coverage(self) -> float:
        covered = {p for s in self.sections.values() for c in s for p in range(c.page_start, c.page_end + 1)}
        covered |= set(self.boilerplate_pages)
        covered &= set(range(1, self.page_count + 1))
        return len(covered) / self.page_count if self.page_count else 0.0


class OfferObjects(BaseModel):
    issue_size_cr: Fact


Verdict = Literal["subscribe_long_term", "subscribe_listing_gains", "neutral", "avoid"]


Stance = Literal["subscribe", "avoid", "neutral", "listing_gains_only"]
STANCES: tuple[Stance, ...] = ("subscribe", "listing_gains_only", "avoid", "neutral")


class SentimentSource(BaseModel):
    """One dated third-party article or broker note and the stance it takes."""

    url: str
    title: str
    source_name: str
    published: date
    stance: Stance
    key_reasons: list[str] = Field(default=[], max_length=3)  # paraphrased
    claimed_numbers: list[ClaimedFact] = []


class Sentiment(BaseModel):
    """Market view from public sources. Its numbers are ClaimedFacts only, never RHP Facts."""

    available: bool
    note: str = ""  # e.g. "sentiment unavailable: ..." or how the RHP date was approximated
    sources: list[SentimentSource] = []
    tally: dict[Stance, int] = {}
    bull_points: list[str] = []
    bear_points: list[str] = []

    @property
    def claimed_numbers(self) -> list[ClaimedFact]:
        return [n for s in self.sources for n in s.claimed_numbers]


class Report(BaseModel):
    ipo: Ipo
    status: Literal["complete", "partial_budget"]
    verdict: Verdict | None
    offer: OfferObjects | None = None
    sentiment: Sentiment | None = None
    unverified_fields: list[str] = []
    rhp_sha256: str
    pdf_path: str | None = None


class RunSummary(BaseModel):
    analysed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    notify_failed: list[str] = []
