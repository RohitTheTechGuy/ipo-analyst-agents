# 18 — Sentiment: articles + stance

**What to build:** A tool-calling agent searches Tavily for reviews and broker notes published after the RHP date, keeps up to 8 sources (4 when degraded), tags each one's stance, drops spam, and labels every third-party number "claimed by <source>".

**Blocked by:** 03 — Budget cap + call log

**Status:** ready-for-agent

- [ ] At seam B, with a fake search source, the stance tally and the bull/bear points come from the fixture articles
- [ ] Articles dated before the RHP date are excluded
- [ ] In degraded mode at most 4 sources are read
- [ ] Third-party numbers are `ClaimedFact`s, never mixed with RHP facts

**Spec:** `docs/specs/ipo-agents.md`
