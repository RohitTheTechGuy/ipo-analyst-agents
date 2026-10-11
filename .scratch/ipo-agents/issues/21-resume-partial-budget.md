# 21 — Resume + partial-budget report

**What to build:** A crashed analysis resumes from its LangGraph checkpoint on the next run with prior spend carried over. A run stopped by `BudgetExceeded` produces a partial report from facts verified so far, alerts on Telegram, and becomes terminal `failed_budget`.

**Blocked by:** 07 — Offer & Objects grounded facts

**Status:** ready-for-agent

- [x] At seam A, a crash mid-graph followed by a rerun doesn't repeat completed model calls
- [x] The resumed run's budget includes the pre-crash spend
- [x] A budget stop yields a `partial_budget` Report, a Telegram alert and status `failed_budget`
- [x] `failed_budget` IPOs aren't retried on later runs

**Spec:** `docs/specs/ipo-agents.md`

**From ticket 23:** `run_daily.py --ipo X` can't re-run an IPO that is already `done`/`failed_budget` (the runner skips terminal IPOs). If a manual re-run is wanted, add an explicit `force` flag to `run_daily` here. The CLI's `--dry-run` duplicates the runner's skip/hold/analyse rules (`cli.dry_run`); consider a read-only `plan()` in the runner that both use.
