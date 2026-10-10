---
title: IPO Analyst Agents
labels: [ready-for-agent]
source: ipo-agents-plan.md (design) + grilling decisions (2026-10-11)
---

# IPO Analyst Agents: Spec

## Problem Statement

I invest in Indian mainboard IPOs, but each one comes with a 300–500 page Red Herring Prospectus (RHP) that I don't have time to read in the ~3 days the issue is open. Free online "reviews" are shallow, often sponsored, and quote numbers I can't verify. The things that should change my decision are buried deep in the document: how much of the issue is promoters or PE funds cashing out, whether profit is backed by cash flow, litigation, related-party deals, pre-IPO share prices far below the offer price. So I either skip good IPOs or subscribe based on hype and grey-market premium (GMP). I also forget closing dates. I want a trustworthy, grounded analysis of every new mainboard IPO delivered to me automatically, at a predictable and capped cost.

## Solution

Every weekday morning a Python job on my Mac finds the mainboard IPOs that are currently open, skips the ones it has already analysed, and runs a LangGraph analysis on each new one. The analysis reads every page of the RHP, extracts facts that each cite a page and a verbatim quote, rejects any number it can't find on the cited page, values the offer price against listed peers using Python arithmetic, gathers market sentiment from dated public sources, and produces a verdict (Subscribe long term / Subscribe for listing gains only / Neutral / Avoid) with reasons, red flags and a confidence level. I receive a short Telegram summary plus a full PDF report. Each IPO's analysis is capped at $1.50 of OpenAI usage: past 70% of the cap the system switches to cheaper models, and at the cap it stops and sends me what it has. Already-analysed IPOs get a "closes today/tomorrow" reminder with no further LLM spend. It recommends only; it never bids.

## User Stories

**Discovery and scheduling**
1. As an investor, I want the job to run automatically at 10:30 IST on weekdays, so that I get analysis without remembering to start it.
2. As an investor, I want a run that was missed because my Mac was asleep to happen when it wakes, so that I don't lose a day of the IPO window.
3. As an investor, I want only mainboard IPOs (no SME issues), so that I'm not spammed with issues I don't trade.
4. As an investor, I want only IPOs that are currently open for subscription, so that every report is actionable.
5. As an investor, I want discovery to fall back to BSE or Chittorgarh when NSE blocks the request, so that one blocked source doesn't cost me a day.
6. As an investor, I want to be told on Telegram when no source could list open IPOs, so that a silent failure doesn't look like "no IPOs today".
7. As an investor, I want each IPO analysed only once, so that I'm not charged twice or sent duplicate reports.
8. As an investor, I want an IPO whose final RHP isn't published yet to be put on hold and re-checked on the next run, so that analysis never runs on a draft (DRHP).
9. As an investor, I want an analysis that crashed partway to resume from where it stopped on the next run, so that I don't pay to re-read pages already processed.
10. As an investor, I want several new IPOs on the same day to be processed one after another, so that rate limits and costs stay predictable.

**Reading the RHP**
11. As an investor, I want every page of the RHP assigned to some section reader, so that nothing in the fine print is skipped.
12. As an investor, I want the run to fail loudly if page coverage is below 100%, so that I never get a report built on a partial reading.
13. As an investor, I want pages that are pure boilerplate (definitions, abbreviations) marked as covered without being sent to a model, so that I don't pay to read glossaries.
14. As an investor, I want pages with no text layer (scanned images) to go through OCR, so that scanned tables aren't silently missed.
15. As an investor, I want sections to be identified from the RHP's own table of contents, falling back to its headings, so that section boundaries match the document.
16. As an investor, I want the RHP's hash stored, so that I can tell exactly which document a report was built from.

