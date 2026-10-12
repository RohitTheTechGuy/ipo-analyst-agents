# IPO Analyst Agents

> [!CAUTION]
> ## ⚠️ THIS IS NOT INVESTMENT ADVICE
> This project is an **experimental, educational software project**. Its reports, BUY / NO BUY calls and Telegram messages are **generated automatically by AI models** from IPO prospectuses and public sources. **They can be incomplete or wrong.**
>
> - Nothing here is a recommendation to buy, sell or apply for any security.
> - The author is **not a SEBI-registered investment adviser or research analyst**.
> - Grey-market premium (GMP) figures are **unofficial**.
> - **Do your own research and consult a SEBI-registered adviser before investing.** You alone are responsible for your investment decisions and any losses.

A daily multi-agent system that reads the prospectus (RHP) of every **Indian mainboard IPO** open that day. It produces a verified analyst report and a **BUY / NO BUY** call, and sends them to Telegram: a short plain-English summary plus a full PDF. It then follows the IPO with daily grey-market (GMP) and subscription updates until the IPO closes.

Built with **Python · LangGraph · OpenAI (GPT-5.6 Luna / Terra, GPT-6.1 Sol) · Groq · Telegram**.

> Recommend and remind only: it never places a bid. **Not investment advice.**

---

## What you get on Telegram

```
📄 Fusion CX Ltd. IPO
Apply 14 Oct–16 Oct · ₹275–₹289 a share
Smallest application: 51 shares = ₹14,739

🧭 Our call: NO BUY (high confidence)
Do not apply because serious questions about the company's accounts outweigh its growth.

Why:
• The auditor found bank statements that did not match the books, and delays in required payments.
• The top 10 customers provide 52.17% of sales, and the US provides 85.26%.
• People inside the company bought shares at ₹72.2 on average; you would pay up to ₹289.
Watch out:
• The company has given loans to firms linked to its owners.
📊 Price vs similar listed companies: P/E 24.6× vs peer median 26.3× (7% discount)

📈 Unofficial pre-listing trading suggests shares may open about 7% above ₹289.

Full analysis in the attached PDF. Not investment advice.
```

