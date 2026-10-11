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
# Risk factors listed at most: every quantified one, then the most severe company-specific ones. A long RHP
# prints 60-90; listing them all overruns the output limit, and the report shows only the top TOP_RISKS.
MAX_LISTED_RISKS = 25


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
    """Risk Factors section: the quantified and most severe company-specific risks (at most
    MAX_LISTED_RISKS), classified and ranked, plus counts of all the risk factors printed."""

    risks: list[RiskFactor] = Field(
        description=f"At most {MAX_LISTED_RISKS}, in the order printed: every quantified risk, then the most "
                    "severe company-specific ones. Generic risks are counted, not listed")
    # Counted by the model, not printed in the RHP: shown as counts only, never used in a calculation.
    total_count: int | None = Field(default=None, ge=0,
                                    description="How many risk factors the section prints in all, listed or not")
    generic_count: int | None = Field(default=None, ge=0,
                                      description="How many of them are generic boilerplate")

    @model_validator(mode="after")
    def _cap(self):
        """Enforces MAX_LISTED_RISKS on a reply that lists more: keeps every quantified risk, then the
        company-specific ones by severity rank (unranked, then generic, last), in the order printed. The
        counts, if the model gave none, are taken from the full list first."""
        if len(self.risks) <= MAX_LISTED_RISKS:
            return self
        if self.total_count is None:
            self.total_count = len(self.risks)
        if self.generic_count is None:
            self.generic_count = sum(r.kind == "generic" for r in self.risks)

        def priority(i: int) -> tuple:
            r = self.risks[i]
            return (not r.quantified, r.kind != "company_specific", r.severity_rank is None,
                    r.severity_rank or 0, i)

        keep = sorted(sorted(range(len(self.risks)), key=priority)[:MAX_LISTED_RISKS])
        self.risks = [self.risks[i] for i in keep]
        return self

    @property
    def n_risks(self) -> int:
        """Risk factors printed in all: the model's count, else the number listed."""
        return self.total_count if self.total_count is not None else len(self.risks)

    @property
    def n_generic(self) -> int:
        """Generic risk factors printed: the model's count, else the number listed."""
        return self.generic_count if self.generic_count is not None else sum(r.kind == "generic" for r in self.risks)

    @property
    def top_company_specific(self) -> list[RiskFactor]:
        """Up to TOP_RISKS company-specific risks by severity rank; a risk whose title failed verification
        or that has no rank is left out."""
        ranked = [r for r in self.risks if r.kind == "company_specific" and r.severity_rank is not None
                  and r.title.page is not None]
        return sorted(ranked, key=lambda r: r.severity_rank)[:TOP_RISKS]

    # A quantified risk whose figure is also printed in Financials or Fine Print litigation is marked
    # "corroborated" by cross_checks.py (risk_figures_corroborated).


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


class CitedText(BaseModel):
    """A short text (a name, a claim) and the RHP words that support it; the verifier checks the quote."""

    text: str = Field(description="Short paraphrase or name, no more than one sentence")
    source: Quote


# --- Business & Industry --------------------------------------------------------------------

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
    evidence: Quote | None = Field(description="The RHP words that say so; null when dependent is false")


class IndustryClaim(BaseModel):
    """A market claim from the industry report, always shown as per the company-commissioned report."""

    claim: str = Field(description="The claim, paraphrased in one sentence")
    source: Quote
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
    competitors: list[CitedText] = Field(description="Competitors the RHP names, one per item")
    moats: list[CitedText] = Field(description="Competitive strengths the company claims, a few words each")
    industry_claims: list[IndustryClaim] = Field(description="Key market claims from the industry report")
    single_plant: Dependence = Field(description="All or most manufacturing at a single plant")
    single_geography: Dependence = Field(description="Most revenue from a single state, region or country")


# --- Promoters & Governance -----------------------------------------------------------------

class Promoter(BaseModel):
    name: str
    background: str = Field(description="Education, experience and other businesses, in one or two sentences")
    source: Quote


