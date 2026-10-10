# 17 — Peer valuation

**What to build:** Compares the IPO's multiples at the upper band with listed peers (RHP peers kept separate from agent-suggested ones, each suggestion with a reason), choosing multiples that fit the business. Python computes median/min/max and premium/discount, and every peer number carries its source and as-of date.

**Blocked by:** 11 — Financials (map → reduce) + flags, 13 — Business & Industry agent, 15 — Offer Price Basis agent

**Status:** ready-for-agent

- [ ] At seam B, multiples for 2 golden IPOs match a manual calculation
- [ ] Peer data the fake market-data source can't return is reported as missing, never filled in
- [ ] Agent-suggested peers are listed separately with reasons
- [ ] The choice of multiple (e.g. P/B + RoE for an NBFC) matches the business type on the golden set

**Spec:** `docs/specs/ipo-agents.md`
