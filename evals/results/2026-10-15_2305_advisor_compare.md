# Financial Advisor: Sol vs Grok

| IPO | Saved call (Sol, live run) | sol now | grok now | Agree |
|---|---|---|---|---|
| FusionCX | NO_BUY | NO_BUY (high) | NO_BUY (high) | yes |
| HD Fire | NO_BUY | NO_BUY (medium) | NO_BUY (medium) | yes |

Spent this run: $0.28 (ledger eval-advisor)

## FusionCX (advised as of 2026-10-14)

### sol (gpt-6.1-sol)
- **Call:** NO_BUY (high conviction, horizon none)
- **Headline:** Skip Fusion CX: strong growth does not offset accounting-control weaknesses and a limited valuation cushion.
- **Reasons:**
  - Auditor findings on uncertain audit trails, deficient accounting backups and bank filings that did not reconcile with the books materially weaken confidence in financial controls. — Q:financials.auditor_remarks[4].source, Q:financials.auditor_remarks[7].source, Q:financials.auditor_remarks[11].source
  - Valuation offers no clear bargain: P/E is 24.6× against the peer median of 26.3×, but EV/EBITDA is 13.2× against 11.2× despite a below-peer PAT margin. — V:read
  - Fresh capital dominates the offer, but seller-exit disclosures are incomplete, while the upper-band price of ₹289 is far above the secondary weighted-average acquisition cost of ₹72.2. — D:fresh_pct, N:offer_objects, G:OFFER_PRICE_FAR_ABOVE_WACA, F:offer_price_basis.waca_secondary.waca
  - Strong annual growth and operating cash flow exceeding profit are positives, but the latest interim operating cash flow of ₹232.01 million trails profit of ₹556.98 million, warranting caution on cash conversion. — F:offer_price_basis.kpis[4].values[1].value, F:financials.periods[1].cfo, F:financials.periods[1].pat, F:financials.periods[0].cfo, F:financials.periods[0].pat
  - Loans to promoter entities create related-party credit exposure, while proposed debt repayment exceeding the disclosed borrowing snapshot requires reconciliation before accepting the balance-sheet improvement case. — C:loans_to_promoter_entities, F:other_disclosures.items[4].figures[3], X:debt_repayment_vs_borrowings
- **Key risks:**
  - The top 10 customers contribute 52.17% of revenue and overseas customers contribute 88.71%, increasing exposure to client losses and international demand. — F:business_industry.top10_customers_pct, G:CUSTOMER_CONCENTRATION, F:business_industry.revenue_mix[20].share
  - Substantial goodwill and provisional acquisition accounting create impairment risk and uncertainty over future reported assets and earnings. — F:other_disclosures.items[5].figures[0], Q:other_disclosures.items[3].source
  - The dated, unofficial GMP of ₹20.0 suggests possible listing upside, so skipping the offer could miss a premium without resolving the fundamental concerns. — S:gmp
- **What would change its mind:** Auditor-verified remediation of control weaknesses, complete seller-exit disclosures, reconciled debt-use figures and sustained cash conversion would address the main objections. A clearer valuation discount against comparable listed peers would then make the offer worth reconsidering.
- **Dropped or downgraded by the checks:** nothing

