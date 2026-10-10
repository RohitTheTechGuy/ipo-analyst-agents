# IPO Analyst Agents: Execution Plan

**Stack:** Python · LangGraph · langchain-openai (GPT-5.6 Luna for heavy reading, GPT-5.6 Terra for decision-critical steps) · langchain-groq (small calls) · Telegram Bot
**Scope:** Indian **mainboard** IPOs only · **Recommend + remind** (no auto-bidding) · Runs **daily**, only for **currently open** IPOs · Already-analysed IPOs are skipped

---

## 1. What the system does each morning

```
10:30 IST cron
   │
   ▼
[Discover open mainboard IPOs] ──► [Skip ones already analysed (SQLite)]
   │
   ▼  (for each NEW open IPO)
[Download RHP PDF] ─► [Parse + split into sections + coverage check]
   │
   ├──► Section agents (parallel, one per RHP section group)
   ├──► Peer Valuation agent
   └──► Market Sentiment agent
   │
   ▼
[Grounding Verifier] ──(ungrounded numbers?)──► retry that agent (max 2)
   │
   ▼
[Synthesizer: verdict + reasons + red flags]
   │
   ▼
[Render full report → PDF (Python, no LLM)]
   │
   ▼
[Telegram: summary message + full report as PDF attachment]

Also every run: "Closes today / tomorrow" reminder for IPOs already analysed (no re-analysis)
```

---

## 2. Where LangGraph fits (and where it doesn't)

| Part | Use LangGraph? | Why |
|---|---|---|
| Scheduler (daily trigger) | No | Plain cron / GitHub Actions / systemd timer. |
| Discovering open IPOs, downloading PDFs | No (plain Python) | Deterministic HTTP + parsing. No LLM needed. |
| PDF parsing and section splitting | No (plain Python) | Deterministic. Using an LLM here only adds hallucination risk. |
| **Per-IPO analysis** | **Yes** | Parallel fan-out to many agents (`Send`), join, verifier → retry loop, shared state, checkpointing so a crash mid-run resumes instead of re-spending tokens. This is exactly what LangGraph is good at. |
| Sentiment research | **Yes (tool-calling agent node)** | Open-ended: search → pick sources → fetch → summarise. Needs a ReAct-style loop. |
| Telegram notify | No | One HTTP call. |

**Design choice:** one outer Python script (`run_daily.py`) handles discovery, dedupe and notification. For each new IPO it invokes one **compiled LangGraph analysis graph**. You can wrap the outer loop in LangGraph too if you want everything visualised in LangGraph Studio, but it isn't necessary.

---

## 3. Data sources

