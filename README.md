# IPO Analyst Agents

> [!CAUTION]
> ## ⚠️ THIS IS NOT INVESTMENT ADVICE
> This project is an **experimental, educational software project**. Its reports, BUY / NO BUY calls and Telegram messages are **generated automatically by AI models** from IPO prospectuses and public sources. **They can be incomplete or wrong.**
>
> - Nothing here is a recommendation to buy, sell or apply for any security.
> - The author is **not a SEBI-registered investment adviser or research analyst**.
> - Grey-market premium (GMP) figures are **unofficial**.
> - **Do your own research and consult a SEBI-registered adviser before investing.** You alone are responsible for your investment decisions and any losses.

A daily multi-agent system for **Indian mainboard IPOs**. Each weekday it lists the IPOs open that day on Telegram, and for each one you pick it reads the prospectus (RHP). It produces a verified analyst report and a **BUY / NO BUY** call, and sends them to Telegram: a short plain-English summary plus a full PDF. It then follows the IPO with daily grey-market (GMP) and subscription updates until it lists, and answers your questions about any analysed RHP right in the chat.

Built with **Python · LangGraph · OpenAI (GPT-5.6 Luna / Terra, GPT-6.1 Sol) · xAI (Grok 4.20) · Groq · Telegram**.

> Recommend and remind only: it never places a bid. **Not investment advice.**

---

## What you get on Telegram

Each weekday at 10:30 IST, a numbered list of the new IPOs ready for analysis. Nothing is analysed (or paid for) until you reply:

```
🆕 IPOs open and ready for analysis (up to $5.00 each). Reply with numbers or symbols to analyse, e.g. "1,3":
1. TONBO — Tonbo Imaging India Limited (closes 17 Oct)
2. ZETWERK — Zetwerk Manufacturing Businesses Limited (closes 17 Oct)

Reply "all" for every one, or "none" to skip; "stop" stops an analysis that is running. GMP/subscription updates for analysed IPOs continue either way.
Ask anything about an analysed IPO's RHP, e.g. "ZETWERK: how much debt does it have?"
```

On a day with no IPO to offer and no update due, you get one line instead: "😴 No mainboard IPO open today, and no updates due. The daily check ran fine." So no message at all means something is wrong.

Reply `1`, `ZETWERK`, `1,2`, `all` or `none`. A choice made at the terminal prompt gets the same messages. Within about a minute the bot replies in three steps:

```
✅ Got it: analysing ZETWERK (Zetwerk Manufacturing Businesses Limited), up to $5.00 each. The report arrives here when it is done.
🚀 Analysis started: ZETWERK (Zetwerk Manufacturing Businesses Limited). Reply "stop" or "cancel" to stop it.
```

- **To stop:** reply `stop` or `cancel` while it runs. The bot checks every 20 seconds and stops at the next model call, usually within a minute: "⏹ Stopped at your request. ZETWERK: stopped part-way, $0.84 spent. Its progress is saved: reply ZETWERK to continue from there." Spend so far is not refunded, and IPOs picked but not yet started are skipped.
- **A reply the bot doesn't fully understand starts nothing.** "cancel 2" never starts IPO 2.
- **When the analysis is done,** you get the summary below:

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

While the IPO is open, a **daily update** reports the GMP and subscription. On the **closing day**, the advisor gathers fresh news and makes its call again, and a **demand check** (Grok) judges that call against the final subscription: why demand is high or low, what retail demand says, and, when demand contradicts the call (a NO BUY on a heavily subscribed IPO, say), why the call still holds or why demand weakens it. An IPO analysed on its closing day gets the demand check as a message right after its summary.

