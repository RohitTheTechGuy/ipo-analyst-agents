# 04 — Discovery of open mainboard IPOs

**What to build:** `run_daily` lists currently open mainboard IPOs from NSE, falling back to BSE then Chittorgarh, drops SME issues, skips IPOs already analysed, and puts IPOs that only have a DRHP on hold until the RHP appears.

**Blocked by:** 01 — Walking skeleton

**Status:** ready-for-agent

- [ ] At seam A, with recorded fixtures, only open mainboard IPOs are analysed
- [ ] When NSE fails, the BSE/Chittorgarh fallback is used
- [ ] When all sources fail, Telegram receives a "no source" alert (not silence)
- [ ] A DRHP-only IPO gets status `waiting_rhp` and is picked up on a later run once the RHP exists
- [ ] Already-`done` IPOs are never re-analysed

**Spec:** `docs/specs/ipo-agents.md`
