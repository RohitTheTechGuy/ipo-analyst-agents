# 07 — Offer & Objects grounded facts

**What to build:** The first real section agent extracts the offer details (issue size, fresh issue vs OFS, selling shareholders, price band, lot, use of proceeds, promoter holding) as page-cited `Fact`s. The verifier rejects any number not found on its cited page, and those fields appear as "could not verify".

**Blocked by:** 03 — Budget cap + call log, 06 — Ingestion with 100% coverage

**Status:** ready-for-agent

- [ ] At seam B, a fake LLM returning a number absent from its cited page leads to that field being marked "could not verify" in the Report
- [ ] Numbers are normalised (commas, ₹, lakh/crore/million) before matching; quotes need a fuzzy match ≥ 0.9
- [ ] A field returned as not disclosed appears as "not disclosed"
- [ ] Offer facts appear in the Report and the Telegram summary

**Spec:** `docs/specs/ipo-agents.md`

**From the golden set (ticket 02):**
- Final RHPs show the price band, rupee issue/OFS amounts and P/E as `[●]`. Price band and lot size come from the exchange listing (`Ipo`), not the RHP; an OFS given only as a share count is valid.
- "Nil" disclosures (e.g. no contingent liabilities, WACA "Nil") must be storable: allow a `Fact` with value 0 whose quote contains "Nil"/"no", and have the verifier accept that.
- RHPs report in ₹ million (not crore); keep the unit from the page and convert only in the renderer.
- Negatives appear in brackets, e.g. (15,844.00); the verifier must treat them as negative numbers.
