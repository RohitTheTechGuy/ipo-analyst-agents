# 26 — Pre-live hardening: optional parts fail soft, prose grounding, paths

**What to build:** no optional part (sentiment, valuation, synthesizer, derived calcs, summary formatting) can lose a verified analysis, and no unverified number reaches the PDF/Telegram via prose, from the pre-live review (B3, S3, S4, S5, S6).

**Blocked by:** 01–23

**Status:** ready-for-agent

- [ ] Sentiment, peer valuation and synthesizer catch non-budget errors (and BudgetExceeded for sentiment/valuation) and degrade to "unavailable"/"not reached (synthesizer failed)"
- [ ] `_report` derived blocks (offer calcs, legal, sections, cross-checks) each fail soft; zero divisors are not computable
- [ ] `format_summary` failures don't abort the run for other IPOs
- [ ] Model-written prose fields next to a Quote are blanked/flagged when they contain a number not in their verified quote; bull/bear points checked against key_reasons; synthesizer section summaries checked against their own section only, page numbers not allowed values
- [ ] Telegram bullets cut at word boundaries, never inside a number
- [ ] Relative `db_path`/`work_dir` resolve against the repo root; `data/` gitignored
- [ ] LLM-facing schemas have no non-None defaults or `minimum` constraints (OpenAI strict mode)

**Spec:** `docs/specs/ipo-agents.md`