**Grounded extraction**
17. As an investor, I want every number taken from the RHP to show its page number, so that I can check it myself in seconds.
18. As an investor, I want any extracted number that doesn't appear on its cited page to be rejected, so that invented numbers never reach the verdict.
19. As an investor, I want a section reader whose numbers fail verification to retry at most twice with the specific failures listed, after which those fields are shown as "could not verify", so that one bad extraction doesn't block the report.
20. As an investor, I want "not disclosed" to be an allowed answer that appears in the report, so that gaps are visible instead of filled in by guesswork.
21. As an investor, I want all arithmetic (CAGR, margins, offer-for-sale (OFS) %, dilution, valuation multiples) done in Python, never by a model, so that the calculations are correct.
22. As an investor, I want the offer size, fresh issue vs OFS split, selling shareholders, use of proceeds, and promoter holding before and after the issue, so that I can see who is cashing out.
23. As an investor, I want a flag when OFS is over 50%, when a PE/VC investor exits fully, or when general corporate purposes are close to the 25% limit, so that exit-driven IPOs stand out.
24. As an investor, I want 3 years plus the stub period of revenue, EBITDA, profit after tax (PAT), cash from operations (CFO), debt, receivables and returns, so that I can judge the quality of growth.
25. As an investor, I want a flag when PAT is positive but CFO is negative, when profit jumps in the year before the IPO, when receivable days rise faster than revenue, or when the auditor adds a qualification, so that common window-dressing is caught.
26. As an investor, I want company-specific risks separated from generic boilerplate and the top 10 ranked, so that I read the risks that matter.
27. As an investor, I want litigation by type and amount, contingent liabilities compared with net worth, and related-party totals, so that hidden liabilities are visible.
28. As an investor, I want promoter background, pay compared with profit, share pledges, and group-company conflicts, so that I can judge governance.
29. As an investor, I want the weighted average cost of acquisition (WACA) of recent pre-IPO share deals compared with the offer price, so that I can see whether insiders bought far cheaper.
30. As an investor, I want cross-checks between sections (debt repayment in the use of proceeds vs the debt schedule, litigation vs contingent liabilities, KPIs vs financials), so that inconsistencies are flagged.

**Valuation**
31. As an investor, I want the IPO's multiples at the upper end of the price band compared with the median, minimum and maximum of listed peers, so that I know whether it's cheap or expensive.
32. As an investor, I want the peers the RHP itself lists kept separate from any peers the agent suggests, each suggestion with a reason, so that I can judge whether the comparison is fair.
33. As an investor, I want the multiples chosen to fit the business (P/B with RoE for lenders, EV/EBITDA for capex-heavy businesses, P/S for loss-makers), so that the comparison is meaningful.
34. As an investor, I want every peer number to carry its source and as-of date, and missing peer data reported as missing, so that no peer figure comes from model memory.

**Sentiment**
35. As an investor, I want up to 8 dated articles and broker notes published after the RHP date, each tagged subscribe / avoid / neutral / listing-gains-only, so that I see the market view at a glance.
36. As an investor, I want numbers from third parties labelled "claimed by <source>" and kept apart from RHP-verified numbers, so that I never confuse the two.
37. As an investor, I want GMP shown and labelled "unofficial", and never used as the sole basis for a verdict, so that grey-market noise doesn't drive my decision.
38. As an investor, I want the latest subscription status by investor category when available, so that I can see demand.
39. As an investor, I want obvious spam or sponsored content dropped, so that the stance tally isn't gamed.

**Verdict and report**
40. As an investor, I want a verdict, confidence level and 3–5 reasons built only from verified facts, so that I can trust the call.
41. As an investor, I want the top red flags and top positives ranked, so that I see the most important points first.
42. As an investor, I want a PDF report in the same layout every time (summary page, offer, business, financials, valuation, risks, governance, sentiment, appendix), so that I can compare IPOs side by side.
43. As an investor, I want figures in Indian formatting (₹ crore/lakh), each with a page reference, so that the report reads naturally.
44. As an investor, I want the appendix to list unverified fields, data sources with timestamps, and the RHP hash, so that the report is auditable.
45. As an investor, I want the report produced without any model involved in formatting, so that nothing can be invented during rendering.

**Telegram**
46. As an investor, I want a summary that fits on one screen (dates, band, lot, issue split, verdict, reasons, red flags, valuation, sentiment) followed by the PDF, so that I can decide from my phone.
47. As an investor, I want a reminder on the day before and on the day an analysed IPO closes, sent once per day, so that I don't miss the UPI mandate.
48. As an investor, I want a "not investment advice" footer on every summary, so that the tool's role is clear.

**Budget guardrail**
49. As an investor, I want each IPO's analysis capped at $1.50 of OpenAI usage, so that cost is predictable.
50. As an investor, I want every model call checked against its worst-case cost before it is sent, so that no single call can push the run past the cap.
51. As an investor, I want the run to switch to cheaper models and fewer sentiment sources once 70% of the cap is spent, so that most runs still finish with a full report.
52. As an investor, I want a run that reaches the cap to stop, send me a partial report and an alert, and not be retried automatically, so that a runaway IPO can't keep spending.
53. As an investor, I want spending to carry across a crash-and-resume, so that resuming can't reset the cap.
54. As an investor, I want a run to refuse to start if any configured model has no known price, so that the cap can never be computed against an unknown price.
55. As an investor, I want an IPO whose pre-run estimate (from page count) is over 70% of the cap to start already on the cheaper models, so that large RHPs don't hit the cap partway through.
56. As an investor, I want every model call logged with its task, model, tokens and cost, so that I can see where the money goes and tune the routing.
57. As an investor, I want free Groq calls used for small tasks and to fall back to OpenAI on rate limits without waiting, so that cost stays low and Groq is never a bottleneck.

