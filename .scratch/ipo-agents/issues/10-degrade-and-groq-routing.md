# 10 — Degrade at 70% + Groq routing

**What to build:** Once 70% of the cap is spent (or the pre-run page-count estimate exceeds 70%), Terra tasks are routed to Luna and sentiment drops to 4 sources. Small tasks go to Groq when under the size and daily-usage limits, and fall back to Luna immediately on a 429.

**Blocked by:** 03 — Budget cap + call log

**Status:** ready-for-agent

- [ ] At seam B, crossing 70% of spend switches subsequent Terra tasks to Luna
- [ ] A large-page-count RHP starts in degraded mode
- [ ] A Groq-listed task over ~6k tokens, or past 80% of daily Groq usage, goes to Luna
- [ ] A Groq 429 falls back to Luna without waiting

**Spec:** `docs/specs/ipo-agents.md`

**From the ticket 03 review:** `LangChainLLM` builds `ChatOpenAI` with `max_retries=0` so no retry escapes the meter. Retries for 429/5xx must live in metered code (each attempt reserves and settles through `MeteredLLM`).
