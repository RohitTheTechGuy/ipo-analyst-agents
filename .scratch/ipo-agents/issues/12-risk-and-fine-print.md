# 12 — Risk Factors + Fine Print & Legal

**What to build:** Risk factors are classified as company-specific or boilerplate, with the top 10 ranked. Litigation by type and amount, contingent liabilities vs net worth and related-party totals are extracted with their flags.

**Blocked by:** 11 — Financials (map → reduce) + flags

**Status:** ready-for-agent

- [ ] At seam B, the top 10 company-specific risks are ranked and the boilerplate risks are separated
- [ ] Litigation counts and amounts per type match a hand-checked fixture
- [ ] The contingent-liabilities/net-worth and RPT > ~15% of revenue flags fire correctly
- [ ] Uses map → reduce for long sections

**Spec:** `docs/specs/ipo-agents.md`
