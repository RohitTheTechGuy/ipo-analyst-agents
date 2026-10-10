# 01 — Walking skeleton

**What to build:** All three seams run end to end with trivial behaviour: `run_daily` discovers a fake IPO, calls `analyse_rhp`, which calls `ingest` on a tiny PDF, and the fake Telegram notifier receives a stub summary. Sets up the package, config file, SQLite store, Pydantic contracts (`Fact`, `ClaimedFact`, Report, SectionedDocument) and the fake adapter set every later ticket tests with.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] `pytest` passes offline with no keys and no network
- [ ] One passing test at each seam (A `run_daily`, B `analyse_rhp`, C `ingest`)
- [ ] A fake adapter set exists for exchange, downloader, LLM, search, market data, GMP and Telegram
- [ ] A second `run_daily` on the same day doesn't re-analyse the same IPO
- [ ] Test dependencies and `pango`/`tesseract` system libs are installed and recorded

**Spec:** `docs/specs/ipo-agents.md`
