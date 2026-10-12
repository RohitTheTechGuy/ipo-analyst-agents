# Session handoff: IPO Analyst Agents

_Updated 2026-10-12, at the end of the peer-valuation session._

**Where to read first:**
- System design: [`docs/specs/ipo-agents.md`](docs/specs/ipo-agents.md).
- [`docs/agents.md`](docs/agents.md): every agent, its model, tools, state and the orchestration diagram. Updated this session to the current code.
- Tickets 01–27: `.scratch/ipo-agents/issues/`. Advisor plan: `docs/plans/27-financial-advisor.md`.
- Lessons: the user's Obsidian vault at `~/Obsidian Docs/IPO Analyst Agents/`.

**Repo:** github.com/RohitTheTechGuy/ipo-analyst-agents (private). `origin/main` is at `99b9abd`. **The working tree has uncommitted work**; see the next section.

## ⚠️ Uncommitted: business-specific multiples (needs a commit once the user OKs it)

The user challenged the old design: "why only these multiples… otherwise why would I need an agent". The agent may now define its own multiples on RHP KPIs.

- The plan is in `~/.claude/plans/for-the-peer-vlauation-zesty-charm.md`.
- `docs/agents.md` § Peer Valuation describes the result.
- 537 offline tests pass.

What changed:

- **`ipo_agents/kpis.py` (new).** Reads the RHP's KPI tables.
  - Looks up a KPI by name with footnote markers ignored (`kpi_key`), and a peer by name, matching on the start of the name.
  - Takes the latest full fiscal year; a stub never counts.
  - Turns units into amounts: rupee KPIs become ₹ crore; counted KPIs keep their scale.
  - Refuses to divide by a %, a ratio or a number of days.
  - `operating_metric` builds the IPO-vs-peers operating table.
- **`contracts.py`.**
  - `MultipleChoice` is open: a standard key, or its own key plus `label`, `numerator` and `kpi`.
  - `MultipleStats` gains `label`, `unit`, `kpi` and a `.title` property.
  - New: `PeerKpi`, `OfferPriceBasis.peer_kpis`, `ValuationPlan.operating_metrics`, `OperatingMetric` and `Valuation.operating`.
  - New LLM fields default to `None`, never `[]` or `""`. Two reasons: OpenAI strict mode rejects non-null defaults (`tests/test_hardening.py`), and reminders reload old `report.json` files.
- **`valuation.py`.**
  - New Sol prompt: think like an analyst, with examples rather than a whitelist.
  - Shows the model the KPI names the RHP discloses.
  - New functions `_ipo_kpi_multiple` / `_peer_kpi_multiple`.
  - `_spread` is now unit-aware.
  - New: `operating_line` (Telegram).
- **Routing.** `config.yaml` sends `peer_valuation` to **sol**.
- **Extraction prompt.** `sections.py` now extracts the peer-KPI table, one item per peer per KPI. It adds that tables often put several peers side by side in one row.
- **Output.**
  - PDF: per-multiple labels and units, plus a "How the business compares" table.
  - Chart: unit-aware.
  - Telegram: an "Operating:" line.