class GroupCompany(BaseModel):
    name: str
    same_line_of_business: bool = Field(
        description="True if the RHP says it is in the same or a similar line of business, or has common pursuits")
    source: Quote = Field(description="The RHP words naming it (and, if so, saying it is in the same line of business)")


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
    subsidiaries: list[CitedText]
    remuneration: list[Remuneration] = Field(description="Each promoter, director and KMP paid in the latest full fiscal year")
    board_size: Fact = Field(description="Number of directors on the board")
    independent_directors: Fact = Field(description="Number of independent directors on the board")
    promoter_shares_pledged: Fact = Field(
        description="Promoter shares pledged or encumbered, number of shares or % as printed; 'Nil' is 0")
    dividends: list[Dividend] = Field(description="Dividends declared in the last three fiscal years and the stub period")
    auditor_changes: list[CitedText] = Field(description="Each change of statutory auditor in the last three years")
    kmp_changes: list[CitedText] = Field(description="Each change in key managerial personnel in the last three years")


# --- Offer Price Basis ----------------------------------------------------------------------

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


# --- Other Disclosures ----------------------------------------------------------------------

class MaterialItem(BaseModel):
    summary: str = Field(description="What the RHP discloses, in one or two sentences")
    why_material: str = Field(description="Why an investor should know it, in one sentence")
    source: Quote
    figures: list[Fact] = Field(description="Any numbers the item cites; empty if none")


class OtherDisclosures(BaseModel):
    """Every page no other section reads (General Information, Key Regulations, Offer Procedure...)."""

    items: list[MaterialItem] = Field(description="Material items only; empty when nothing is material")

    @computed_field
    @property
    def nothing_material(self) -> bool:
        return not self.items


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
    """One input of a derived number: a Fact's dotted path and page, exchange data (page None), or a
    third-party number (page None; `source` names who claims it and as of when)."""

    field: str  # e.g. "offer_objects.ofs_shares" or "ipo.price_band"
    page: int | None = None
    source: str | None = None  # e.g. "NSE, as of 2026-10-09" for a peer's market cap


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
    section: str | None = None  # the section group whose facts raised it


# --- Peer valuation -------------------------------------------------------------------------

Multiple = Literal["pe", "ev_ebitda", "pb", "ps"]
MULTIPLE_LABELS: dict[str, str] = {"pe": "P/E", "ev_ebitda": "EV/EBITDA", "pb": "P/B", "ps": "P/S"}


class PeerQuote(BaseModel):
    """A listed peer's market data and latest annual financials, each a third-party claim with its source
    and as-of date. Rupee amounts are in ₹ crore. A number the source didn't give is None: missing, never
    filled in."""

    name: str  # as asked for (the RHP's or the agent's name for the peer)
    symbol: str  # the NSE symbol it resolved to
    price: ClaimedFact | None = None  # ₹ per share
    market_cap: ClaimedFact | None = None
    revenue: ClaimedFact | None = None  # latest fiscal year
    revenue_prior_year: ClaimedFact | None = None  # the fiscal year before it, for growth
    ebitda: ClaimedFact | None = None
    pat: ClaimedFact | None = None
    net_worth: ClaimedFact | None = None
    debt: ClaimedFact | None = None
    cash: ClaimedFact | None = None


class MultipleChoice(BaseModel):
    multiple: Multiple
    reason: str = Field(description="Why this multiple fits this business, in one sentence")


class SuggestedPeer(BaseModel):
    name: str = Field(description="An NSE-listed company, its full name")
    reason: str = Field(description="Why it is comparable, in one sentence")


class ValuationPlan(BaseModel):
    """The peer valuation agent's choices. It names multiples and peers only; Python computes every number."""

    multiples_to_use: list[MultipleChoice] = Field(
        description="The multiples that fit this business, most important first: pb (read with RoE) for "
                    "lenders/NBFCs, ev_ebitda for capex-heavy businesses, ps for loss-makers, pe for steady earners")
    additional_peers: list[SuggestedPeer] = Field(
        description="Up to 3 more NSE-listed peers the RHP's table leaves out; empty if the table is complete")