### grok (grok-4.20-0309-reasoning)
- **Call:** NO_BUY (high conviction, horizon none)
- **Headline:** Avoid Fusion CX IPO due to auditor qualifications, high price versus WACA, and customer concentration.
- **Reasons:**
  - Multiple auditor qualifications highlight issues with books of account, missing daily backups in India, uncertain audit trails, bank statements not agreeing with books, and delays in statutory dues deposits. — C:auditor_remarks, Q:financials.auditor_remarks[4].source, Q:financials.auditor_remarks[5].source, Q:financials.auditor_remarks[6].source, Q:financials.auditor_remarks[7].source, Q:financials.auditor_remarks[8].source, Q:financials.auditor_remarks[9].source, Q:financials.auditor_remarks[10].source, Q:financials.auditor_remarks[11].source, Q:financials.auditor_remarks[12].source
  - The upper band of ₹289 is 4.0 times the secondary WACA of ₹72.2, meaning promoters and insiders acquired shares far cheaper than the IPO price. — G:OFFER_PRICE_FAR_ABOVE_WACA, F:offer_price_basis.waca_secondary.waca, D:cap_price_to_waca_secondary
  - The business shows high customer concentration with top 10 customers at 52.17% of revenue and single-geography dependence with US revenue at 85.26%. — G:CUSTOMER_CONCENTRATION, G:SINGLE_GEOGRAPHY_DEPENDENCE, F:risk_factors.risks[0].figure, F:business_industry.top10_customers_pct, F:business_industry.revenue_mix[0].share, Q:risk_factors.risks[0].title, Q:risk_factors.risks[1].title
  - The fresh issue allocates ₹2756.95 million to debt repayment while total borrowings stand at ₹2381.32 million as of 30 June 2026, and several offer details including OFS shares and post-issue promoter holding are not disclosed. — F:offer_objects.objects[0].amount, F:financials.periods[0].total_borrowings, N:offer_objects, X:debt_repayment_vs_borrowings
  - Governance concerns include loans to promoter entities, multiple group companies in the same line of business, frequent KMP changes, and goodwill of ₹3626.43 million that carries impairment risk. — C:loans_to_promoter_entities, G:GROUP_COMPANY_CONFLICT, G:FREQUENT_KMP_CHANGES, F:fine_print_legal.related_party[9].amount, F:fine_print_legal.related_party[11].amount, F:risk_factors.risks[4].figure, Q:promoters_governance.kmp_changes[0].source
- **Key risks:**
  - The company relies on the alternative eligibility route under Regulation 6(2) requiring at least 75% allotment to QIBs because it does not meet the standard Regulation 6(1)(a) criteria, and failure would require a full refund. — Q:other_disclosures.items[1].source, F:other_disclosures.items[1].figures[0]
  - Restated financials, emphasis on IPO-specific statements, and provisional purchase price allocation for a recent acquisition that may change materially add to financial opacity. — Q:financials.restatement.source, Q:financials.auditor_remarks[0].source, Q:financials.auditor_remarks[2].source, Q:other_disclosures.items[3].source
  - High pre-issue promoter holding of 98.46% combined with not disclosed post-issue holding and OFS details creates governance and dilution uncertainty. — F:offer_objects.promoter_holding_pre_pct, N:offer_objects
- **What would change its mind:** Clear resolution of all auditor qualifications, full disclosure of OFS and post-issue promoter holding, and a significantly lower price band that aligns closer to the secondary WACA of ₹72.2 would improve the case.
- **Dropped or downgraded by the checks:** nothing

## HD Fire (advised as of 2026-10-13)

### sol (gpt-6.1-sol)
- **Call:** NO_BUY (medium conviction, horizon none)
- **Headline:** Skip HD Fire Protect’s IPO: business strengths do not outweigh governance concerns, cash-flow verification gaps and uncertain valuation comfort.
- **Reasons:**
  - The statutory auditor’s early resignation followed by appointment as CFO, non-traceable corporate records and pending Companies Act non-compliances warrant caution about reporting governance. — Q:other_disclosures.items[1].source, Q:other_disclosures.items[8].source, Q:risk_factors.risks[9].title
  - Annual operating cash flow could not be verified, while the cash conversion cycle increased from 83.0 days to 102.0 days across the latest reported full years, leaving earnings conversion insufficiently established. — N:financials, F:offer_price_basis.kpis[13].values[2].value, F:offer_price_basis.kpis[13].values[1].value
  - The 40.7× P/E is not an obvious bargain against Elgi Equipment at 44.7× and Kirloskar Pneumatic at 17.18×, particularly with profit growth of 6.4% and an incomplete peer comparison behind the headline discount. — V:read, F:offer_price_basis.peers[3].pe, F:offer_price_basis.peers[2].pe, C:pre_ipo_profit_jump
  - The offer is 100.0% promoter OFS with no fresh capital for the company, while the reported acquisition WACA of ₹0.0 cannot provide a meaningful price-to-cost multiple against the ₹258–₹271 offer band. — D:ofs_pct, Q:other_disclosures.items[0].source, F:offer_price_basis.waca_primary.waca, F:offer_price_basis.waca_secondary.waca, E:ipo
  - Global certifications, customer-empanelment barriers, profitable growth and no reported annual borrowings are genuine strengths, but do not offset the unresolved governance and cash-flow verification concerns. — Q:business_industry.moats[2].source, Q:business_industry.moats[13].source, F:financials.periods[1].revenue_from_operations, F:financials.periods[2].revenue_from_operations, F:financials.periods[1].pat, F:financials.periods[2].pat, F:financials.periods[1].total_borrowings, Q:other_disclosures.items[1].source, N:financials
