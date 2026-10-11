"""Section agent registry: one SectionSpec (prompt + schema) per section group. The graph runs every entry."""
from __future__ import annotations

from ipo_agents.contracts import (BusinessIndustry, Financials, OfferObjects, OfferPriceBasis, OtherDisclosures,
                                  PromotersGovernance)
from ipo_agents.section_agent import SectionSpec

OFFER_OBJECTS_PROMPT = (
    "You extract the Offer & Objects section of an Indian IPO red herring prospectus (RHP): the offer "
    "summary, capital structure, objects of the offer and offer structure. Extract the total offer size, "
    "the fresh issue and the offer for sale (OFS) as rupee amounts where stated, and the OFS as a number of "
    "shares; equity shares outstanding before and after the offer; the promoter holding % before and after "
    "the offer; each object of the fresh issue with its amount, and the general corporate purposes amount "
    "separately; the anchor, QIB, non-institutional and retail portions as %; and each selling shareholder "
    "with the shares they offer, their holding after the offer, and their category as the RHP labels it "
    "(Promoter Selling Shareholder -> promoter; Investor Selling Shareholder -> investor; else other). "
    "Do not extract the price band or lot "
    "size. When merging parts: the same object or selling shareholder appears in both The Offer / the "
    "offer summary and Objects of the Offer; keep one. Objects of the Offer is authoritative for the "
    "objects and their amounts, and Capital Structure for share counts and holdings."
)

FINANCIALS_PROMPT = (
    "You extract the Financials of an Indian IPO red herring prospectus (RHP). Read the Restated Financial "
    "Information tables (restated statements of assets and liabilities, profit and loss, and cash flows, and "
    "their notes) and Management's Discussion and Analysis (MD&A). Give one period entry per column: each of "
    "the (usually 3) fiscal years, and any stub period (an interim period shorter than a year, e.g. 'three "
    "months ended June 30, 2024'), with its label and end date exactly as printed and kind 'full_year' or "
    "'stub'. Never annualise a stub period: report its figures as printed. For each period extract revenue "
    "from operations, total income, EBITDA (only if the RHP states it), restated profit after tax (PAT), net "
    "cash from operating activities (CFO), capex (purchase of property, plant and equipment), total "
    "borrowings, cash and cash equivalents, net worth, trade receivables, inventories, trade payables, and "
    "return on net worth / ROE and ROCE in % (only if disclosed). Sign convention: a loss and cash used are "
    "negative; cash used shown in brackets, e.g. (6,330.87), is value -6330.87. For a negative "
    "figure quote the table row where its brackets or minus sign are printed, not prose such as 'net cash "
    "used in operating activities was 6,330.87', which drops the sign. Cite the page where each number is printed. "
    "Also list every qualification and emphasis-of-matter paragraph in the auditor's examination report, each "
    "with a one-line summary and a verbatim quote of the paragraph with its page; and whether restatement "
    "adjustments were made (the audited-to-restated reconciliation), quoting the line that shows it. "
    "When merging parts: the same period (the same end date) appears in several parts (profit and loss, "
    "balance sheet, cash flow, MD&A); merge them into one period, taking each metric from the part that states "
    "it. The Restated Financial Information tables are authoritative over MD&A for every number."
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

OTHER_DISCLOSURES_PROMPT = (
    "You scan the pages of an Indian IPO red herring prospectus (RHP) that no other section reader covers "
    "(general information, key regulations, offer procedure, statutory and regulatory disclosures and the "
    "like) for anything an investor should know that is not standard boilerplate: e.g. regulatory action "
    "or penalties, a refused or withdrawn earlier offer, a change in business, unusual terms of the offer, "
    "or dependence on a licence. List only material items, each with its page, why it is material, and "
    "any numbers it cites. Returning no items (nothing material) is a valid and common answer; never list "
    "boilerplate to fill the answer. When merging parts: the same item in several parts is one item."
)

# Output tokens for the sections routed to Groq: prompt + schema + pages + this must fit Groq's
# per-call limit (groq_limits.max_call_tokens, 6,000), or the call falls back to Luna.
GROQ_MAX_TOKENS = 1500

SECTIONS: dict[str, SectionSpec] = {
    spec.group: spec for spec in [
        SectionSpec(group="offer_objects", schema=OfferObjects, prompt=OFFER_OBJECTS_PROMPT, report_field="offer"),
        SectionSpec(group="financials", schema=Financials, prompt=FINANCIALS_PROMPT, report_field="financials"),
        SectionSpec(group="business_industry", schema=BusinessIndustry, prompt=BUSINESS_INDUSTRY_PROMPT,
                    report_field="business"),
        SectionSpec(group="promoters_governance", schema=PromotersGovernance, prompt=PROMOTERS_GOVERNANCE_PROMPT,
                    report_field="governance", max_tokens=GROQ_MAX_TOKENS),
        SectionSpec(group="offer_price_basis", schema=OfferPriceBasis, prompt=OFFER_PRICE_BASIS_PROMPT,
                    report_field="price_basis"),
        SectionSpec(group="other_disclosures", schema=OtherDisclosures, prompt=OTHER_DISCLOSURES_PROMPT,
                    report_field="other", max_tokens=GROQ_MAX_TOKENS),
    ]
}
