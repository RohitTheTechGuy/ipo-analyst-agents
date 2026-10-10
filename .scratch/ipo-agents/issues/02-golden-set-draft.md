# 02 — Golden set draft

**What to build:** Three past mainboard IPOs (one OFS-heavy, one loss-making, one NBFC/lender) are chosen, their final RHP PDFs are stored as test fixtures, and about 10 expected numbers per IPO are drafted with page references, marked `needs_human_review` until the investor verifies them.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] 3 RHP PDFs stored with their SHA-256 hashes
- [ ] Each IPO has ~10 expected numbers (issue size, OFS split, revenue, PAT, CFO, net worth, contingent liabilities, WACA, P/E at upper band, …) with page and verbatim quote
- [ ] Every file is marked `needs_human_review: true`
- [ ] **HITL:** the investor spot-checks the numbers against the PDFs and clears the flag

**Spec:** `docs/specs/ipo-agents.md`