The **PDF report** follows the message. It covers the offer and use of funds, financials, risk factors, legal and governance, business and industry, peer valuation (each peer's numbers, and whether it is really a peer), market sentiment, the analyst view and the advisor's call. Every figure carries its RHP page.

While the IPO is open, a **daily update** reports the GMP and subscription. On the **closing day**, the advisor gathers fresh news and makes its call again.

---

## How it works

```
launchd 10:30 IST ─► discover open mainboard IPOs (NSE + BSE + Chittorgarh) ─► skip already analysed
     │
     ▼  per new IPO with a final RHP
download RHP ─► ingest (PDF → 8 section groups, OCR fallback)
     │
     ├─► 8 section agents in parallel: map → reduce → verify → re-ask (max 2)
     ├─► Market Sentiment: articles, GMP, subscription
     └─► Peer Valuation: the agent picks business-specific multiples (EV per employee, per subscriber, P/E…)
     │
     ▼
Synthesizer (analyst view) ─► Python checks and calcs ─► Financial Advisor (BUY / NO BUY)
     │
     ▼
PDF ─► Summary Editor (plain English) ⇄ Summary Eval (rulebook + judge, 1 revision) ─► Telegram: ONE summary + PDF
```

Full agent map, models, context flow and diagrams: **[`docs/agents.md`](docs/agents.md)**. Design spec: [`docs/specs/ipo-agents.md`](docs/specs/ipo-agents.md).

### Design principles

- **Models read, Python decides the numbers.**
  - Every RHP figure is a `Fact` (value, unit, page, verbatim quote). The verifier checks it against the page's text.
  - Ratios and multiples are computed in Python from verified facts.
  - Free-text prose that contains an unchecked number is dropped.
- **Nothing is made up.** A missing value stays "not disclosed" or "could not verify". It is never filled in.
- **Agents reason where reasoning matters.** Examples:
  - The peer-valuation agent chooses how this kind of business is valued.
  - The peer-fit check judges whether each listed peer really is one.
  - The advisor weighs the whole report.
- **Fail soft.** If an optional step fails, such as sentiment, the advisor or the plain summary, the run still sends the report and states what is missing.
- **Hard budget.**
  - Each IPO is capped at **$5**: $4 for the analysis plus $1 for the advisor and the summary.
  - Every call is metered against an SQLite ledger.
  - Near the cap, the run moves Terra tasks down to cheaper models.

### Models (routing in `config.yaml`)

| Key | Model | Used for |
|---|---|---|
| `luna` | gpt-5.6-luna | bulk RHP reading, descriptive sections |
| `terra` | gpt-5.6-terra | decision-critical sections, synthesizer, peer-fit check, summary editor |
| `sol` | gpt-6.1-sol | Financial Advisor, choice of peer multiples, summary judge |
| `groq` | gpt-oss-120b | small calls (verifier, stance tags), with a fallback to Luna |

---

## Setup

Needs macOS with Homebrew (the scheduler uses `launchd`) and Python 3.14 (what the venv was built with).

```bash
brew install pango tesseract          # PDF rendering (WeasyPrint) and OCR
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                  # then fill it in
```

`.env` keys (see [`.env.example`](.env.example)):

| Key | Required | For |
|---|---|---|
| `OPENAI_API_KEY` | yes | all model calls |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | yes | sending summaries and PDFs |
| `GROQ_API_KEY` | no | free small calls; without it they run on Luna |
| `TAVILY_API_KEY` | no | news search for sentiment |
| `XAI_API_KEY` | no | reserved for an xAI (Grok) provider; not wired in yet |

Discovery, GMP, subscription and peer prices come from public sites and need no keys.

## Usage

```bash
.venv/bin/python run_daily.py --dry-run            # what would run today; no model calls, no Telegram
.venv/bin/python run_daily.py                      # the daily run
.venv/bin/python run_daily.py --ipo FUSIONCX       # one open IPO only (no daily updates for others)
.venv/bin/python run_daily.py --ipo FUSIONCX --force   # re-run it, within what is left of its budget
.venv/bin/python run_daily.py --date 2026-10-14    # run as if on another date
.venv/bin/python run_daily.py --smoke              # ~$0.03 pre-flight check of every model and schema
.venv/bin/python scheduler/install.py install      # schedule weekdays at 10:30 IST (launchd)
```

Outputs:
- **Reports:** `reports/<ipo_id>/<run>/` holds `report.json`, the PDF, the message sent, the full summary and the summary review.
- **Working files:** `data/work/<ipo_id>/`.
- **Logs:** `logs/`.

## Tests and evals

```bash
.venv/bin/pytest -q -p no:warnings                 # 550+ offline tests; no network, no spend
.venv/bin/python -m evals.summary_eval --offline   # summary rulebook only, free
.venv/bin/python -m evals.summary_eval             # editor + judge on saved reports, ~$0.10–0.25
```

- **Tests:** they fake only the outer edges (exchange, downloader, LLM, Telegram) and run the real graph.
- **Golden RHPs:** `tests/golden/` holds Bajaj Housing Finance, Hyundai Motor India and Ola Electric, to check extracted figures.
- **Summary eval:** it runs in every daily job before sending and can also be run offline. See [`evals/eval.md`](evals/eval.md).

## Repository layout

```
ipo_agents/
  cli.py, runner.py          entry point; the daily run (discover → analyse → notify)
  analysis.py                the LangGraph graph per IPO (checkpointed)
  section_agent.py           map → reduce → verify → re-ask loop; sections.py has the prompts
  verifier.py, grounding.py  fact and quote verification; number grounding for prose
  valuation.py, kpis.py, peers.py   peer valuation, RHP KPI multiples, NSE/Yahoo peer data
  sentiment.py, market.py    news stance, GMP, subscription
  synthesizer.py, advisor.py analyst view; BUY / NO BUY
  plain_summary.py, summary_judge.py   plain-English Telegram summary and its eval gate
  calc.py, fin_calc.py, legal_calc.py, flags.py, cross_checks.py   deterministic maths and flags
  budget.py, router.py, llm.py   metering, model routing, provider boundary
  discovery/, notify/, report/   IPO sources, Telegram and reminders, PDF renderer
evals/        summary eval (eval.md, summary_eval.py, results/)
docs/         agents.md, specs/, plans/
scheduler/    launchd installer
tests/        offline test suite, fakes, golden RHPs
config.yaml   models, prices, routing, budgets, paths
run_daily.py  CLI wrapper
```

---

## ⚠️ Disclaimer: NOT INVESTMENT ADVICE

**This is not investment advice.** All output is AI-generated analysis of RHPs and public sources. It may contain errors or omissions and must not be relied on for investment decisions. The author is not a SEBI-registered investment adviser or research analyst. GMP figures are unofficial. Consult a SEBI-registered adviser and read the full offer document before investing. Investing in IPOs carries risk, including loss of capital.
