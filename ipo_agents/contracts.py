"""Data contracts shared across the pipeline (spec: Data contracts)."""
from __future__ import annotations

from datetime import date
from typing import Literal

import math

from pydantic import BaseModel, Field, computed_field, model_validator


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


class SubscriptionStatus(BaseModel):
    """Times subscribed by investor category, each figure tied to its source and as-of date."""

    qib: ClaimedFact | None = None
    nii: ClaimedFact | None = None
    retail: ClaimedFact | None = None
    total: ClaimedFact


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


class Cited(BaseModel):
    """A short piece of text from the RHP and the page it is on (not checked by the verifier)."""

    text: str = Field(description="Short paraphrase or name, no more than one sentence")
    page: int = Field(ge=1, description="N from the nearest '[page N]' tag above the text")


COMMISSIONED_REPORT = "per company-commissioned industry report"


class RevenueShare(BaseModel):
    name: str = Field(description="Segment, geography or product, as the RHP names it")
    basis: Literal["segment", "geography", "product"]
    share: Fact = Field(description="Its revenue for the latest full fiscal year: % of revenue, or the amount, as printed")


class CapacityLine(BaseModel):
    facility: str = Field(description="Plant or product line")
    installed_capacity: Fact = Field(description="Installed capacity, with its unit as printed")
    utilisation_pct: Fact = Field(description="Capacity utilisation for the latest full fiscal year, %")


class Dependence(BaseModel):
    dependent: bool = Field(description="True only if the RHP says so: one plant, or one state/country for most revenue")
    evidence: Cited | None = Field(description="Where the RHP says it; null when dependent is false")


class IndustryClaim(BaseModel):
    """A market claim from the industry report, always shown as per the company-commissioned report."""

    claim: str = Field(description="The claim, paraphrased in one sentence")
    page: int = Field(ge=1)
    figure: Fact | None = Field(description="The claim's headline number (market size, growth %), if it has one")

    @computed_field
    @property
    def attribution(self) -> str:
        return COMMISSIONED_REPORT


class BusinessIndustry(BaseModel):
    """Business & Industry section (Our Business, Industry Overview)."""

    what_it_sells: str = Field(description="What the company sells and to whom, in one or two sentences")
    revenue_mix: list[RevenueShare] = Field(description="Revenue by segment, geography or product")
    top1_customer_pct: Fact = Field(description="Revenue from the largest customer, % of revenue, latest full fiscal year")
    top10_customers_pct: Fact = Field(description="Revenue from the top 10 customers, % of revenue, latest full fiscal year")
    top_supplier_pct: Fact = Field(description="Purchases from the largest supplier, % of purchases, latest full fiscal year")
    capacity: list[CapacityLine]
    order_book: Fact = Field(description="Order book, rupee amount, latest date stated")
    competitors: list[Cited] = Field(description="Competitors the RHP names, one per item")
    moats: list[Cited] = Field(description="Competitive strengths the company claims, a few words each")
    industry_claims: list[IndustryClaim] = Field(description="Key market claims from the industry report")
    single_plant: Dependence = Field(description="All or most manufacturing at a single plant")
    single_geography: Dependence = Field(description="Most revenue from a single state, region or country")


class Promoter(BaseModel):
    name: str
    background: str = Field(description="Education, experience and other businesses, in one or two sentences")
    page: int = Field(ge=1)


class GroupCompany(BaseModel):
    name: str
    same_line_of_business: bool = Field(
        description="True if the RHP says it is in the same or a similar line of business, or has common pursuits")
    page: int = Field(ge=1)


class Remuneration(BaseModel):
    name: str
    role: str = Field(description="e.g. Managing Director, Chief Financial Officer")
    is_promoter: bool = Field(description="True if this person is a named promoter")
    amount: Fact = Field(description="Total remuneration for the latest full fiscal year, rupee amount")


class Dividend(BaseModel):
    period: str = Field(description="Fiscal year or period, as written")
    per_share: Fact = Field(description="Dividend per equity share, ₹")


