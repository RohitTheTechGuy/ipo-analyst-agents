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
    category: Literal["promoter", "investor", "other"] | None = Field(
        default=None, description="How the RHP classes this seller: Promoter, Investor (PE/VC) or Other "
                                  "Selling Shareholder")
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


class Quote(BaseModel):
    """Text (not a number) taken from the RHP. The verifier checks the quote is printed on the cited
    page; one it can't find comes back with page and verbatim_quote set to None."""

    page: int | None = Field(default=None, ge=1)
    verbatim_quote: str | None = Field(default=None, description="The words copied character for character from the page")


class FinancialPeriod(BaseModel):
    """One column of the restated financials: a fiscal year or a stub (interim) period."""

    label: str = Field(description="Period label as printed, e.g. 'Fiscal 2024' or 'Three months ended June 30, 2024'")
    end_date: str = Field(description="Period end date as printed, e.g. 'March 31, 2024'")
    kind: Literal["full_year", "stub"] = Field(description="'stub' for an interim period shorter than a full fiscal year")
    revenue_from_operations: Fact
    total_income: Fact = Field(description="Revenue from operations plus other income")
    ebitda: Fact = Field(description="EBITDA as disclosed; not disclosed if the RHP doesn't state it")
    pat: Fact = Field(description="Restated profit (or loss) after tax for the period; a loss is negative")
    cfo: Fact = Field(description="Net cash from / (used in) operating activities; cash used is negative")
    capex: Fact = Field(description="Purchase of property, plant and equipment (incl. intangibles if one line)")
    total_borrowings: Fact = Field(description="Total borrowings (current + non-current) at period end")
    cash_and_equivalents: Fact = Field(description="Cash and cash equivalents at period end")
    net_worth: Fact = Field(description="Net worth / total equity at period end")
    trade_receivables: Fact = Field(description="Trade receivables at period end")
    inventories: Fact = Field(description="Inventories at period end")
    trade_payables: Fact = Field(description="Trade payables at period end")
    ronw_pct: Fact = Field(description="Return on net worth / ROE, % as disclosed")
    roce_pct: Fact = Field(description="Return on capital employed, % as disclosed")


class AuditorRemark(BaseModel):
    """A qualification or emphasis-of-matter paragraph in the auditor's examination report."""

    kind: Literal["qualification", "emphasis_of_matter"]
    summary: str = Field(description="One line in plain words: what the auditor points to")
    source: Quote


class Restatement(BaseModel):
    adjusted: bool = Field(description="True if the restated figures differ from the audited ones (any non-nil restatement adjustment)")
    source: Quote


class Financials(BaseModel):
    """Financials section: 3 fiscal years plus any stub period. Flags are computed in Python (fin_calc)."""

    periods: list[FinancialPeriod] = Field(description="One entry per period column: each fiscal year and any stub period")
    auditor_remarks: list[AuditorRemark] = Field(description="Every auditor qualification and emphasis of matter; empty if none")
    restatement: Restatement | None = Field(description="Whether restatement adjustments were made; None if not stated")


TOP_RISKS = 10


class RiskFactor(BaseModel):
    """One risk factor, compact: its heading as a Quote, not the whole paragraph."""

    topic: str = Field(description="A few words naming the risk, e.g. 'single plant dependence'")
    title: Quote = Field(description="The risk factor's heading, copied verbatim (its first sentence if very long)")
    kind: Literal["company_specific", "generic"] = Field(
        description="'generic' for boilerplate that could sit in any RHP (economy, politics, the market, the "
                    "equity shares); 'company_specific' for risks about this company's own business")
    quantified: bool = Field(description="True if the risk states a number about this company")
    figure: Fact | None = Field(default=None, description="The main number the risk states, if quantified")
    severity_rank: int | None = Field(default=None, ge=1, description="Severity rank among the company-specific "
                                                                      "risks, 1 = most severe; null for generic")


class RiskFactors(BaseModel):
    """Risk Factors section: every risk factor, classified and ranked."""

    risks: list[RiskFactor] = Field(description="Every risk factor in the section, in the order printed")

    @property
    def top_company_specific(self) -> list[RiskFactor]:
        """Up to TOP_RISKS company-specific risks by severity rank; a risk whose title failed verification
        or that has no rank is left out."""
        ranked = [r for r in self.risks if r.kind == "company_specific" and r.severity_rank is not None
                  and r.title.page is not None]
        return sorted(ranked, key=lambda r: r.severity_rank)[:TOP_RISKS]

    # TODO(ticket 20, synthesizer): cross-check risks that cite a number (RiskFactor.figure) against the
    # same number in Fine Print litigation / contingent liabilities and in Financials; flag mismatches.


