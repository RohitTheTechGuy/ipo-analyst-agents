# 20 — Synthesizer verdict + cross-section checks

**What to build:** From verified outputs only, produces the verdict (Subscribe long term / Listing gains only / Neutral / Avoid), confidence, 3–5 reasons and ranked red flags and positives, and runs the cross-section checks (debt repayment vs debt schedule, litigation vs contingent liabilities, KPIs vs financials).

**Blocked by:** 09 — Offer calculations + flags, 12 — Risk Factors + Fine Print & Legal, 13 — Business & Industry agent, 14 — Promoters & Governance agent, 15 — Offer Price Basis agent, 16 — Other Disclosures agent, 17 — Peer valuation, 19 — Sentiment: GMP + subscription status

**Status:** ready-for-agent

- [ ] At seam B, a verdict never cites a fact that is unverified or absent from its input
- [ ] Each cross-section inconsistency in a fixture is flagged
- [ ] GMP alone can't drive the verdict (fixture test)
- [ ] Golden IPOs produce a verdict with reasons that reference page-cited facts

**Spec:** `docs/specs/ipo-agents.md`
