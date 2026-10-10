# 11 — Financials (map → reduce) + flags

**What to build:** The Financials agent reads large financial sections chunk by chunk (map) and consolidates them (reduce) into 3 years plus the stub period of key metrics, raising the PAT vs CFO, pre-IPO profit jump, receivable days and auditor-remark flags.

**Blocked by:** 07 — Offer & Objects grounded facts

**Status:** ready-for-agent

- [ ] At seam B, the golden financial numbers match after verification
- [ ] Map → reduce handles a section larger than one call
- [ ] Each financial flag fires on a fixture that should trigger it
- [ ] Ratios are computed in Python

**Spec:** `docs/specs/ipo-agents.md`