class PeerRow(BaseModel):
    name: str
    origin: Literal["rhp", "agent_suggested"]
    reason: str | None = None  # why the agent suggested it
    quote: PeerQuote | None = None
    multiples: dict[str, Derived] = {}  # multiple -> value; with P/B also "roe_pct"
    unavailable: str | None = None  # "data unavailable: ..." when no quote could be fetched


class MultipleStats(BaseModel):
    """One chosen multiple: the IPO at the upper band against the peer set's median, min and max."""

    multiple: Multiple
    reason: str
    ipo: Derived
    median: Derived
    min: Derived
    max: Derived
    premium_pct: Derived  # IPO vs the peer median; negative is a discount


class Valuation(BaseModel):
    """Peer valuation (plan 5.3). The model picked the multiples and suggested peers; Python did the maths."""

    ipo_market_cap: Derived  # ₹ crore, at the upper band
    multiples: list[MultipleStats] = []
    rhp_peers: list[PeerRow] = []
    suggested_peers: list[PeerRow] = []  # shown separately, as "agent-suggested"
    peer_set: Literal["rhp", "agent_suggested"] = "rhp"  # whose multiples the median/min/max use
    checks: dict[str, Derived] = {}  # revenue growth and PAT margin, IPO vs peer median (VALUATION_RICH)
    read: str = ""  # one paragraph, from a Python template

    @property
    def headline(self) -> MultipleStats | None:
        """The first chosen multiple whose premium could be computed."""
        return next((m for m in self.multiples if m.premium_pct.value is not None), None)


# --- Synthesizer ----------------------------------------------------------------------------

Confidence = Literal["low", "medium", "high"]


class EvidencedPoint(BaseModel):
    text: str = Field(description="One sentence. Any number in it must be copied from the items it cites")
    evidence: list[str] = Field(description="IDs of the digest items it rests on, e.g. ['D:ofs_pct', 'G:OFS_HEAVY']")


class SectionSummary(BaseModel):
    section: str = Field(description="The section's key in the digest, e.g. 'offer_objects'")
    text: str = Field(description="One short paragraph; any number in it must be copied from the digest")


class Synthesis(BaseModel):
    """The synthesizer's answer. Python then checks every cited ID and number (synthesizer.py)."""

    verdict: Verdict
    confidence: Confidence
    reasons: list[EvidencedPoint] = Field(description="The 3-5 reasons that drove the verdict, most important first")
    top_red_flags: list[EvidencedPoint] = Field(description="Up to 5 red flags, most serious first")
    top_positives: list[EvidencedPoint] = Field(description="Up to 5 positives, strongest first")
    section_summaries: list[SectionSummary] = Field(description="One short paragraph per section in the digest")


class CheckedSynthesis(Synthesis):
    """A Synthesis after Python's checks: what was dropped or downgraded, and why, is in `notes`."""

    notes: list[str] = []


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
    cross_checks: list[FinancialCheck] = []  # cross_checks.py: consistency between sections
    business: BusinessIndustry | None = None
    governance: PromotersGovernance | None = None
    price_basis: OfferPriceBasis | None = None
    other: OtherDisclosures | None = None
    sentiment: Sentiment | None = None
    valuation: Valuation | None = None  # None: no peer data source, or the run stopped before it
    calculations: dict[str, Derived] = {}  # name -> derived number (calc.py, section_calc.py)
    flags: list[Flag] = []  # red flags (flags.py, section_calc.py)
    unverified_fields: list[str] = []
    synthesis: CheckedSynthesis | None = None  # None: the synthesizer did not run (budget stop, nothing verified)
    rhp_sha256: str
    pdf_path: str | None = None


class RunSummary(BaseModel):
    analysed: list[str] = []
    skipped: list[str] = []
    waiting_rhp: list[str] = []
    failed: list[str] = []
    notify_failed: list[str] = []
