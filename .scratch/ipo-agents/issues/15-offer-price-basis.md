# 15 — Offer Price Basis agent

**What to build:** Extracts EPS, NAV, RoNW, P/E at the band, the RHP's own peer table, KPIs and the WACA of recent share transactions, and flags offer price ≫ WACA and KPIs that appear only in this section.

**Blocked by:** 07 — Offer & Objects grounded facts

**Status:** ready-for-agent

- [ ] At seam B, the golden WACA and P/E at upper band match
- [ ] The RHP peer table is captured for the peer valuation ticket
- [ ] The offer vs WACA flag fires on a fixture that should trigger it

**Spec:** `docs/specs/ipo-agents.md`
