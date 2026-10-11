# Session handoff: IPO Analyst Agents

_Updated 2026-10-11 (end of session `de846e29`)._ For the system design, read [`docs/specs/ipo-agents.md`](docs/specs/ipo-agents.md) and [`docs/agents.md`](docs/agents.md) (every agent, its model, tools, state and the orchestration diagram). Tickets 01–27 are in `.scratch/ipo-agents/issues/`, and the advisor plan is in `docs/plans/27-financial-advisor.md`. Lessons are in the user's Obsidian vault at `~/Obsidian Docs/IPO Analyst Agents/`. Repo: github.com/RohitTheTechGuy/ipo-analyst-agents (private). `main` matches `origin/main` at `6bdd6a2`.

## ⚠️ Four background shell tasks did not finish before the session ended

The harness reported 4 background tasks as "stopped, no completion record": **`bh5ipxl8w`, `bgey9lbjd`, `bht13phiu`, `btfm34iu3`**.

**What they were:** `Monitor` **log watchers** (`tail -F <run log> | grep …`), one per live HD Fire test run (runs 1–4). They were not the analysis runs. They never exit on their own and were cut off when the session ended. **Nothing is lost.**

**What I verified before writing this:**
- No `run_daily.py` or `tail -F` process is running (`pgrep` came back empty).
- Every run had ended before the session closed:
  - Run 1: stopped by me after it wasted tokens on reasoning.
  - Run 2: failed in Promoters & Governance, which led to the fail-soft fix.
  - Run 3: finished, but incomplete.
  - Run 4: finished complete. It was analysed, with no failed sends and no truncations.
- The store shows `HDFIRE-2026-10-13` as `done`, verdict `neutral`, `report_path` set, and last update `2026-10-13`.
- The cost ledger shows `HDFIRE-2026-10-13` at **$1.3339** (144 calls) and `HDFIRE-2026-10-13:advisor` at **$0.0922**, matching run 4. `SMOKE-2026-10-11` is at $0.018.

**What the next session should do:**
- Nothing to clean up.
- The per-run log files lived in that session's scratchpad and are gone. The run outcomes are in the DB and in `reports/`.
- For future runs, write logs to `logs/` (gitignored) rather than the scratchpad, so they outlive the session.

## Where things stand

- **All 27 tickets are built and merged**, with 506 offline tests passing (`.venv/bin/pytest -q -p no:warnings`, about 47s). Ticket 24 (live golden runs) is still open; it needs the user to hand-verify `tests/golden/*/expected.yaml`.
- **Budget: $5.00 per IPO**, split as `budget.cap_usd: 4.00` for analysis and `advisor.cap_usd: 1.00`. All the output limits were revised after the live runs; see `config.yaml` and commit `fed65c3`. The tests pin the old $1.50 / 4k figures in `tests/conftest.py`, so their hand-worked arithmetic stays valid.
- **First live IPO: HD Fire Protect** (`HDFIRE-2026-10-13`; band ₹258–₹271, lot 55), analysed by running the CLI with `--date 2026-10-13`.
  - **Run 4:** all 8 sections, sentiment, valuation and verdict made it into the report.
  - **Analyst view:** NEUTRAL. **Advisor:** NO BUY at medium conviction.
  - **Cost:** $1.43.
  - **Archived** at `reports/HDFIRE-2026-10-13/2026-10-11_1644_run4/`, which holds the report JSON, the PDF and the exact Telegram text.
- **Test spend today:** about $4.8 in total across the smoke test and runs 1–4.
- **Telegram works.** The bot is `@IPOSummary_bot` and the chat is verified. The summary now shows reasons in full (`BULLET_CHARS 500`), and any message over 4,096 characters is split at line breaks.
- **The launchd 10:30 schedule is not installed.** The user asked to wait until they had checked the run. Install it with `.venv/bin/python scheduler/install.py install`.
- **Next automatic step for HD Fire:** once the schedule is installed, the daily GMP + subscription updates run on 14–15 Oct, plus the closing-day advisor refresh on 15 Oct. HD Fire is already `done`, so it won't be re-analysed.

## Open items, in the order to tackle them

1. **"Issue: not disclosed" in the summary.** The RHP prints `[●]`, but `calculations["issue_size_cr"]` already holds the value computed at the upper band (26,284,500 × ₹271 ≈ ₹712 cr). Show it, labelled as computed.
2. **Peer median P/E of 96×** looks inflated for a fire-safety maker. Check the RHP peer table against the agent-suggested peers in `report.valuation` before trusting "58% discount".
3. **31 "could not verify" fields**, mostly Business list quotes rejected by the exact-match rule for `Quote`s in `verifier._quoted`. Consider normalising hyphenation and Unicode dashes as well as whitespace, without going back to fuzzy matching (that let a different firm's name through).
4. **Offer Price Basis verifier re-asks cost $0.37.** Tune this, for example by capping the re-asks for dense tables.
5. **Golden-number review** (ticket 24) is the user's task.
6. **Optional:** resend the full HD Fire summary from the saved `report.json`. That's free, with no model calls; the user hasn't asked for it yet.

## Gotchas learned live (all fixed; don't regress them)

Details are in commits `47b8465`, `7b13dba`, `fbe7d10` and `fed65c3`, and in the vault lessons note.
- **Reasoning effort:**
  - Luna's `reasoning_effort` is `none`. At `low`, hidden reasoning used up whole output budgets.
  - Both GPT-5.6 models accept `none` and reject `minimal`.
  - Reasoning models get no `temperature` (`gpt-6.1-sol` rejects 0).
- **Output limits:** map calls get `map_max_tokens`, separate from a section's own limit.
- **Fail-soft:** a section whose own reply can't be parsed is skipped as "could not read". Provider errors still fail the IPO so it gets retried.
- **Groq:** Governance and Other Disclosures route to Luna, because their real prompts exceed Groq's 6k limit. Groq's free tier caps at 8k tokens per minute; stance tags fall back to Luna.
- **Budget resets:** spend per IPO never resets on its own. `--ipo X --reset-budget` (with a typed confirmation) clears both ledgers and the checkpoint. **Only do it with the user's explicit OK.**

## How the user works

The user writes terse instructions, and "yes" means go ahead with the recommendation. They want autonomy once a build is approved (see memory `build-autonomy`), but every spend or reset decision, and anything that touches the outside world, needs their OK. They like being kept informed of progress during long runs.

## Suggested skills

- `mattpocock-skills:tdd`: every fix so far has been red then green at the three agreed test seams (`run_daily`, `analyse_rhp`, `ingest`).
- `superpowers:verification-before-completion`: run the suite, then a live `run_daily.py --smoke` after any change to models or limits. It costs about $0.02.
- `code-review` or a reviewer sub-agent: before merging anything that touches `budget.py`, `verifier.py` or `grounding.py`.
- `mattpocock-skills:diagnosing-bugs`: for live-run anomalies, starting from the `llm_calls` ledger and the per-run log.