**Operation**
58. As the operator, I want model IDs, prices, routing per task, thresholds and the cap in one config file, so that tuning is a one-line change.
59. As the operator, I want secrets only in `.env`, so that keys never end up in git.
60. As the operator, I want to run a single IPO by ID from the command line, so that I can test or re-run on demand.
61. As the operator, I want a golden set of 3 past IPOs with hand-verified numbers, so that every change is checked against known truth.
62. As the operator, I want the whole test suite to run offline without keys, so that development costs $0.

## Implementation Decisions

**Stack and layout**
- Python only: all logic lives in `.py` modules within one importable package at the repo root, plus a `run_daily` entry point. Config is YAML; secrets go in `.env`. No notebooks, shell scripts or other languages.
- LangGraph runs the per-IPO analysis (parallel section fan-out with `Send`, a verify → retry loop, SQLite checkpointing keyed by IPO ID). Discovery, ingestion, the calculator, rendering and notification are plain Python.
- Models: GPT-5.6 Luna for heavy reading, GPT-5.6 Terra for the steps that drive the decision (consolidating Offer, Financials and Fine Print; peer valuation; the synthesizer), and Groq `openai/gpt-oss-120b` for small calls under ~6k tokens with automatic fallback to Luna. All model assignments live in a routing table in config.
- Search: Tavily. YouTube is out of scope for v1.
- Scheduling: launchd on this Mac at 10:30 local time on weekdays. The Mac is already on IST.

