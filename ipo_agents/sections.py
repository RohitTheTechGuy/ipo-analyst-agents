"""Section agent registry: one SectionSpec (prompt + schema) per section group. The graph runs every entry."""
from __future__ import annotations

from ipo_agents.contracts import Financials, FinePrintLegal, OfferObjects, RiskFactors
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
    "You extract the Risk Factors section of an Indian IPO red herring prospectus (RHP). List every risk "
    "factor once, in the order printed. Keep each one compact: a few-word topic, and its heading copied "
    "verbatim as the title quote with its page (the first sentence if the heading is very long); never the "
    "whole paragraph. Classify each as 'generic' (boilerplate that could sit in any RHP: the Indian economy, "
    "politics, regulation in general, natural calamities, the market for and price of the equity shares, "
    "currency, taxation of shareholders) or 'company_specific' (about this company's own plants, customers, "
    "suppliers, products, debt, litigation, losses, promoters). Mark it quantified when the risk states a "
    "number about this company (e.g. a share of revenue, an amount of debt or claims), and give that number "
    "as figure. Rank the company-specific risks by severity (1 = most severe), weighing how likely the risk "
    "is and how much of revenue, profit or the business it could hit; quantified, concentrated risks rank "
    "above vague ones. Generic risks get no rank. When merging parts: the same risk appears once; re-rank "
    "all company-specific risks from 1 across the whole section."
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

SECTIONS: dict[str, SectionSpec] = {
    spec.group: spec for spec in [
        SectionSpec(group="offer_objects", schema=OfferObjects, prompt=OFFER_OBJECTS_PROMPT, report_field="offer"),
        SectionSpec(group="financials", schema=Financials, prompt=FINANCIALS_PROMPT, report_field="financials"),
        SectionSpec(group="risk_factors", schema=RiskFactors, prompt=RISK_FACTORS_PROMPT, report_field="risk_factors"),
        SectionSpec(group="fine_print_legal", schema=FinePrintLegal, prompt=FINE_PRINT_LEGAL_PROMPT,
                    report_field="fine_print_legal", borrow_from=("offer_objects", "financials"),
                    borrow_if=FINE_PRINT_BORROW),
    ]
}
