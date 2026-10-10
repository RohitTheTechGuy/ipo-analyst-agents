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


class SellingShareholder(BaseModel):
    name: str
    shares_offered: Fact
    holding_after: Fact = Field(description="Holding after the offer, % or shares as written")


class ProceedsObject(BaseModel):
    """One object of the fresh issue, as named in Objects of the Offer."""

    purpose: str
    amount: Fact


class OfferObjects(BaseModel):
    """Offer & Objects section. Price band and lot size come from the exchange (Ipo), never from here."""

    issue_size: Fact = Field(description="Total offer size, rupee amount")
    fresh_issue: Fact = Field(description="Fresh issue, rupee amount")
    ofs_amount: Fact = Field(description="Offer for sale, rupee amount")
    ofs_shares: Fact = Field(description="Offer for sale, number of equity shares")
    pre_issue_shares: Fact = Field(description="Equity shares outstanding before the offer")
    post_issue_shares: Fact = Field(description="Equity shares outstanding after the offer")
    promoter_holding_pre_pct: Fact = Field(description="Promoter (and promoter group) holding before the offer, %")
    promoter_holding_post_pct: Fact = Field(description="Promoter (and promoter group) holding after the offer, %")
    objects: list[ProceedsObject] = Field(description="Each use of the fresh-issue proceeds, except GCP")
    gcp_amount: Fact = Field(description="General corporate purposes, rupee amount")
    anchor_pct: Fact = Field(description="Anchor investor portion, % of the QIB portion")
    qib_pct: Fact = Field(description="QIB portion, % of the offer")
    nii_pct: Fact = Field(description="Non-institutional portion, % of the offer")
    retail_pct: Fact = Field(description="Retail portion, % of the offer")
    selling_shareholders: list[SellingShareholder]


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
    waiting_rhp: list[str] = []
    failed: list[str] = []
    notify_failed: list[str] = []