**Modules (deep modules behind small interfaces)**
- **Daily runner:** `run_daily(today, adapters) -> RunSummary`. Handles discovery, dedupe, holding IPOs that have only a DRHP, analysing each new IPO in turn, notifications and reminders.
- **Analyser:** `analyse_rhp(ipo, rhp_pdf, adapters, budget) -> Report`. Covers ingestion, section agents, peer valuation, sentiment, the verifier, the synthesizer and the PDF render. The report carries `status` (`complete` / `partial_budget`), the verdict, facts, unverified fields, and the PDF path.
- **Ingestion:** `ingest(rhp_pdf) -> SectionedDocument`. Returns the page→section map, chunks with page metadata, boilerplate marks, and the coverage result. Raises an error when coverage is below 100%.
- **Adapters (the system's outer boundary, swappable as a set):** exchange listing source, RHP downloader, LLM provider, web search, market data, GMP/subscription source, Telegram notifier. Real and fake versions share one interface each.
- **Model router + budget meter:** the only way any code obtains a model. `get_model(task, est_tokens)` applies the routing table, the Groq size and daily-usage limits, and the degrade switch. The meter enforces the cap before each call and records actual usage after it.
- **Calculator:** pure functions for every derived number.
- **Verifier:** normalises numbers (commas, ₹, lakh/crore/million) and requires each value to appear on its cited page and each quote to match the page text with a fuzzy score of at least 0.9.
- **Store:** SQLite tables for IPOs, LLM calls and Groq daily usage, plus the LangGraph checkpointer.
- **Renderer:** Jinja2 HTML → WeasyPrint PDF, with matplotlib charts. Never calls a model.

**Data contracts**
- Every RHP-derived numeric field is a `Fact`: `{value, unit, page, verbatim_quote}`. `value` may be null when paired with a `not_disclosed_reason`.
- Every third-party number is a `ClaimedFact`: `{value, unit, source_url, source_name, as_of}`.
- Each of the 8 section groups (Offer & Objects, Business & Industry, Financials, Risk Factors, Fine Print & Legal, Promoters & Governance, Offer Price Basis, Other Disclosures) has its own Pydantic output schema. All agents return structured output at temperature 0.
- IPO status lifecycle: `discovered → waiting_rhp → analysing → done | failed | failed_budget`. `failed` is retried on the next run and resumes from its checkpoint. `failed_budget` and `done` are terminal.
- An LLM call record holds: `ipo_id, task, model, input_tokens, cached_tokens, output_tokens, cost_usd, timestamp`.

**Budget rules**
- Cap: $1.50 per IPO analysis. Only OpenAI tokens are metered; Groq is logged at $0; search isn't metered.
- Before each call: worst case = prompt tokens (tiktoken) × input price + `max_tokens` × output price. If spent + worst case would exceed the cap, the meter raises `BudgetExceeded`. Every call must set `max_tokens`.
- After each call: the meter adds the actual cost from usage metadata, with cached input charged at the cached price.
- Degrade at 70% of the cap (or when the pre-run estimate is over 70%): Terra tasks are routed to Luna, and sentiment sources drop from 8 to 4.
- When `BudgetExceeded` is raised, the graph stops, the report is rendered as `partial_budget` from the facts verified so far, a Telegram alert is sent, and the status becomes `failed_budget`.
- Model prices live in config. A model in the routing table with no price makes the run refuse to start.
- A monthly spend limit in the OpenAI dashboard is set by hand as a backstop.

**Build orchestration**
- Wave 0 builds a **walking skeleton**: all three seams run end to end with trivial behaviour, plus the contracts (schemas, config, store, router/budget, fake adapters).
- Later waves run as parallel sub-agents on local branches in git worktrees, each owning specific modules: Wave 1 is discovery+notifier, ingestion, calculator+verifier, renderer and golden set; Wave 2 is section agents, peer valuation and sentiment; Wave 3 is synthesizer, graph wiring, the runner and the scheduler.
- After each wave, the master session runs a code-reviewer sub-agent on each diff, then the full test suite, then merges into local `main`. Changes to Wave 0 contracts go through the master.

## Testing Decisions

- **TDD with vertical slices:** one failing test at a seam, then the minimum code to pass it, then the next. Tests are never written in bulk ahead of the code. Refactoring happens at review, not inside the loop.
- **A good test** asserts on observable behaviour at a seam: messages Telegram received, the returned Report or SectionedDocument, and what the next `run_daily` does (for example, no duplicate analysis). It never asserts on private functions, internal call order, or by querying SQLite directly. Expected values come from independent sources (hand-verified RHP numbers, worked examples), never from re-running the code's own formula.
- **Agreed seams (only these three):**
  - **A: `run_daily(today, adapters)`.** Covers mainboard filtering, the NSE→fallback chain, the "no source" alert, dedupe, DRHP hold, ordering when several IPOs open the same day, crash-then-resume, reminders sent once per day, the summary message format, and budget-stop alerts.
  - **B: `analyse_rhp(ipo, rhp_pdf, adapters, budget)`.** Covers grounding (an ungrounded number is retried, then marked "could not verify"), "not disclosed", red-flag triggers, cross-section checks, peer multiples against hand calculations, sentiment labelling, the verdict built only from verified input, report content, and all budget rules (degrade at 70%, stop before crossing $1.50, refusal on a missing price, spend carried across resume, pre-run degrade). The golden tests live here.
  - **C: `ingest(rhp_pdf)`.** Covers 100% coverage on the real golden RHPs, section boundaries, boilerplate marks, OCR fallback, and the loud failure when coverage is incomplete.
- **Fakes are used only at the outer boundary:** a scripted fake LLM (returns given structured outputs plus usage metadata), recorded exchange HTTP fixtures, fake search, market-data and GMP sources, and a recording Telegram notifier. Everything inside (router, meter, calculator, verifier, graph, renderer, store) runs for real against a temporary SQLite file.
- **Offline by default:** the suite needs no keys and no network. Live golden runs (real models, $1.50 cap enforced) are a separate opt-in command after the final merge.
- **Golden set:** 3 past mainboard IPOs (one OFS-heavy, one loss-making, one NBFC). A sub-agent drafts about 10 expected numbers each with page references, marked `needs_human_review`; the investor verifies them before they count as truth.
- **Prior art:** none. The repo is greenfield, and the Wave 0 skeleton tests set the pattern.

## Out of Scope

- Auto-bidding, UPI mandates or any broker integration.
- SME IPOs, follow-on public offers (FPOs), REITs/InvITs, and non-Indian markets.
- YouTube search and transcripts (v2).
- Re-analysing when an RHP replaces a DRHP; DRHP-only IPOs are put on hold instead.
- Metering search API costs, and any cap per day or per month inside the app beyond the per-IPO cap.
- OpenAI Batch API.
- Hosting on a VPS or in the cloud, and GitHub Actions.
- A web UI or dashboard; Telegram and the PDF are the only outputs.
- Performance tracking after listing (whether verdicts were right).

## Further Notes

- The design source is `ipo-agents-plan.md`. Where it conflicts with this spec, this spec wins: Luna is GPT-5.6 Luna (not `gpt-6-luna`), there's no DRHP re-analysis, YouTube is deferred, and the cap is $1.50 per IPO.
- The system needs `brew install pango tesseract` (for WeasyPrint and OCR); neither is installed yet.
- Manual steps for the investor before live runs: create the Telegram bot (BotFather), create the OpenAI, Groq and Tavily keys, set the OpenAI monthly spend limit, and verify the golden numbers.
- GMP is unofficial and unregulated, and the output is not investment advice.
- The verifier proves that a number and its unit are printed in the quoted text on the cited page. It can't prove the number means what the field says (e.g. that a quoted share count is the post-issue count). That residual risk is why the golden set is hand-checked.
