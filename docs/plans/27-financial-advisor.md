# Plan: Financial Advisor agent (BUY / NO BUY)

## Context
The pipeline already produces a verified report per IPO, plus an analyst verdict from the synthesizer (ticket 20) using Terra. The user wants a final **expert financial-advisor agent** on top of that. It reads the finished report, everything the other agents gathered, and the latest market signals (new articles, subscription, GMP), then gives a clear **BUY / NO BUY** call. It runs on the strongest model within a **$0.50 budget**. It reads only summarised, verified material, never raw RHP pages, so it stays cheap.

**Decisions made (from the user's answers):**
- Model: **`gpt-6.1-sol`** (official price $2.00 in / $0.10 cached / $10.00 out per 1M tokens; it's on the user's key).
- Timing: **analysis day + a closing-day refresh** (latest GMP, subscription and articles).
- The advisor is the **headline**. The synthesizer verdict stays as the "Analyst view".
- Budget: a **separate $0.50 ledger** per IPO, on top of the $1.50 analysis cap, so the worst case is $2.00 per IPO.

## Design

### Config (`config.yaml`, `ipo_agents/config.py`)
- Model: `sol: { provider: openai, id: gpt-6.1-sol, price_in: 2.00, price_cached: 0.10, price_out: 10.00, reasoning_effort: high }`.
- Routing: `advisor: sol`.
- New `advisor:` block:
  - `cap_usd: 0.50`
  - `max_tokens: 12000` (reasoning + answer)
  - `closing_day_refresh: true`
- Worst-case reserve per run is about 25k in × $2 + 12k out × $10 ≈ **$0.17**. Two runs plus refreshed stance tags (Groq at $0, Luna fallback is tiny) come to about $0.35, which is under $0.50.

### Separate ledger (`ipo_agents/budget.py`)
- `BudgetMeter(ledger_id, config, cap=None)`: an optional cap override, defaulting to `config.budget.cap_usd`.
- The advisor uses ledger `f"{ipo_id}:advisor"` with `config.advisor.cap_usd`. Spend is persisted in `llm_calls` as now, so it survives restarts and never mixes with the analysis cap.
- Reuse `MeteredLLM`, the router, retries and fail-closed pricing unchanged.
- `--reset-budget` (`ipo_agents/maintenance.py`) clears both ledgers.

### Persist the report (`ipo_agents/analysis.py` / `runner.py`, `store.py`)
- When a report is built, write `work_dir/<ipo_id>/report.json` (`Report.model_dump_json()`), and set the existing `ipos.report_path` column to point at it.
- The closing-day refresh needs this; today only the PDF is saved.

### Advisor module (new `ipo_agents/advisor.py`)
- **Input digest:**
  - Reuse `synthesizer.build_digest(report)`, which keeps only verified items (F:/Q:/N:/D:/G:/C:/X:/V:/S:).
  - Add the analyst view (synthesizer verdict, reasons, red flags) as `A:` items.
  - Add the fresh market block (`S:gmp`, `S:subscription`, `S:tally`, article stances and bull/bear points with dates).
  - Add context: today's date, days to close, upper band, minimum investment.
  - No raw RHP text.
- **Schema `Advice`:**
  - `call: buy | no_buy`, `conviction: low | medium | high`, `horizon: listing_gains | long_term | none`
  - `headline` (one line)
  - `reasons` (3–5) and `key_risks` (≤3), both `EvidencedPoint` with evidence IDs
  - `what_would_change_my_mind`
  - The schema follows strict-mode rules: no non-null defaults, no min/max.
- **System prompt:**
  - Persona: a seasoned Indian IPO advisor serving a retail applicant.
  - Weigh business quality and governance, financial quality, valuation vs peers, and offer structure (OFS/exit, WACA). Demand (subscription) informs listing gains.
  - GMP is unofficial and never the sole basis.
  - "Could not verify / not disclosed" counts as a risk.
  - Cite evidence IDs and never introduce new numbers.
  - Keep it general: no position sizing or personal suitability.
- **Python validation**, reusing `synthesizer` helpers (`_keep`, `_unsupported`, `_numbers_in`):
  - Drop points with unknown IDs or with numbers not in the cited items.
  - A `buy` whose surviving reasons cite only `S:`/`A:` items (no RHP-derived F/D/C/X/G/V evidence) is downgraded to low conviction, with a note.
  - Fewer than 2 surviving reasons → `advice=None` ("advice not reached").
- **Interface:** `advise(report, sentiment, llm, config, today) -> CheckedAdvice | None`. It fails soft: any error or `BudgetExceeded` on the advisor ledger returns None and logs it. The analysis is never lost.

### Flow
- **Analysis day** (`runner.run_daily`, after `analyse_rhp` returns a complete report):
  1. Run the advisor on the advisor ledger.
  2. Attach `Report.advice`.
  3. Save the JSON, render the PDF and send.
  4. If the IPO is analysed on its closing day, this run is the freshest, so there's no separate refresh.
- **Closing-day refresh** (`notify/reminders.py` → `send_daily_updates`, when `close_date == today` and refresh is enabled):
  1. Load `report.json`.
  2. Re-gather sentiment (`sentiment.gather_sentiment`: Tavily articles + stance tags + GMP + subscription), metered on the advisor ledger.
  3. Re-run the advisor and save `advice_<date>.json`.
  4. The "⏰ closes TODAY" message leads with the refreshed call and notes any change from the analysis-day call (e.g. "was NO BUY → now BUY"), then shows the GMP and subscription lines as now.
  5. If the refresh fails, send the normal update with the analysis-day advice.

### Output
- **Telegram summary** (`runner.format_summary`):
  - First line: `🧭 Advisor: BUY · conviction high · listing gains`
  - Then the headline, 3 reasons and the top 2 risks.
  - The existing verdict line is relabelled `Analyst view: …`.
  - The disclaimer is unchanged.
- **PDF page 1** (`report/templates/report.html`): an advisor box above the analyst verdict badge, with the call, conviction, horizon, reasons with evidence, risks and "what would change my mind". The text label is not colour alone.

### Smoke
- `--smoke` adds the `Advice` schema.
- `--smoke` makes one tiny call on `sol` (about $0.01) to validate the model ID and the strict schema there.

## Critical files
- New: `ipo_agents/advisor.py`, `tests/test_advisor.py`.
- Edit:
  - `config.yaml`, `ipo_agents/config.py`
  - `ipo_agents/budget.py` (cap override)
  - `ipo_agents/contracts.py` (`Advice`, `CheckedAdvice`, `Report.advice`)
  - `ipo_agents/runner.py`, `ipo_agents/notify/reminders.py`
  - `ipo_agents/analysis.py` (JSON persist)
  - `ipo_agents/report/templates/report.html`
  - `ipo_agents/maintenance.py` (smoke + reset)
- Reuse:
  - `synthesizer.build_digest` / `_keep` / `_unsupported`
  - `MeteredLLM`, `BudgetMeter`
  - `sentiment.gather_sentiment`
  - `runner._subscription_line`, `labels.subscription_text`

## Verification (TDD, seams A/B, offline; hand-worked costs)
- **Routing and separation:**
  - The advisor call routes to `sol` with task `advisor`.
  - The advisor spend lands on the `<ipo_id>:advisor` ledger, and the analysis spend never counts against it (or vice versa).
- **Budget:** a $0.50 cap stop yields "advice not reached", with the analysis summary and PDF intact.
- **Digest and validation:**
  - The digest contains no unverified value.
  - Points with bad IDs or unsupported numbers are dropped.
  - A GMP-only buy is downgraded.
  - Fewer than 2 reasons gives no advice.
- **Closing day:**
  - The refresh reloads `report.json`, re-gathers sentiment, re-runs the advisor and sends a message showing the call change.
  - A failed refresh still sends the normal update.
- **Summary and PDF:** the summary leads with the advisor line, with the Analyst view kept, and PDF page 1 has the advisor box (`@pytest.mark.renderer`).
- **Maintenance:** `--reset-budget` clears both ledgers, and the smoke includes `Advice`.
- **Live:**
  - `run_daily.py --smoke` (sol + Advice OK).
  - One real IPO run, then compare the advisor ledger cost with the ~$0.10–0.20 estimate.
