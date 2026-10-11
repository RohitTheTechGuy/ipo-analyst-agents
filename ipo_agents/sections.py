"""Section agent registry: one SectionSpec (prompt + schema) per section group. The graph runs every entry."""
from __future__ import annotations

from ipo_agents.contracts import (MAX_LISTED_RISKS, BusinessIndustry, Financials, FinePrintLegal, OfferObjects,
                                  OfferPriceBasis, OtherDisclosures, PromotersGovernance, RiskFactors)
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

RISK_FACTORS_PROMPT = (
    "You extract the Risk Factors section of an Indian IPO red herring prospectus (RHP). List at most "
    f"{MAX_LISTED_RISKS} risk factors, each once, in the order printed: every quantified risk, then the most "
    "severe company-specific ones. Do not list generic risks; count them. Give total_count, the number of risk "
    "factors the section prints in all (listed or not), and generic_count, how many of those are generic. "
    "Keep each listed risk compact: a few-word topic, and its heading copied "
    "verbatim as the title quote with its page (the first sentence if the heading is very long); never the "
    "whole paragraph. Classify each as 'generic' (boilerplate that could sit in any RHP: the Indian economy, "
    "politics, regulation in general, natural calamities, the market for and price of the equity shares, "
    "currency, taxation of shareholders) or 'company_specific' (about this company's own plants, customers, "
    "suppliers, products, debt, litigation, losses, promoters). Mark it quantified when the risk states a "
    "number about this company (e.g. a share of revenue, an amount of debt or claims), and give that number "
    "as figure. Rank the company-specific risks by severity (1 = most severe), weighing how likely the risk "
    "is and how much of revenue, profit or the business it could hit; quantified, concentrated risks rank "
    "above vague ones. Generic risks get no rank. When merging parts: the same risk appears once; re-rank "
    "all company-specific risks from 1 across the whole section; keep at most "
    f"{MAX_LISTED_RISKS} as above; total_count and generic_count are the sums over the parts."
)

FINE_PRINT_LEGAL_PROMPT = (
    "You extract the fine print of an Indian IPO red herring prospectus (RHP): Outstanding Litigation and "
    "Material Developments, Government and Other Approvals, Material Contracts and Documents, and the pages "
    "of other sections that state contingent liabilities or related party transactions. "
    "Litigation: from the summary of outstanding litigation, one line per party (the company, its "
    "promoters, directors, subsidiaries), case type (criminal; tax; regulatory = actions by statutory or "
    "regulatory authorities and disciplinary actions; civil = material civil litigation; other) and "
    "direction (against the party, or by it), with the number of cases and the aggregate amount involved. "
    "Contingent liabilities: the total not provided for at the latest date; if no total is printed, the "
    "claims not acknowledged as debts. "
    "Related party transactions of the latest full fiscal year: one line per counterparty and category "
    "(sales, purchases, loans given, guarantees, rent, remuneration, other), with the period, and whether "
    "the counterparty is a promoter, promoter group entity or group company; no total lines. "
    "Approvals: each approval or licence that is pending, applied for or expired, and whether it is "
    "critical (the plant, product or main activity can't run without it), quoting the line. "
    "Material contracts: quote each material contract with a promoter or promoter group entity. "
    "When merging parts: the same litigation line or transaction appears once. The litigation chapter is "
    "authoritative for litigation, and the restated financial information for related party transactions "
    "and contingent liabilities."
)
# Contingent liabilities and related party transactions are printed in the financials (notes, other
# financial information) and the offer-document summary, not in the legal chapters (plan 5.2).
FINE_PRINT_BORROW = r"contingent\s+liabilit|related\s+party\s+transactions"

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
# Output tokens for the merge (reduce), final and re-ask calls of the largest sections, whose answer covers
# the whole section; their map calls keep config.default_max_tokens.
LARGE_SECTION_MAX_TOKENS = 8000

SECTIONS: dict[str, SectionSpec] = {
    spec.group: spec for spec in [
        SectionSpec(group="offer_objects", schema=OfferObjects, prompt=OFFER_OBJECTS_PROMPT, report_field="offer"),
        SectionSpec(group="financials", schema=Financials, prompt=FINANCIALS_PROMPT, report_field="financials",
                    reduce_max_tokens=LARGE_SECTION_MAX_TOKENS),
        SectionSpec(group="risk_factors", schema=RiskFactors, prompt=RISK_FACTORS_PROMPT, report_field="risk_factors",
                    reduce_max_tokens=LARGE_SECTION_MAX_TOKENS),
        SectionSpec(group="fine_print_legal", schema=FinePrintLegal, prompt=FINE_PRINT_LEGAL_PROMPT,
                    report_field="fine_print_legal", borrow_from=("offer_objects", "financials"),
                    borrow_if=FINE_PRINT_BORROW),

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
