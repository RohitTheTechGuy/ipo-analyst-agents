# Session handoff: IPO Analyst Agents

## Goal for the next session
Build the **IPO Analyst Agents** system: a daily Python/LangGraph pipeline that analyses each open Indian mainboard IPO's RHP and sends a verdict and a PDF report to Telegram, capped at **$1.50 of OpenAI usage per IPO**. Build it ticket by ticket with TDD, using sub-agents in git worktrees, with this session acting as the master that reviews and merges into local `main`.

## Where things are
- **Design source:** `ipo-agents-plan.md` (repo root, written by the user). The spec overrides it where they conflict.
- **Spec:** `docs/specs/ipo-agents.md`, labelled `ready-for-agent`. It holds every decision from the grilling (budget rules, models, seams, scope). Read it first.
- **Tickets:** `.scratch/ipo-agents/issues/01…24`. The table below is the source if those files are missing.
- **Environment:** `.venv/` (Python 3.14) has langchain 1.4.4, langgraph 1.2.14, langchain-openai 1.7.0, langchain-anthropic 1.7.5, langchain-groq 1.1.3, python-dotenv, notebook and ipykernel. Versions are pinned in `requirements.txt`. `.gitignore` covers `.venv/`, `.env`, checkpoints and `__pycache__`.
- **Git:** only the initial commit exists, and all of the above is uncommitted. There's **no remote**, and merges stay local. Commit only when the user asks.
- **Not set up yet:** there's no `.env` and no API keys. `pango` and `tesseract` aren't installed (WeasyPrint and OCR need them, and ticket 01/06 runs `brew install`). The Mac's clock is on IST.

## Tickets (approved; blockers are by number)
| # | Ticket | Blocked by |
|---|---|---|
| 01 | Walking skeleton: all 3 seams end to end with fakes, store, config, contracts | — |
| 02 | Golden set draft (3 IPOs: OFS-heavy, loss-making, NBFC). **The user verifies the numbers.** | — |
| 03 | Budget cap + call log (router as the only way to get a model, pre-call worst case, `BudgetExceeded`, refusal on a missing price) | 01 |
| 04 | Discovery (NSE → BSE/Chittorgarh, mainboard filter, dedupe, DRHP wait, "no source" alert) | 01 |
| 05 | Telegram notifier + closing reminders | 01 |
| 06 | Ingestion with 100% coverage (table-of-contents map, boilerplate, OCR, loud failure) | 01, 02 |
| 07 | Offer & Objects grounded facts; the verifier rejects ungrounded numbers as "could not verify" | 03, 06 |
| 08 | Verifier retry loop (max 2, sent the specific failed fields) | 07 |
| 09 | Offer calculations + flags (OFS %, dilution, promoter holding; OFS > 50%, PE exit, GCP near 25%) | 07 |
| 10 | Degrade at 70% + Groq routing/fallback | 03 |
| 11 | Financials (map → reduce) + flags | 07 |
| 12 | Risk Factors + Fine Print & Legal | 11 |
| 13 | Business & Industry agent | 07 |
| 14 | Promoters & Governance agent | 07 |
| 15 | Offer Price Basis agent (KPIs, WACA vs offer price) | 07 |
| 16 | Other Disclosures agent | 07 |
| 17 | Peer valuation | 11, 13, 15 |
| 18 | Sentiment: articles + stance (Tavily, 8 sources or 4 when degraded) | 03 |
| 19 | Sentiment: GMP + subscription status | 18 |
| 20 | Synthesizer verdict + cross-section checks | 09, 12–17, 19 |
| 21 | Resume + partial-budget report | 07 |
| 22 | Full PDF report layout | 20 |
| 23 | Scheduler (launchd) + single-IPO CLI | 04, 05 |
| 24 | Live golden runs (**needs the user's keys + verified golden numbers**) | all |

The tickets that can start immediately are 01 and 02. After 01, tickets 03, 04 and 05 can run in parallel.

## Agreed test seams (TDD; tests only at these)
A: `run_daily(today, adapters)` · B: `analyse_rhp(ipo, rhp_pdf, adapters, budget)` · C: `ingest(rhp_pdf)`. Fakes are used only at the outer boundary: exchange HTTP, the LLM, Tavily, market data and Telegram. Work in vertical slices, red → green, and keep the suite offline with no keys.

## How the user works
- Terse instructions. "yes" means take the recommended option. They redirect often, so keep changes small and reversible.
- They often chain skills (`/grilling`, `/to-spec`, `/to-tickets`, `/handoff`) while plan mode is active, and have rejected ExitPlanMode several times. Don't assume that plan approval means "start building"; the build starts only on an explicit ask.
- They want to approve test seams and ticket breakdowns before anything is published.

## Next steps
1. Confirm that the spec and ticket files exist. If they don't, publish them from this handoff and the spec (see the plan file `~/.claude/plans/also-install-langchain-anthropic-spicy-hartmanis.md`).
2. Start ticket 01 (walking skeleton), with ticket 02 running in parallel as a sub-agent.
3. After each wave: a code-reviewer sub-agent reviews each diff, the full `pytest` suite runs, then the branches merge into local `main`.

## Suggested skills
- `mattpocock-skills:tdd`: the red → green loop at the three seams.
- `mattpocock-skills:codebase-design`: the adapter and seam design in ticket 01.
- `superpowers:using-git-worktrees` + `superpowers:subagent-driven-development`: parallel ticket sub-agents.
- `mattpocock-skills:code-review` (or `/code-review`): master review before each merge.
- `superpowers:verification-before-completion`: before declaring any ticket done.
- `mattpocock-skills:to-tickets`: only if the ticket files need to be re-published.