**After close,** an update goes out each weekday until the expected listing day: GMP, final subscription and the latest call ("📈 Zetwerk IPO · bidding closed Mon 19 Oct, expected to list Thu 22 Oct", then "🔔 … is expected to list TODAY"). The listing day is counted as the third weekday after close (SEBI's T+3); exchange holidays aren't known, so in a holiday week the real listing can be a day or two later. These updates make no model calls.

### Ask about an analysed RHP

Send a question in the chat, e.g. `ZETWERK: how much debt does it have?`, then follow-ups like `and the year before?`. Within about a minute the bot answers from that IPO's RHP only, citing PDF pages:

```
As at June 30, 2026, the company had total borrowings of ₹19,421.92 million (p. 602). …

💬 From the ZETWERK RHP (Claude, your subscription). Not investment advice.
```

- **It costs nothing extra.** Answers come from the Claude Code CLI on the Mac, logged in with your Claude subscription; they count against the plan's usage limits. The bot checks `claude auth status` before each answer and refuses unless the login is the subscription one; API-key variables are removed from its environment, so it can never bill an API account.
- **Only analysed IPOs.** A question naming an IPO that isn't analysed gets "JIO isn't analysed, so there's no RHP to answer from." A question about another company inside an RHP's chat is refused too.
- **The conversation follows the chat.** Use Telegram's *Reply* on any earlier message (a summary, an update, an answer) and ask, e.g. reply `why?` to a summary: the question is about that message's IPO, and Claude sees the message you replied to. Without a reply, the IPO is the one named, else the chat's current one (the last one answered about, or the last summary sent), else the only one analysed. Otherwise the bot asks which. Follow-ups such as `and the year before?` continue the same conversation per IPO.
- **What counts as a question:** a reply ending in `?`, starting with `ask`, or of four words or more. A short reply that isn't a pick or a stop (`why`, `more on debt`) is also a question when it replies to an earlier message, or comes within 30 minutes of an answer; otherwise it gets "Didn't recognise" as before. `1` in reply to the offer is still a pick.
- **RHP first, web on request.** Answers come from the RHP alone unless you ask for the web (`search the web`, `check online`, `google it`, `internet`): that one answer may also search the web, citing each site and date. The web is otherwise used only to download the RHP.
- **Locked down:** the CLI runs in the IPO's folder with read and search tools only (no shell, writes or MCP servers, and web only on request), and without your settings, CLAUDE.md files or Claude Code memory. RHP text is treated as data, never as instructions.
- **Speed:** the first question on an IPO extracts its pages once (about a minute for a 700-page RHP with OCR). After an answer the bot keeps listening for 10 minutes, so follow-ups come back in seconds. Questions sent while an analysis is running are answered when it finishes.
- **Limits:** answers are not run through the Python number checks that guard the report. Check the cited page when it matters.

---

## How it works

```
launchd 10:30 IST ─► discover open mainboard IPOs (NSE + BSE + Chittorgarh) ─► skip already analysed
     │                 ─► GMP/subscription updates ─► Telegram: numbered list of new IPOs
     ▼  per IPO you reply with (read every minute by a second launchd job; questions are answered there too)
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

### What a run actually costs

Measured from the spend ledger (`data/ipo.db`, table `llm_calls`) on the two IPOs analysed so far:

| Step | Ledger | HD Fire (460-page RHP) | Fusion CX (559 pages) |
|---|---|---|---|
| RHP reading: 111–156 Luna page-batch calls | analysis | $0.26 | $0.46 |
| Terra sections: price basis, financials, fine print, offer | analysis | $0.87 | $1.03 |
| Luna sections: business, governance, risks, other | analysis | $0.11 | $0.13 |
| Synthesizer (Terra) | analysis | $0.08 | $0.08 |
| Sentiment (stance tags on Groq, free, or Luna) | analysis | $0.01 | $0.01 |
| Peer valuation: Sol multiples plus the Terra peer-fit check | analysis | ~$0.02 | ~$0.05 |
| **Analysis subtotal** (cap $4.00) | | **~$1.35** | **~$1.76** |
| Advisor (Sol), analysis day | advisor | $0.09 | $0.09 |
| Summary editor (Terra) plus judge (Grok), 1–2 rounds | advisor | ~$0.02–0.04 | ~$0.02–0.04 |
| Closing day: fresh sentiment, advisor again, demand check (Grok) | advisor | ~$0.15 | ~$0.15 |
| **Advisor subtotal** (cap $1.00) | | **~$0.28** | **~$0.28** |
| **Per IPO, start to close** (cap $5.00) | | **~$1.63** | **~$2.04** |

- The cost grows with the RHP's length, mostly from page reading and the Terra sections. Expect about $1.50–2.50 per IPO.
- The other daily updates (GMP and subscription) make no model calls and cost $0.
- Outside any IPO:
  - `--smoke` costs about $0.014;
  - `evals.summary_eval` costs about $0.10;
  - `evals.advisor_compare` costs about $0.28.
- Some figures are estimates, marked ~. The two analyses ran before the peer-fit check and the Grok judge existed, so those rows use what the same calls cost in later runs.

### Models (routing in `config.yaml`)

| Key | Model | Used for |
|---|---|---|
| `luna` | gpt-5.6-luna | bulk RHP reading, descriptive sections |
| `terra` | gpt-5.6-terra | decision-critical sections, synthesizer, peer-fit check, summary editor |
| `sol` | gpt-6.1-sol | Financial Advisor, choice of peer multiples |
| `grok` | grok-4.20 reasoning (xAI) | closing-day demand check; summary judge: another company's model than the editor's, so it never grades its own family's writing |
| `groq` | gpt-oss-120b | small calls (sentiment stance tags), with a fallback to Luna |

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
| `XAI_API_KEY` | yes, while a routed model uses xAI | the summary judge (Grok) |

Discovery, GMP, subscription and peer prices come from public sites and need no keys.

### Keeping it running

`scheduler/install.py install` loads two launchd jobs: the 10:30 weekday run, and the job that reads your Telegram replies every minute. They run without Claude Code or a terminal open, but only while the Mac is on and logged in. To recover from sleep, shutdowns and power cuts on their own:

```bash
sudo pmset repeat wakeorpoweron MTWRF 10:25:00   # wake or power on before the 10:30 run
sudo pmset -a autorestart 1                      # boot again after a power cut
```

After a boot, the jobs start only once you are logged in. Logging in by itself needs FileVault off and automatic login on. That is a security trade-off: anyone with the machine can read the disk, `.env` keys included. If a run was missed, run `run_daily.py --ask-telegram` once you are back.

## Usage

```bash
.venv/bin/python run_daily.py --dry-run            # what would run today; no model calls, no Telegram
.venv/bin/python run_daily.py                      # the daily run: updates, then asks which new IPOs to analyse (Enter = none)
.venv/bin/python run_daily.py --updates-only       # analyse no IPO; only GMP/subscription updates for analysed ones
.venv/bin/python run_daily.py --ask-telegram       # updates, then offer new IPOs on Telegram (the scheduled run)
.venv/bin/python run_daily.py --telegram-picks     # analyse the IPOs picked by Telegram reply, answer RHP questions (scheduled every minute)
.venv/bin/python run_daily.py --ipo FUSIONCX       # one open IPO only (no daily updates for others)
.venv/bin/python run_daily.py --ipo FUSIONCX --force   # re-run it, within what is left of its budget
.venv/bin/python run_daily.py --date 2026-10-14    # run as if on another date
.venv/bin/python run_daily.py --smoke              # ~$0.02 pre-flight check of every model and schema
.venv/bin/python scheduler/install.py install      # weekdays 10:30 IST updates + Telegram offer, and the 5-min picks job (launchd)
```

Outputs:
- **Reports:** `reports/<ipo_id>/<run>/` holds `report.json`, the PDF, the message sent, the full summary and the summary review.
- **Working files:** `data/work/<ipo_id>/`.
- **Logs:** `logs/ipo-agents.log`; launchd output in `logs/launchd*.log` (`launchd-picks*` for the reply job).

## Tests and evals

```bash
.venv/bin/pytest -q -p no:warnings                 # 550+ offline tests; no network, no spend
.venv/bin/python -m evals.summary_eval --offline   # summary rulebook only, free
.venv/bin/python -m evals.summary_eval             # editor + judge on saved reports, ~$0.10
.venv/bin/python -m evals.advisor_compare          # Financial Advisor on Sol vs Grok on saved reports, ~$0.28
```

- **Tests:** they fake only the outer edges (exchange, downloader, LLM, Telegram) and run the real graph.
- **Golden RHPs:** `tests/golden/` holds Bajaj Housing Finance, Hyundai Motor India and Ola Electric, to check extracted figures.
- **Summary eval:** it runs in every daily job before sending and can also be run offline. See [`evals/eval.md`](evals/eval.md).

## Repository layout

```
ipo_agents/
  cli.py, runner.py          entry point; the daily run (discover → pick → analyse → notify)
  analysis.py                the LangGraph graph per IPO (checkpointed)
  section_agent.py           map → reduce → verify → re-ask loop; sections.py has the prompts
  verifier.py, grounding.py  fact and quote verification; number grounding for prose
  valuation.py, kpis.py, peers.py   peer valuation, RHP KPI multiples, NSE/Yahoo peer data
  sentiment.py, market.py    news stance, GMP, subscription
  synthesizer.py, advisor.py analyst view; BUY / NO BUY
  plain_summary.py, summary_judge.py   plain-English Telegram summary and its eval gate
  calc.py, fin_calc.py, legal_calc.py, flags.py, cross_checks.py   deterministic maths and flags
  budget.py, router.py, llm.py   metering, model routing, provider boundary
  discovery/, notify/, report/   IPO sources, Telegram (offer, picks, reminders), PDF renderer
evals/        summary eval (eval.md, summary_eval.py, results/)
docs/         agents.md, specs/, plans/
scheduler/    launchd installer: daily run + Telegram picks job
tests/        offline test suite, fakes, golden RHPs
config.yaml   models, prices, routing, budgets, paths
run_daily.py  CLI wrapper
```

---

## ⚠️ Disclaimer: NOT INVESTMENT ADVICE

**This is not investment advice.** All output is AI-generated analysis of RHPs and public sources. It may contain errors or omissions and must not be relied on for investment decisions. The author is not a SEBI-registered investment adviser or research analyst. GMP figures are unofficial. Consult a SEBI-registered adviser and read the full offer document before investing. Investing in IPOs carries risk, including loss of capital.
