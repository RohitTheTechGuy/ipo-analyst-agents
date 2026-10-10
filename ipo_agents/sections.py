"""Section agent registry: one SectionSpec (prompt + schema) per section group. The graph runs every entry."""
from __future__ import annotations

from ipo_agents.contracts import BusinessIndustry, OfferObjects, OfferPriceBasis, PromotersGovernance
from ipo_agents.section_agent import SectionSpec

OFFER_OBJECTS_PROMPT = (
    "You extract the Offer & Objects section of an Indian IPO red herring prospectus (RHP): the offer "
    "summary, capital structure, objects of the offer and offer structure. Extract the total offer size, "
    "the fresh issue and the offer for sale (OFS) as rupee amounts where stated, and the OFS as a number of "
    "shares; equity shares outstanding before and after the offer; the promoter holding % before and after "
    "the offer; each object of the fresh issue with its amount, and the general corporate purposes amount "
    "separately; the anchor, QIB, non-institutional and retail portions as %; and each selling shareholder "
    "with the shares they offer and their holding after the offer. Do not extract the price band or lot "
    "size. When merging parts: the same object or selling shareholder appears in both The Offer / the "
    "offer summary and Objects of the Offer; keep one. Objects of the Offer is authoritative for the "
    "objects and their amounts, and Capital Structure for share counts and holdings."
)

BUSINESS_INDUSTRY_PROMPT = (
    "You extract the Business & Industry section of an Indian IPO red herring prospectus (RHP): Our "
    "Business and Industry Overview. Extract what the company sells and to whom; its revenue mix by "
    "segment, geography or product; the share of revenue from its largest customer and from its top 10 "
    "customers, and the share of purchases from its largest supplier, for the latest full fiscal year; "
    "installed capacity and utilisation per plant or product line; the order book; the competitors it "
    "names; the competitive strengths it claims (a few words each); and the key market claims of the "
    "industry report (market size, growth, market share), each with its page. Say whether all or most "
    "manufacturing is at a single plant, and whether most revenue comes from a single state, region or "
    "country, only if the RHP says so, citing the page. When merging parts: Our Business is authoritative "
    "for company figures, Industry Overview for market claims; the same competitor, strength or claim in "
    "several parts is one item."
)

PROMOTERS_GOVERNANCE_PROMPT = (
    "You extract the Promoters & Governance section of an Indian IPO red herring prospectus (RHP): Our "
    "Management, Our Promoters and Promoter Group, Group Companies, History and Certain Corporate Matters, "
    "Our Subsidiaries and Dividend Policy. Extract each promoter with a one- or two-sentence background "
    "(education, experience, other businesses); each group company, marking whether the RHP says it is in "
    "the same or a similar line of business or has common pursuits; the subsidiaries; the remuneration of "
    "each promoter, director and key managerial personnel (KMP) for the latest full fiscal year, marking "
    "who is a promoter; the number of directors and of independent directors; promoter shares pledged or "
    "encumbered; dividends declared per share; and each change of statutory auditor and each change in KMP "
    "in the last three years, with its page. When merging parts: Our Management is authoritative for the "
    "board, remuneration and KMP changes, Our Promoters for promoters and pledges, Group Companies for "
    "group companies; the same person, company or change in several parts is one item."
)

OFFER_PRICE_BASIS_PROMPT = (
    "You extract the Basis for Offer Price section of an Indian IPO red herring prospectus (RHP). Extract "
    "basic and diluted EPS, NAV per share and return on net worth (RoNW) for each period shown; the P/E at "
    "the floor and cap price (usually [●]); the industry P/E (highest, lowest, average); the comparison "
    "with listed peers exactly as the RHP's table states it, one item per listed peer and not the company "
    "itself, with its P/E, basic EPS, RoNW, NAV per share and revenue or total income; each key performance "
    "indicator (KPI) with its value per period; and the weighted average cost of acquisition (WACA) of "
    "primary issuances and of secondary transactions, each with the floor and cap price as a multiple of it "
    "if printed. Where the RHP gives WACA for several look-back periods, take the latest one it compares "
    "with the floor and cap price. When merging parts: the same peer, KPI or period in several parts is "
    "one item."
)

SECTIONS: dict[str, SectionSpec] = {
    spec.group: spec for spec in [
        SectionSpec(group="offer_objects", schema=OfferObjects, prompt=OFFER_OBJECTS_PROMPT, report_field="offer"),
        SectionSpec(group="business_industry", schema=BusinessIndustry, prompt=BUSINESS_INDUSTRY_PROMPT,
                    report_field="business"),
        SectionSpec(group="promoters_governance", schema=PromotersGovernance, prompt=PROMOTERS_GOVERNANCE_PROMPT,
                    report_field="governance"),
        SectionSpec(group="offer_price_basis", schema=OfferPriceBasis, prompt=OFFER_PRICE_BASIS_PROMPT,
                    report_field="price_basis"),
    ]
}