| Need | Primary source | Fallback / notes |
|---|---|---|
| List of open mainboard IPOs (name, symbol, dates, price band, lot size) | NSE "current issues" JSON endpoint (the one behind nseindia.com's IPO page) | BSE public-issues page; Chittorgarh IPO list. NSE blocks plain bots: needs browser-like headers + a cookie from a homepage hit first. Keep this behind one `fetch_open_ipos()` function so the source can be swapped. |
| Filter mainboard vs SME | NSE marks SME issues separately (series/segment field) | Also drop anything with "SME" in issue type. |
| RHP PDF | Link on the NSE/BSE issue page | SEBI "Public Issues → RHP" listing; company/BRLM website. Store the PDF + SHA-256 hash. |
| Peer market prices / financials | NSE quote API for live price + market cap | `yfinance` (`TICKER.NS`) as fallback. Screener.in has the best fundamentals but needs scraping/login, so it's optional. |
| Web articles / broker notes | Search API (Tavily or Serper) | Apify RAG web browser actor is an option (you have the Apify plugin). |
| YouTube | YouTube Data API v3 (search) + `youtube-transcript-api` (transcripts) | Apify YouTube scraper actor. |
| GMP / subscription status | Chittorgarh / InvestorGain pages; NSE bid details for live subscription | GMP is unofficial and unregulated. Report it, never base the verdict on it alone. |

---

## 4. Ingestion and the context strategy

**Your idea of one agent per section is right**, with three adjustments:

1. **Some sections are still too big for one call.** Risk Factors (40–60 pages) and Restated Financials (100+ pages) don't fit comfortably. Inside those agents, use **map → reduce**: a fast model extracts facts chunk by chunk (with page numbers), then one call consolidates.
2. **Group small sections** rather than creating 20 agents. Fewer agents means fewer calls and fewer things to keep in sync (see the agent table).
3. **Cross-section checks are needed.** For example: Objects say "repay ₹X debt" → does the debt schedule show it? Litigation amounts → do they appear in contingent liabilities? These checks happen in the **Synthesizer**, which sees all agents' structured outputs (not the raw text).

### "Read everything" guarantee
- Parse the PDF with **PyMuPDF** (text, page-by-page) + **pdfplumber** (tables). OCR fallback (`pytesseract`) only for pages with no extractable text.
- Use the RHP's **Table of Contents** (SEBI ICDR format, so headings are standard) to map page ranges → sections. Fall back to regex on ALL-CAPS headings.
- **Coverage check (hard rule):** every page number 1…N must be assigned to exactly one agent. Anything unmapped goes to the **Other Disclosures** agent. The run fails loudly if coverage < 100%.
- Each chunk carries metadata: `{ipo_id, section, page_start, page_end}`. Every extracted fact must cite a page.

### Standard RHP sections → agent mapping

| RHP section(s) | Agent |
|---|---|
| Summary of the Offer Document, The Offer, Capital Structure, Objects of the Offer, Offer Structure, Terms of the Offer | **Offer & Objects** |
| Industry Overview, Our Business | **Business & Industry** |
| Restated Financial Information, Other Financial Information, MD&A, Capitalisation Statement, Financial Indebtedness | **Financials** |
| Risk Factors | **Risk Factors** |
| Outstanding Litigation & Material Developments, Government & Other Approvals, Related Party Transactions (notes to accounts), Contingent Liabilities (notes), Material Contracts & Documents | **Fine Print & Legal** |
| Our Management, Our Promoters & Promoter Group, Group Companies, History & Corporate Structure, Our Subsidiaries, Dividend Policy | **Promoters & Governance** |
| Basis for Offer Price (incl. KPIs, WACA, peer table) | **Offer Price Basis** (feeds the Peer Valuation agent) |
| Everything else (Definitions, General Information, Key Regulations, Other Regulatory Disclosures, etc.) | **Other Disclosures** (scan for anything material; usually short output) |

---

## 5. The agents

**Total: 11 LLM agents + 1 verifier.**
- 8 section agents (one per section group). In code this is **one generic `section_agent` node** run 8 times in parallel via `Send`, each with its own prompt + output schema.
- 1 Peer Valuation agent
- 1 Market Sentiment agent (tool-calling)
- 1 Synthesizer
- 1 Grounding Verifier (mostly Python; a small LLM is only used for fuzzy quote matching)

Discovery, ingestion, the calculator and the Telegram notifier are plain Python functions, not agents.

All LLM agents run at `temperature=0` and return **Pydantic structured output** via `.with_structured_output(Schema)` on whichever model the router picks (section 6.3). Every numeric field is a pair: `{value, unit, page, verbatim_quote}`.

### 5.1 Non-LLM steps (plain Python)
- **Discovery:** returns open mainboard IPOs. Diffs against SQLite.
- **Ingestion:** download, hash, parse, section-map, coverage check, chunk.
- **Calculator:** *all* arithmetic (CAGR, margins, ratios, multiples, OFS %, dilution). LLMs extract inputs and Python computes. An LLM never does maths.

### 5.2 Section agents

| Agent | Extracts | Key flags it must raise |
|---|---|---|
| **Offer & Objects** | Issue size, fresh issue vs OFS split, each selling shareholder + shares sold + holding after, price band, lot size, post-issue shares, pre/post promoter holding, use of fresh-issue proceeds (amounts by object), anchor/QIB/NII/retail split, lock-in terms | OFS > 50% of issue; PE/VC fully exiting; proceeds mainly for debt repayment or "general corporate purposes" (GCP is capped at 25%, so check how close they are to it); promoter stake after issue |
| **Business & Industry** | What the company sells, to whom, revenue mix (segment/geography/product), top-customer and top-supplier concentration, capacity and utilisation, order book, competitors named, moats claimed | Customer concentration (top 1 / top 10 %), single-plant/single-geography dependence. Industry report is usually commissioned for the IPO, so tag industry claims as "per company-commissioned report" |
| **Financials** | 3 yrs + stub period: revenue, EBITDA, PAT, operating cash flow, capex, debt, cash, net worth, receivables, inventory, payables, ROE/ROCE (as disclosed) | PAT positive but CFO negative; sudden jump in the year before IPO; receivable days rising faster than revenue; auditor qualifications/emphasis of matter; restatement adjustments; stub period not comparable to full year |
| **Risk Factors** | Every risk factor, classified as *company-specific* vs *generic boilerplate*, with the top 10 company-specific ones ranked by severity | Risks quantified with a number in the text (those are real); risks repeated in litigation/financials |
| **Fine Print & Legal** | Litigation against company/promoters/directors/subsidiaries (count + amount by type: criminal, tax, regulatory, civil), contingent liabilities, related-party transactions (sales, purchases, loans, guarantees, rent, remuneration), pending approvals, material contracts with promoter entities | Contingent liabilities vs net worth; RPT revenue > ~15% of revenue; loans to promoter entities; criminal cases against promoters; expired/pending licences |
| **Promoters & Governance** | Promoter background, other businesses, group companies, KMP remuneration, board independence, pledged shares, dividend history, subsidiaries | Promoter pay vs PAT; conflicts of interest (group companies in the same line of business); pledges; frequent auditor/KMP changes |
| **Offer Price Basis** | EPS, NAV, RoNW, P/E at band, peer table *as stated in RHP*, KPIs, weighted average cost of acquisition (WACA) of recent primary/secondary transactions vs offer price | Offer price ≫ recent pre-IPO transaction prices; KPIs that appear only here but not in the financials |
| **Other Disclosures** | Anything material not covered elsewhere | Usually "nothing material", which is a valid output |

### 5.3 Peer Valuation agent
Goal: is the offer price cheap or expensive vs **listed** peers, using the **right** multiples for this kind of business.

1. **Peer set:** start with the peers the RHP itself lists in Basis for Offer Price (SEBI requires this). The LLM may *propose* extra listed peers based on business description. Each proposed peer needs a reason, and it is shown separately as "additional peers (agent-suggested)".
2. **Pick relevant multiples (LLM decides, with reason):** e.g. P/B + RoE for lenders/NBFCs, EV/EBITDA for capex-heavy/manufacturing, P/E for steady earners, EV/Sales or P/S for loss-making/high-growth.
3. **Fetch peer data (tools):** current price, market cap, latest annual PAT/EBITDA/revenue/net worth/debt/cash from NSE / yfinance. Record source + as-of date for every number.
4. **Compute in Python:**
   - IPO market cap at upper band = upper price × post-issue shares (from Offer & Objects agent)
   - IPO P/E, EV/EBITDA, P/B, P/S on the **latest full fiscal year** (stub periods are not annualised unless clearly labelled "annualised, indicative")
   - Peer median / min / max per multiple
   - Premium/discount of IPO vs peer median
5. **Output:** table + one-paragraph read: "Priced at X× vs peer median Y× → Z% premium, justified/not by growth/margins (from Financials agent)".

If peer data can't be fetched, the agent says so. **It never fills a number from memory.**

### 5.4 Market Sentiment agent (tool-calling ReAct node)
Tools: `web_search`, `fetch_page`, `youtube_search`, `youtube_transcript`, `get_gmp`, `get_subscription_status`.

1. Search: `"<company> IPO review"`, `"<company> IPO subscribe or avoid"`, broker notes (e.g. "<company> IPO note"), YouTube `"<company> IPO"`. Only results published **after the RHP date**.
2. Keep max ~8 articles + ~5 videos. Prefer brokers/research houses and established finance media. Drop obvious spam/sponsored content.
3. For each source, record: `{url, title, author/channel, date, stance: subscribe|avoid|neutral|listing-gains-only, key reasons (paraphrased), any numbers they claim}`.
4. Aggregate: stance tally, common bull points, common bear points, GMP (labelled *unofficial*), latest subscription by category if available.
5. Numbers quoted from third parties are tagged **"claimed by <source>"**. They are never mixed into RHP-verified figures.

### 5.5 Grounding Verifier (Python + small LLM)
- For each section agent output: every numeric `value` must be found in its cited page's text (after normalising commas, ₹, lakh/crore/million). The `verbatim_quote` must match the page text (fuzzy match ≥ 0.9).
- For peer/sentiment output: every number must have a source URL/API + timestamp.
- On failure, send that agent back with a list of the specific ungrounded fields → **retry (max 2)** → if it still fails, those fields are dropped and marked "could not verify".

### 5.6 Synthesizer
Input: all *verified* structured outputs (not raw text).
- Cross-section consistency checks (debt repayment vs debt schedule, litigation vs contingent liabilities, KPIs vs financials).
- **Verdict:** `Subscribe (long term)` / `Subscribe (listing gains only)` / `Neutral` / `Avoid`, with a confidence level and the 3–5 reasons that drove it.
- **Top red flags** (ranked), **top positives**.
- Outputs the report **content as structured JSON** (verdict, reasons, red flags, section summaries, each fact with its RHP page). It does not write layout or formatting; the PDF renderer does that.
- May only use facts present in its input. It is told explicitly that inventing a number is a failure.

### 5.7 Notifier (Python)
- Short Telegram message (fits in one screen) + full report attached as **PDF** via `sendDocument`.
- Reminder messages for IPOs closing today/tomorrow (from SQLite, no LLM).

### 5.8 Report renderer (Python, no LLM)
Turns the Synthesizer's JSON + the verified agent outputs into a readable PDF. No LLM touches this step, so nothing can be invented during formatting.

**Tooling:** Jinja2 HTML template + CSS → **WeasyPrint** → PDF. Charts drawn with **matplotlib**, saved as SVG/PNG and embedded. (Alternative: ReportLab, but HTML/CSS is much easier to make look good and to tweak.)

**Layout (about 8–12 pages):**
1. **Page 1, one-glance summary:** company name, dates, price band, lot size and minimum investment; a coloured verdict badge (green Subscribe / blue Listing gains only / grey Neutral / red Avoid) with confidence; 3–5 reasons; top 3 red flags; key numbers tile row (issue size, OFS %, P/E at upper band vs peer median, GMP labelled unofficial).
2. **Offer structure:** fresh issue vs OFS bar, table of selling shareholders, use of proceeds table, promoter holding before/after.
3. **Business snapshot:** what they do, revenue mix chart, customer/supplier concentration.
4. **Financials:** 3-year + stub table; charts for revenue / EBITDA / PAT trend and PAT vs operating cash flow; flagged ratios highlighted.
5. **Valuation vs peers:** peer multiples table + bar chart with the IPO marked; premium/discount to median.
6. **Risks and fine print:** ranked company-specific risks; litigation summary table; contingent liabilities vs net worth; related-party totals.
7. **Promoters and governance:** promoter background, pay, pledges, conflicts.
8. **Market sentiment:** stance tally, bull/bear points, source list with links and dates.
9. **Appendix:** fields that could not be verified, data sources with timestamps, RHP version/hash, disclaimer.

**Readability rules:**
- Every RHP number has a small grey page reference, e.g. `₹412.6 cr (p. 287)`.
- Third-party numbers are in a different style and labelled "claimed by <source>".
- Red flags in a red-tinted callout, positives in a green one. Never colour alone: each also gets an icon or label.
- Indian number formatting (₹ crore, lakh) throughout.
- Same template every time, so reports are easy to compare across IPOs.

---

## 6. LangGraph design

### 6.1 State

```python
from typing import Annotated, TypedDict
import operator

class IPOState(TypedDict):
    ipo: dict                      # name, symbol, dates, price band, lot size, rhp_url
    sections: dict[str, list]      # section_name -> list of chunks (with page metadata)
    findings: Annotated[list, operator.add]   # outputs from section agents (fan-in via reducer)
    peer_valuation: dict
    sentiment: dict
    verification: dict             # per-agent pass/fail + ungrounded fields
    retry_counts: dict[str, int]
    report_json: dict              # Synthesizer output (content only)
    report_pdf_path: str           # set by the renderer
    verdict: dict
```

### 6.2 Graph (illustrative)

```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langgraph.checkpoint.sqlite import SqliteSaver

def fan_out_sections(state: IPOState):
    return [Send("section_agent", {"section": name, "chunks": chunks, "ipo": state["ipo"]})
            for name, chunks in state["sections"].items()]

g = StateGraph(IPOState)
g.add_node("ingest", ingest_node)                 # Python: parse + section map + coverage check
g.add_node("section_agent", section_agent_node)   # one generic node, prompt/schema chosen by section
g.add_node("peer_valuation", peer_valuation_node)
g.add_node("sentiment", sentiment_agent_node)     # ReAct sub-agent with tools
g.add_node("verify", verify_node)
g.add_node("synthesize", synthesize_node)

g.add_edge(START, "ingest")
g.add_conditional_edges("ingest", fan_out_sections, ["section_agent"])
g.add_edge("ingest", "sentiment")                 # runs in parallel with section agents
g.add_edge("section_agent", "peer_valuation")     # peer valuation needs Offer + Financials + Price Basis
g.add_edge(["peer_valuation", "sentiment"], "verify")
g.add_conditional_edges("verify", route_after_verify,   # -> "section_agent" (retry failed only) or "synthesize"
                        ["section_agent", "synthesize"])
g.add_edge("synthesize", END)

graph = g.compile(checkpointer=SqliteSaver.from_conn_string("checkpoints.db"))
graph.invoke(initial_state, config={"configurable": {"thread_id": ipo_id}})
```

Notes:
- `thread_id = ipo_id` means if the run crashes halfway, re-invoking resumes from the last checkpoint instead of re-reading 500 pages.
- The exact join wiring around `Send` + retries needs testing in LangGraph Studio. The shape above is the intent, not final code.

### 6.3 Model routing

Three tiers, each used where it fits:

- **OpenAI GPT-5.6 Luna** (latest Luna): all the heavy RHP reading, plus medium-sized calls.
- **OpenAI GPT-5.6 Terra:** only the steps that most directly drive the buy/skip decision.
- **Groq** (`openai/gpt-oss-120b`): small calls only, because Groq caps tokens per minute and per day (see section 8).

| Job | Model | Why |
|---|---|---|
| Reading every RHP chunk (map step, all 8 section groups) | **Luna** | The heavy part: 300k–500k tokens per RHP. Cheap and has no tight token caps. |
| Consolidating **Offer & Objects** | **Terra** | OFS share, use of proceeds, promoter exit: core to the decision. |
| Consolidating **Financials** | **Terra** | Profit vs cash flow, growth quality, auditor remarks: core to the decision. |
| Consolidating **Fine Print & Legal** | **Terra** | Contingent liabilities, related-party deals, litigation: where the hidden red flags are. |
| **Peer valuation** (incl. Offer Price Basis) | **Terra** | Choosing the right peers and multiples needs judgement. Python still does the maths. |
| **Synthesizer** (verdict) | **Terra** | The final call. |
| Consolidating **Risk Factors** | Luna | Long input. Mainly sorting company-specific risks from boilerplate. |
| Consolidating **Business & Industry** | Luna | Medium input. Descriptive. |
| **Sentiment agent** | Luna | Reads ~13 articles/transcripts, roughly 100k tokens. Too big for Groq's per-minute cap. |
| Consolidating **Promoters & Governance** | Groq, falls back to Luna | Usually small once extracted. |
| Consolidating **Other Disclosures** | Groq, falls back to Luna | Usually "nothing material". |
| **Verifier** fuzzy quote checks | Groq, falls back to Luna | Many tiny calls. |
| Short per-source stance tagging inside sentiment | Groq, falls back to Luna | One small call per source. |

**Routing rule (one `get_model(task, est_tokens)` function):**
- A call goes to Groq only if its estimated size (prompt + expected output) is **under ~6,000 tokens**, the day's Groq usage is under ~80% of the daily cap, and the task is on the Groq list.
- Otherwise, or if Groq returns a 429 rate-limit error, it automatically goes to Luna. Nothing ever waits on Groq.
- Token estimates use `tiktoken` before each call. Daily Groq usage is tracked in SQLite.

All model IDs and the Terra/Luna/Groq assignment per task live in `config.yaml`, so moving a step between tiers is a one-line change:

```yaml
models:
  luna:  { provider: openai, id: gpt-6-luna }
  terra: { provider: openai, id: gpt-5.6-terra }
  groq:  { provider: groq,   id: openai/gpt-oss-120b }

routing:
  map_extract:            luna
  offer_objects:          terra
  financials:             terra
  fine_print_legal:       terra
  peer_valuation:         terra
  synthesizer:            terra
  risk_factors:           luna
  business_industry:      luna
  sentiment_agent:        luna
  promoters_governance:   groq   # falls back to luna
  other_disclosures:      groq   # falls back to luna
  verifier:               groq   # falls back to luna
  sentiment_stance_tag:   groq   # falls back to luna

groq_limits:
  max_call_tokens: 6000
  daily_budget_pct: 80
```

---

## 7. Anti-hallucination rules (non-negotiable)

1. **Extract, don't recall.** Prompts say: "Use ONLY the text provided. If not present, return `null` with reason 'not disclosed in provided pages'."
2. **Every RHP number carries `page` + `verbatim_quote`.** No citation, no number.
3. **All arithmetic in Python.** LLMs output inputs, never results.
4. **Verifier gate** (5.5) before anything reaches the Synthesizer.
5. **Third-party numbers are labelled** "claimed by <source>" and kept separate from RHP-verified numbers.
6. **"Not found" is a valid answer** and is shown in the report as such.
7. **Temperature 0**, structured output, and schema validation (Pydantic raises → retry).
8. **Coverage check** guarantees every page was read by some agent.

---

## 8. Model provider, cost and limits

**Providers:** OpenAI via `langchain-openai` (`ChatOpenAI`) and Groq via `langchain-groq` (`ChatGroq`). LangGraph is provider-agnostic, so the graph design doesn't change. Both return the same message types, so agents don't care which one answers.

**Why Groq only gets small calls:** Groq's published limits (checked 11 Oct 2026) for its main text models are 8,000 tokens/min and 200,000 tokens/day per model. One RHP is 300k–500k tokens, so the heavy reading can't go there. Small calls fit comfortably and cost nothing.

**Prices** (checked 11 Oct 2026, per 1M tokens, from OpenAI's pricing page): GPT-5.6 Luna $0.20 input / $0.02 cached / $1.20 output; GPT-5.6 Terra $2.00 / $0.20 / $12.00.
**Note:** the Luna model in use is `gpt-6-luna`, which wasn't listed on the pricing page when checked. Confirm its price; the Luna rows below assume GPT-5.6 Luna pricing.

**Cost estimate per IPO:**

| Step | Model | Input tokens | Output tokens | Approx. cost |
|---|---|---|---|---|
| Reading every RHP chunk | Luna | ~520k | ~60k | ~$0.18 |
| Offer, Financials, Fine Print consolidation + Peer valuation + Synthesizer + retries | Terra | ~90k | ~20k | ~$0.42 |
| Risk Factors, Business consolidation | Luna | ~40k | ~8k | ~$0.02 |
| Sentiment agent | Luna | ~100k | ~10k | ~$0.03 |
| Promoters, Other Disclosures, verifier, stance tagging | Groq | small | small | $0 |
| **Per IPO** | | | | **~$0.65 (≈ ₹55)** |

- **Per month** (8–10 mainboard IPOs, each analysed once): **about $5–7**. Reminders don't use the LLM.
- Terra is about 2/3 of the cost while handling a small share of the tokens. That's the price of better judgement where it matters most.
- These are estimates. RHP length varies a lot, so log actual tokens and cost per run, per model (LangChain usage metadata), on the first test runs and compare.

**Keeping cost and time down:**
- **Prompt caching:** keep the system prompt identical and at the start of every chunk call. Cached input is 10× cheaper.
- **Parse financial tables in Python** (pdfplumber) rather than sending them to the model where possible. Cheaper and more accurate.
- **Skip pure boilerplate pages** (Definitions, Abbreviations) from LLM reading, but log them as "covered: boilerplate" so coverage stays 100%.
- **Cache** parsed text + chunk extractions by RHP hash, so a re-run never re-reads.
- **Don't use the Batch API.** It's 50% cheaper but can take up to 24 hours, and IPOs are only open about 3 days.
- Process IPOs **sequentially** (parallelism *within* an IPO, not across IPOs), with LangChain's `InMemoryRateLimiter` + exponential backoff on 429s as a safety net.
- **Set a monthly spend limit** in the OpenAI dashboard as a hard cap.

---

## 9. Persistence and dedupe (SQLite)

```sql
CREATE TABLE ipos (
  ipo_id TEXT PRIMARY KEY,       -- exchange symbol + open date
  name TEXT, open_date DATE, close_date DATE,
  price_band TEXT, lot_size INT,
  rhp_url TEXT, rhp_sha256 TEXT,
  status TEXT,                   -- discovered | analysing | done | failed
  verdict TEXT, report_path TEXT,
  analysed_at TIMESTAMP, last_reminded_at TIMESTAMP
);
```

- Daily run: `open IPOs from exchange` minus `status = 'done'` → analyse.
- `failed` IPOs are retried on the next run (checkpoint resumes).
- Reminders: `status='done' AND close_date IN (today, tomorrow) AND last_reminded_at < today`.

---

## 10. Telegram output

**Message 1 (summary):**
```
📄 <Company> IPO (Mainboard)
Open: 13–15 Oct · Band ₹X–₹Y · Lot N (₹Z min)
Issue: ₹A cr (Fresh ₹B cr / OFS ₹C cr → OFS D%)

Verdict: SUBSCRIBE – LISTING GAINS ONLY (confidence: medium)
Why:
• …
• …
Red flags:
• …
Valuation: P/E E× vs peer median F× (G% premium)
Sentiment: 6 subscribe / 2 avoid / 1 neutral · GMP ₹H (unofficial)

Full report attached. Not investment advice; generated from RHP + public sources.
```
**Message 2:** `report_<company>.pdf` via `sendDocument` (Telegram bot file limit is 50 MB; these reports will be well under 2 MB).
**Reminders:** `⏰ <Company> IPO closes TODAY. Verdict was: …`

Bot token + chat ID go in `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Never commit them or paste them into chats.

---

## 11. Project structure

```
ipo-agents/
├── config.yaml              # model IDs, rate limits, thresholds, schedule
├── .env                     # OPENAI_API_KEY, GROQ_API_KEY, TELEGRAM_*, TAVILY/SERPER, YOUTUBE_API_KEY
├── run_daily.py             # entry point: discover → dedupe → graph per IPO → notify
├── discovery/nse.py, bse.py
├── ingestion/download.py, parse.py, sectionize.py, chunk.py
├── llm/router.py            # get_model(task, est_tokens): Terra / Luna / Groq + fallback, usage tracking
├── agents/
│   ├── schemas.py           # Pydantic models with {value, unit, page, quote}
│   ├── prompts/             # one prompt file per section agent
│   ├── section_agent.py
│   ├── peer_valuation.py
│   ├── sentiment.py
│   ├── verifier.py
│   └── synthesizer.py
├── tools/calc.py, market_data.py, search.py, youtube.py, gmp.py
├── graph.py                 # LangGraph wiring
├── report/
│   ├── render.py            # JSON → HTML (Jinja2) → PDF (WeasyPrint)
│   ├── charts.py            # matplotlib charts
│   ├── templates/report.html
│   └── templates/report.css
├── notify/telegram.py
├── store/db.py              # SQLite
└── tests/                   # golden tests on 2–3 past RHPs
```

---

## 12. Scheduling and hosting

- **Run time:** 10:30 IST on weekdays (IPOs open/close on trading days). Cron: `CRON_TZ=Asia/Kolkata 30 10 * * 1-5`. Since this is after market open, the exchange IPO pages are reliably updated for the day. A closing-day reminder at 10:30 still leaves several hours before the UPI mandate cut-off. Optional second run at ~16:00 to catch RHPs/updates posted late.
- **Where:** a small VPS or your own machine with cron is the safest. **GitHub Actions** is free but NSE often blocks cloud IPs, so test discovery from there before relying on it.

---

## 13. Build phases

| Phase | Deliverable | Done when |
|---|---|---|
| 1 | Discovery + SQLite dedupe + Telegram "new IPO open" ping | Correct list of today's open mainboard IPOs arrives on Telegram |
| 2 | Ingestion + section mapping + coverage check | 3 past RHPs map to 100% page coverage with correct section boundaries |
| 3 | Offer & Objects + Financials agents + Calculator + Verifier | Numbers match the RHP by hand-check on 3 past IPOs, zero ungrounded numbers |
| 4 | Remaining section agents | Same bar |
| 5 | Peer Valuation agent | Multiples match a manual calc for 2 past IPOs |
| 6 | Sentiment agent | Sources are real, dated, stance correctly tagged |
| 7 | Synthesizer + full LangGraph wiring + checkpointing | End-to-end run on a past IPO < budgeted tokens |
| 8 | Scheduler + reminders, run live for 2 weeks | Daily messages arrive, no duplicates |

**Golden test set:** pick 3 past mainboard IPOs (one OFS-heavy, one loss-making, one lender/NBFC) and hand-write the key numbers. Every change is re-tested against them.

---

## 14. Risks and open questions

- **NSE anti-bot measures** may break discovery. Keep BSE/Chittorgarh fallbacks.
- **RHP timing:** RHP is filed ~3 working days before opening. If only the DRHP is available on day 1, flag "analysed from DRHP" and re-analyse once when the RHP appears (the hash changes). This is the one exception to "skip if already analysed".
- **Scanned/image tables** in some RHPs need OCR, which is slower and error-prone, so the Verifier matters more there.
- **Model fit per step:** the golden tests decide whether a Luna step needs Terra, or a Terra step could drop to Luna. Move it in `config.yaml`.
- **Two providers = two things that can fail.** Groq calls always fall back to Luna, so a Groq outage only costs a little extra, never a missed report.
- **YouTube transcripts** aren't available for every video (disabled or Hindi auto-captions). Treat as best-effort.
- **Not investment advice.** This is a personal research aid. The final call and the UPI approval stay with you.