class PromotersGovernance(BaseModel):
    """Promoters & Governance section (Our Management, Our Promoters, Group Companies, History,
    Subsidiaries, Dividend Policy)."""

    promoters: list[Promoter]
    group_companies: list[GroupCompany]
    subsidiaries: list[Cited]
    remuneration: list[Remuneration] = Field(description="Each promoter, director and KMP paid in the latest full fiscal year")
    board_size: Fact = Field(description="Number of directors on the board")
    independent_directors: Fact = Field(description="Number of independent directors on the board")
    promoter_shares_pledged: Fact = Field(
        description="Promoter shares pledged or encumbered, number of shares or % as printed; 'Nil' is 0")
    dividends: list[Dividend] = Field(description="Dividends declared in the last three fiscal years and the stub period")
    auditor_changes: list[Cited] = Field(description="Each change of statutory auditor in the last three years")
    kmp_changes: list[Cited] = Field(description="Each change in key managerial personnel in the last three years")


class PeriodFact(BaseModel):
    period: str = Field(description="Fiscal year or period, as written, e.g. 'Fiscal 2024'")
    value: Fact


class Eps(BaseModel):
    period: str = Field(description="Fiscal year or period, as written")
    basic: Fact = Field(description="Basic EPS, ₹")
    diluted: Fact = Field(description="Diluted EPS, ₹")


class IndustryPe(BaseModel):
    highest: Fact
    lowest: Fact
    average: Fact


class Peer(BaseModel):
    """One listed peer, as the RHP's own comparison table states it (feeds peer valuation)."""

    name: str
    pe: Fact = Field(description="P/E, times")
    eps: Fact = Field(description="Basic EPS, ₹")
    ronw_pct: Fact = Field(description="Return on net worth, %")
    nav_per_share: Fact = Field(description="Net asset value per share, ₹")
    revenue: Fact = Field(description="Revenue from operations or total income, as the table labels it")


class Kpi(BaseModel):
    name: str = Field(description="KPI name as written")
    values: list[PeriodFact]


class Waca(BaseModel):
    """Weighted average cost of acquisition of one kind of recent share transaction."""

    waca: Fact = Field(description="WACA, ₹ per share")
    floor_price_times: Fact = Field(description="Floor price as a multiple of this WACA, if printed (often [●])")
    cap_price_times: Fact = Field(description="Cap price as a multiple of this WACA, if printed (often [●])")


class OfferPriceBasis(BaseModel):
    """Basis for Offer Price section."""

    eps: list[Eps] = Field(description="Basic and diluted EPS per period")
    nav_per_share: list[PeriodFact] = Field(description="Net asset value per equity share, ₹, per period")
    ronw_pct: list[PeriodFact] = Field(description="Return on net worth, %, per period")
    pe_at_floor: Fact = Field(description="P/E at the floor price on latest fiscal basic EPS (often [●])")
    pe_at_cap: Fact = Field(description="P/E at the cap price on latest fiscal basic EPS (often [●])")
    industry_pe: IndustryPe = Field(description="Industry (peer group) P/E: highest, lowest, average")
    peers: list[Peer] = Field(description="Listed peers in the RHP's comparison table, not the company itself")
    kpis: list[Kpi] = Field(description="Key performance indicators the section discloses")
    waca_primary: Waca = Field(description="Primary issuances (new shares) in the period the RHP reports")
    waca_secondary: Waca = Field(description="Secondary transactions (transfers) in the period the RHP reports")


class RedFlag(BaseModel):
    """A red flag raised by Python from verified facts (never by a model)."""

    section: str  # the section group that raised it
    code: str  # stable id, e.g. "customer_concentration"
    message: str
    pages: list[int] = []


class Derived(BaseModel):
    """A number computed in Python from verified facts; value None when it can't be computed."""

    name: str
    value: float | None
    unit: str
    note: str  # how it was computed, or why it couldn't be


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
    # Third-party market data (ticket 19); None means not available. GMP is unofficial and never drives the verdict.
    gmp: ClaimedFact | None = None
    subscription: SubscriptionStatus | None = None

    @property
    def claimed_numbers(self) -> list[ClaimedFact]:
        return [n for s in self.sources for n in s.claimed_numbers]


class Report(BaseModel):
    ipo: Ipo
    status: Literal["complete", "partial_budget"]
    verdict: Verdict | None
    offer: OfferObjects | None = None
    business: BusinessIndustry | None = None
    governance: PromotersGovernance | None = None
    price_basis: OfferPriceBasis | None = None
    sentiment: Sentiment | None = None
    unverified_fields: list[str] = []
    red_flags: list[RedFlag] = []
    derived: list[Derived] = []
    rhp_sha256: str
    pdf_path: str | None = None


class RunSummary(BaseModel):
    analysed: list[str] = []
    skipped: list[str] = []
    waiting_rhp: list[str] = []
    failed: list[str] = []
    notify_failed: list[str] = []