class LitigationLine(BaseModel):
    """Outstanding litigation for one party, type and direction, as summarised in the RHP."""

    party: Literal["company", "promoters", "directors", "subsidiaries"]
    case_type: Literal["criminal", "tax", "regulatory", "civil", "other"] = Field(
        description="'regulatory' for actions by statutory or regulatory authorities (incl. disciplinary); "
                    "'civil' for material civil litigation")
    direction: Literal["against", "by"] = Field(description="Proceedings against the party, or by it")
    count: Fact = Field(description="Number of cases")
    amount: Fact = Field(description="Aggregate amount involved, where stated")


class RelatedPartyLine(BaseModel):
    """One related-party transaction amount for one counterparty, category and period."""

    category: Literal["sales", "purchases", "loans", "guarantees", "rent", "remuneration", "other"]
    counterparty: str = Field(description="The related party as named")
    promoter_entity: bool = Field(description="True if the counterparty is a promoter, a promoter group entity "
                                              "or a group company")
    period: str = Field(description="Period as printed, e.g. 'Fiscal 2024'")
    amount: Fact


class PendingApproval(BaseModel):
    name: str = Field(description="The approval or licence, e.g. 'Consent to operate, Pune plant'")
    status: Literal["pending", "expired"]
    critical: bool = Field(description="True if the business can't run its plant, product or main activity without it")
    source: Quote


class FinePrintLegal(BaseModel):
    """Fine Print & Legal section. Checks are computed in Python (legal_calc)."""

    litigation: list[LitigationLine] = Field(description="One line per party, case type and direction stated")
    contingent_liabilities: Fact = Field(description="Total contingent liabilities not provided for, at the latest "
                                                     "date; if no total is printed, claims not acknowledged as debts")
    related_party: list[RelatedPartyLine] = Field(description="Related-party transactions of the latest full fiscal "
                                                              "year, one line per counterparty and category; no total lines")
    approvals: list[PendingApproval] = Field(description="Approvals and licences that are pending, applied for, or expired")
    material_contracts: list[Quote] = Field(description="Material contracts with promoters or promoter group entities")


class FinancialCheck(BaseModel):
    """A financial red-flag check Python computed from verified facts. A check whose inputs aren't
    all verified is 'not_computable', never guessed."""

    code: str
    status: Literal["raised", "clear", "not_computable"]
    detail: str


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


class CalcInput(BaseModel):
    """One input of a derived number: a Fact's dotted path and page, or exchange data (page None)."""

    field: str  # e.g. "offer_objects.ofs_shares" or "ipo.price_band"
    page: int | None = None


class Derived(BaseModel):
    """A number computed in Python (calc.py) from verified inputs; None when not computable."""

    value: float | None
    unit: str
    inputs: list[CalcInput] = []
    basis: str = ""  # e.g. "computed at upper band ₹105"
    not_computable_reason: str | None = None  # e.g. "offer_objects.ofs_shares: could not verify"

    @property
    def pages(self) -> list[int]:
        return sorted({i.page for i in self.inputs if i.page is not None})


class Flag(BaseModel):
    code: str  # e.g. "OFS_HEAVY"
    severity: Literal["info", "warning", "error"]
    message: str
    evidence_pages: list[int] = []


class Report(BaseModel):
    ipo: Ipo
    status: Literal["complete", "partial_budget"]
    verdict: Verdict | None
    offer: OfferObjects | None = None
    financials: Financials | None = None
    financial_flags: list[FinancialCheck] = []
    risk_factors: RiskFactors | None = None
    fine_print_legal: FinePrintLegal | None = None
    legal_checks: list[FinancialCheck] = []  # legal_calc.py
    sentiment: Sentiment | None = None
    calculations: dict[str, Derived] = {}  # name -> derived number (calc.py)
    flags: list[Flag] = []  # red flags (flags.py)
    unverified_fields: list[str] = []
    rhp_sha256: str
    pdf_path: str | None = None


class RunSummary(BaseModel):
    analysed: list[str] = []
    skipped: list[str] = []
    waiting_rhp: list[str] = []
    failed: list[str] = []
    notify_failed: list[str] = []
