# Telegram summary eval

## What it is for

After an IPO is analysed, its Telegram summary goes to a reader who may have **no finance background**.

The **summary editor** writes that message. It is a model call in `ipo_agents/plain_summary.py` that rewrites the full expert summary.

This eval checks that the editor's message meets two goals:

1. **Easy to read.** Someone who has never bought a share understands every line.
2. **Only what decides the buy.** It keeps only what moves "apply or don't apply". Everything else stays in the attached PDF.

The eval runs **inside the daily job**. Every newly analysed IPO's message is evaluated before it is sent, and **exactly one summary is sent**. The same loop can also be run by hand on saved reports (the offline regression), to check a prompt or model change before it reaches Telegram.

## What happens in the daily run (per newly analysed IPO)

```
full summary (expert, ~45 lines)
        │
        ▼
summary editor (Terra) ──► draft 1
        ▲                     │
        │                     ▼
        │        ┌── 1. Rulebook (Python, free): fixed pass/fail rules
        │        └── 2. Judge (Grok, another company): five scores 1–5
        │                     │
        │          passes? ───┼── yes ──► send draft 1
        │                     │ no
        └── feedback ◄────────┘   (rule problems, low scores + reasons,
            (1 revision)           jargon to explain, points to add, filler to drop)
                     │
                     ▼
               draft 2 ──► rulebook + judge
                     │
     ┌───────────────┼──────────────────────────────┐
     ▼               ▼                              ▼
passes: send it   fails, but a draft has          no draft has
                  faithfulness 5: send the best   faithfulness 5: send
                  one (fewest rule problems,      the full summary
                  then highest total score)
```

- **One message.** Exactly one summary goes to Telegram, followed by the PDF.
- **Never unscored.** A draft is never sent unless the judge scored it. If the editor or the judge errors or hits the budget cap, the full summary is sent.
- **Cost:** about $0.03–0.06 per IPO (1–2 rounds), billed to the IPO's advisor budget. The $5 per IPO cap doesn't change.
- **Archive:** everything is kept in `reports/<ipo_id>/<run>/`:
  - `telegram_summary.txt`: what was sent
  - `telegram_summary_full.txt`: the full summary
  - `summary_review.json`: every draft, with its rule problems, scores and pass/fail

## The offline regression run

`evals/summary_eval.py` runs the same editor-and-judge loop (`plain_summary.evaluated_summary`) on fixed cases and writes a report:

```
saved reports + fixtures ──► same loop as above ──► table in the terminal + evals/results/<date>_<time>.md
```

### The cases

| Case | Source | What it tests |
|---|---|---|
| FusionCX | saved live report (`reports/FUSIONCX-…/report.json`) | a real NO BUY with many red flags |
| HD Fire | saved live report (`reports/HDFIRE-…/report.json`) | a real NO BUY with governance issues |
| Fixture: BUY | `tests/report_fixture.py` | a BUY call for listing gains |
| Fixture: no advisor call | the same, with no advisor call | the message falls back to the analyst view |

## The two checks (the same in the daily run and the regression)

### 1. Rulebook: fixed checks in Python, no model, no cost

The rulebook is `plain_summary.problems()`. A message fails if it breaks any of these rules:

| Rule | Why |
|---|---|
| At most 15 non-blank lines | Telegram is read on a phone; detail belongs in the PDF |
| No jargon (P/E, OFS, PAT, EBITDA, WACA, QIB, GMP, "promoter", "listing gain", goodwill, capital spending, working capital, board independence, related party, cash generation, "×", …) | the reader doesn't know these words. Basic market words from the news (listed company, stock market, subscribed, auditor) are allowed |
| No flag codes such as `OFFER_PRICE_FAR_ABOVE_WACA` | they are internal labels, not English |
| Every number appears in the full summary | the model can't invent or compute figures |
| No line over 30 words | it must be a quick read |
| The call is shown exactly as the code wrote it | the model never changes BUY / NO BUY |

Two lines are exempt from the jargon rule because the code writes them on purpose:
- the 🧭 call line
- the 📊 peer-multiples line (e.g. "P/E 24.6× vs peer median 26.3×"), which is kept unchanged at the user's request

### 2. Judge: a model scores the message (`ipo_agents/summary_judge.py`)

A **model from another company** than the editor's (Grok 4.20 from xAI, routing `summary_judge`; the editor is OpenAI's Terra) reads the full summary and the plain message side by side. Using another company's model stops the editor's family grading its own work.

The code writes some lines that are correct by construction: the header, the 🧭 call, the 📊 peer line and the footer. The judge ignores them and scores the rest from 1 to 5:

| Score | Question it answers | What lowers it |
|---|---|---|
| **readability_non_finance** | Would someone who knows everyday words (IPO, share, ₹, crore, sales, profit) and basic market words (listed company, stock market, subscribed, auditor) understand every line? | any unexplained accounting or finance term ("cash from operations", "goodwill", "capital spending") |
| **decision_relevance** | Does every line help decide "apply or not"? | filler: true but irrelevant lines (business trivia, a bare article count) |
| **coverage** | Are the decisive points there: the 2–3 main reasons, the biggest risk, price vs similar companies, the listing signal? | a missing point that would change the decision |
| **faithfulness** | Does it say the same as the full summary, no more and no less? | any contradiction, overstatement or new claim (capped at 2) |
| **no_repetition** | Is each point made only once? | the same point twice, even in other words |

**How faithfulness differs from the number rule:** the rulebook already catches invented numbers. Faithfulness catches the subtler case where the numbers are right but the meaning shifts. Real catches:
- "loans to the owners" when the source said loans to **firms linked to** the owners
- "approval to expand" when the source said permission to **operate**
- "bought at ₹72.2" when the source said ₹72.2 is an **average**

**How decision relevance differs from coverage:**
- Decision relevance asks whether anything in the message should be taken out.
- Coverage asks whether anything missing should be put in.

The judge also lists:
- the exact jargon words left
- the decisive points missing
- the filler lines

These lists tell you what to fix in the editor's prompt.

### Pass bar

A draft passes only if all of these hold:
- the rulebook finds no problem
- every judge score is **4 or higher**
- **faithfulness is 5**: a wrong message is worse than a hard-to-read one

## How to run the offline regression

```bash
# Rulebook only, on today's full summaries. No model, no cost: a baseline.
.venv/bin/python -m evals.summary_eval --offline

# The full loop, as in the daily run: draft, then rulebook + judge, then at most one revision.
.venv/bin/python -m evals.summary_eval
```

- **Cost:** about $0.10–0.25 per run (4 cases, 1–2 rounds each). Spend is logged on its own budget ledger, `eval-summary`, capped at $1.00 in total (`--cap` changes it), separate from every IPO's budget.
- **No side effects:** it sends no Telegram message and never analyses an IPO.
- **Output:** a terminal table: pass, what would be sent (plain or full), the number of drafts, the line count and each score. Full details go to `evals/results/<date>_<time>.md`.

**Tuning the editor:** run the regression, read the judge's lists, adjust the editor prompt (`SYSTEM` in `plain_summary.py`), and run it again. The live gate then sends better messages.

## Results so far (2026-10-12, single drafts, before the in-run gate and revision existed)

| Run | Change | Readability | Relevance | Coverage | Faithfulness | Repetition |
|---|---|---|---|---|---|---|
| Baseline (`--offline`) | today's full summary | fails rulebook: 34–46 lines, jargon, flag codes | | | | |
| 1 | first editor prompt | 2 | 4 | 3–5 | 2 | 3–4 |
| 2 | judge told which lines the code writes; prompt with plain-word examples | 3 | 4–5 | 3–4 | 2–5 | 3–4 |
| 4 | peer line kept as is; "plainer, never broader" | 3–4 | 5 | 3–4 | **5** | 3–4 |
| 2026-10-15, with revision | in-run gate + 1 revision, before tuning: 0/4 pass | 3–4 | 5 | 3–5 | 5 | 3–4 |
| 2026-10-15, tuned | basic market words allowed; accounting terms in the rulebook; opening line = one reason; listing signal only once; no "cheap/expensive" unless the source says so: 1/4 pass | 3–4 | 4–5 | 4–5 | **5** | 3–5 |

**Still to fix:**
- **Readability:** phrases like "pre-listing trading" and "listed companies".
- **Coverage:** sometimes misses a risk, e.g. FusionCX's acquisition and goodwill risk.
- **Length:** about 19 lines against the 15-line target. The rulebook counts only non-blank lines, so it passes.

## Files

| File | What it holds |
|---|---|
| `ipo_agents/plain_summary.py` | the editor (prompt, live checks, layout), the rulebook (`problems`) and the draft → evaluate → revise loop (`evaluated_summary`) |
| `ipo_agents/summary_judge.py` | the judge: prompt, scores (`SummaryScores`), pass bar, feedback for the revision |
| `ipo_agents/runner.py` | `_plain_text`: runs the loop in the daily job and sends the one chosen summary |
| `evals/summary_eval.py` | the offline regression: cases, the loop, the report |
| `tests/test_plain_summary.py` | offline tests for the checks, the loop (revision, faithful-only sending, fallbacks) and the rulebook |
| `config.yaml` | `summary_editor: terra`, `summary_judge: grok` |
| `evals/advisor_compare.py` | the Financial Advisor on Sol vs Grok on saved reports, side by side (no routing change) |
| `evals/results/` | one Markdown report per offline run |