- **Key risks:**
  - The critical Consent to Operate for the Jalgaon extension remains pending, creating operational and compliance uncertainty. — Q:fine_print_legal.approvals[0].source, C:critical_approvals_pending
  - Supplier dependence without long-term commitments, Maharashtra-only manufacturing and regulatory scrutiny of PFAS expose the business to supply disruption and compliance costs. — Q:other_disclosures.items[2].source, Q:business_industry.single_plant.evidence, Q:risk_factors.risks[3].title
  - Skipping could miss listing upside suggested by the unofficial ₹77.0 GMP dated 2026-10-11, but that dated grey-market signal does not establish fair value. — S:gmp
- **What would change its mind:** Verified annual cash-flow conversion, independent clarification of the reporting-governance issues and receipt of the pending operating consent would address the main concerns. I would also need a more compelling valuation supported by a complete, comparable listed-peer analysis rather than GMP or the headline discount.
- **Dropped or downgraded by the checks:** nothing

### grok (grok-4.20-0309-reasoning)
- **Call:** NO_BUY (medium conviction, horizon none)
- **Headline:** Skip HD Fire Protect IPO: 100% offer for sale of 26284500 shares at ₹258–₹271 gives company no proceeds.
- **Reasons:**
  - The IPO is a 100.0% offer for sale by promoters with no fresh issue so the company receives none of the ₹712.31 crore. — D:ofs_pct, D:fresh_pct, D:issue_size_cr, F:offer_objects.ofs_shares, Q:other_disclosures.items[0].source, G:OFS_HEAVY
  - A critical Consent to Operate extension for the Jalgaon unit remains pending while manufacturing there accounts for 61.02% to 67.94% of revenue. — Q:fine_print_legal.approvals[0].source, C:critical_approvals_pending, Q:risk_factors.risks[1].title, F:risk_factors.risks[1].figure
  - Governance concerns include an auditor who resigned mid-term and became CFO plus non-traceable corporate records and past Companies Act non-compliances. — Q:other_disclosures.items[1].source, Q:other_disclosures.items[8].source, Q:risk_factors.risks[9].title, F:risk_factors.risks[9].figure
  - Financial quality shows rising working-capital pressure with inventory days at 109.0 in the stub period versus 85.0 for the year ended March 31 2026 and receivable days also higher. — F:offer_price_basis.kpis[10].values[0].value, F:offer_price_basis.kpis[10].values[1].value, F:offer_price_basis.kpis[11].values[0].value, F:offer_price_basis.kpis[11].values[1].value, C:receivable_days_rising, Q:risk_factors.risks[7].title
  - Multiple not-disclosed and could-not-verify items on borrowings capex trade payables and promoter holdings after the offer add to opacity on material matters. — N:offer_objects, N:financials, N:promoters_governance, N:fine_print_legal, N:business_industry, N:offer_price_basis
- **Key risks:**
  - Supplier concentration is high with top 10 suppliers at 59.28% of total expenses in the stub period and 45.0% of raw materials are imported with no long-term agreements. — Q:risk_factors.risks[2].title, F:risk_factors.risks[2].figure, Q:risk_factors.risks[4].title, F:risk_factors.risks[4].figure
  - PFAS chemicals used in foam products face increasing global regulatory scrutiny that could require reformulation or affect 18.71% of revenue from foam equipment. — Q:risk_factors.risks[3].title, F:risk_factors.risks[3].figure, F:business_industry.revenue_mix[4].share
- **What would change its mind:** Strong anchor and institutional subscription data combined with resolution of the pending Consent to Operate and full disclosure of all not-disclosed items on post-issue holdings and capex would support a buy call for listing gains.
- **Dropped or downgraded by the checks:** dropped risk "Promoter remuneration equals 17.1% of PAT for the year ended March 31 2026 while the entire offer is an exit for two promoters.": 31, 2026 not in its cited evidence