**Live results** (no Telegram sent; scripts in that session's scratchpad, now gone):

| IPO | What the agent chose | Result |
|---|---|---|
| FusionCX | **EV per employee**, by itself | ₹32.6 lakh vs peer median ₹59.6 lakh (−45%), against 3 peers |
| HD Fire | P/E and EV/EBITDA only | Correct: the only peer KPI in its RHP is customer count |

## Already merged this session

- `ecc7b26`: each peer's own financials (growth, margins, RoE) beside the IPO's.
  - **Peer-fit check:** task `peer_fit` on Terra. It gives strong / partial / weak, with commonalities and differences, using Yahoo profiles.
  - **`PEER_SET_WEAK`** note: weak peers are flagged, never dropped (the user's choice).
  - **Telegram:** a Peers block.
- `99b9abd`:
  - A "Nil" RHP amount now reads as ₹0. This fixed HD Fire's EV/EBITDA.
  - Symbol overrides added for Elgi (`ELGIEQUIP`) and Ingersoll-Rand (`INGERRAND`).

## Where things stand

- **Tickets.** All 27 are built. Ticket 24 (golden numbers) still needs the user to hand-verify `tests/golden/*/expected.yaml`.
- **Budget.** $5.00 per IPO: `budget.cap_usd: 4.00` + `advisor.cap_usd: 1.00`. Tests pin the old $1.50 / 4k figures in `tests/conftest.py` on purpose.
- **HD Fire Protect** (`HDFIRE-2026-10-13`).
  - Analysed: `done`, analyst view NEUTRAL, advisor NO BUY.
  - Archive: `reports/HDFIRE-2026-10-13/2026-10-11_1644_run4/`.
  - Its saved report predates this session's valuation changes.
- **FusionCX** (`FUSIONCX-2026-10-14`). RHP and `report.json` are in `data/work/`.
- **Spend this session.**
  - About $1.40 on live checks, mostly re-extracting Basis for Offer Price three times (≈$0.43 each).
  - The meter ledgers `FUSIONCX-2026-10-14-kpicheck` and `HDFIRE-2026-10-13-kpicheck`, plus `-peercheck`, hold that spend. They are separate from the IPOs' own ledgers.
- **Schedule.** The launchd 10:30 schedule is **not installed**; the user is still waiting on it. Install with `.venv/bin/python scheduler/install.py install`.
- **Claude Code.** `~/.claude/settings.json` model is now `opus[1m]`.

## Open items, in the order to tackle them

1. **Commit and push the uncommitted work**, but only after the user says so. Suggested message: "Peer valuation: the agent defines business-specific multiples on RHP KPIs".
2. **Peer-KPI extraction recall is poor and varies between runs.**
   - On FusionCX it got headcount for 3 of 6 peers. IKS (13,331) and Sagility (46,860) sit further along the same row on RHP p. 132 and were dropped.
   - A correctly formed quote *does* verify; I checked offline with `verifier._why_not`. So the cause is the extractor (Luna map, then Terra merge), not the verifier.
   - Proposed fix, which the user hasn't approved yet: a focused extraction pass over just the peer-KPI table pages with a stronger model. Estimate $0.10–0.20 per IPO.
3. **Kirloskar Brothers Limited** (suggested peer for HD Fire) has no NSE match. Add a `symbol_overrides` entry (`KIRLOSBROS`, but verify it live).
4. **"Issue: not disclosed" in the Telegram summary.** Show `calculations["issue_size_cr"]`, labelled as computed.
5. **31 "could not verify" fields**, mostly Business `Quote`s. Normalise hyphenation and dashes in `verifier._quoted`; don't go back to fuzzy matching for Quotes.
6. **Offer Price Basis re-asks cost $0.37** on HD Fire. Peer-KPI extraction makes this section bigger still, so watch it.
7. **Stale comment.** `config.yaml` line ~46 still says "$1.50 + $0.50 = $2.00 per IPO".
8. **Golden-number review** (ticket 24) is the user's task.

## Gotchas (don't regress)

- **Reasoning effort and limits.**
  - Luna runs at `reasoning_effort: none`.
  - Reasoning models get no `temperature`.
  - Map calls have their own `map_max_tokens`.
  - A section whose reply can't be parsed is skipped; provider errors still fail the IPO.
  - Commits: `47b8465`, `7b13dba`, `fbe7d10`, `fed65c3`.
- **Groq.** Governance and Other Disclosures route to Luna, because Groq's limit is 6k per call.
- **Budget resets.** `--reset-budget` only with the user's explicit OK.
- **Running Python.** Use `.venv/bin/python`; there is no `python` on PATH. Scripts outside the repo need `PYTHONPATH=.`.
- **WeasyPrint** needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.
- **Unit detection.** `calc._RUPEE_MARKS` contains `"rs"`, which matches "use**rs**" and "subscribe**rs**". `kpis.py` uses its own word-bounded `_RUPEE` regex for that reason; don't swap it for `to_crore`'s check.
- **Live runs.** For a single section or the valuation step, without sending Telegram, call `section_agent.run_section` + `valuation.run_peer_valuation` with a `MeteredLLM` on a separate ledger id.

## How the user works

- **Instructions.** Terse; "yes" means go ahead with the recommendation.
- **Autonomy.** They want it once a build is approved (memory `build-autonomy`).
- **Needs their OK.** Every spend, reset, commit or push, and anything that touches the outside world.
- **Feedback this session.** They want agents that genuinely reason, not lookup tables. "Otherwise why would I need an agent".
- **Communication.** Keep them informed during long runs.

## Suggested skills

- `mattpocock-skills:tdd`: fixes go red then green at the seams (`run_daily`, `analyse_rhp`, `ingest`). `tests/test_peer_valuation.py` has the KPI fixtures (`KPI_LINES`, `IPO_KPIS`, `PEER_KPIS`, `analyse_kpis`).
- `superpowers:verification-before-completion`: run the full suite, then one cheap live check after changing prompts or models.
- `mattpocock-skills:diagnosing-bugs`: for the peer-KPI recall problem, start from the extracted `peer_kpis` quotes against the page text.
- `code-review`: before committing anything that touches `verifier.py`, `budget.py` or `grounding.py`.
