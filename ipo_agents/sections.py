"""Section agent registry: one SectionSpec (prompt + schema) per section group. The graph runs every entry."""
from __future__ import annotations

from ipo_agents.contracts import Financials, OfferObjects
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

SECTIONS: dict[str, SectionSpec] = {
    spec.group: spec for spec in [
        SectionSpec(group="offer_objects", schema=OfferObjects, prompt=OFFER_OBJECTS_PROMPT, report_field="offer"),
        SectionSpec(group="financials", schema=Financials, prompt=FINANCIALS_PROMPT, report_field="financials"),
    ]
}
