# 27 — Financial Advisor agent (BUY / NO BUY)

**What to build:** a final expert-advisor agent (gpt-6.1-sol, separate $0.50 ledger per IPO) that reads the verified report, the analyst (synthesizer) view and fresh market signals and gives a BUY / NO BUY call on analysis day, refreshed on closing day; it leads the Telegram summary and PDF page 1. Full design: `docs/plans/27-financial-advisor.md`.

**Blocked by:** 01–26

**Status:** ready-for-agent

- [ ] `sol` model + `advisor` routing + `advisor:` config block (cap 0.50, max_tokens 12000, closing_day_refresh)
- [ ] BudgetMeter cap override; advisor spend on `<ipo_id>:advisor` ledger, isolated from the $1.50 analysis cap; `--reset-budget` clears both
- [ ] Report persisted as `report.json`; `ipos.report_path` set
- [ ] `advisor.advise(...)`: verified-only digest + analyst view + fresh market; evidence-ID and number validation; GMP-only buy downgraded; <2 reasons → no advice; fails soft
- [ ] Analysis-day advice in Report, Telegram summary headline, PDF page-1 box; analyst verdict relabelled "Analyst view"
- [ ] Closing-day refresh: reload report.json, re-gather sentiment, re-advise, message shows call change; failure falls back to normal update
- [ ] `--smoke` covers `Advice` and one tiny call on `sol`
