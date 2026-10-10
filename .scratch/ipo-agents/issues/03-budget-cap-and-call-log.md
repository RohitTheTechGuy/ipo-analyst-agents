# 03 — Budget cap + call log

**What to build:** Every model is obtained through the router, and every call is checked against a $1.50 per-IPO cap before it is sent. A run that would cross the cap stops with `BudgetExceeded`, and every call is logged with its tokens and cost.

**Blocked by:** 01 — Walking skeleton

**Status:** ready-for-agent

- [ ] At seam B, a fake LLM with inflated usage stops the run before any call that would push spend over $1.50
- [ ] Worst-case pre-call cost uses prompt tokens + `max_tokens`; a call without `max_tokens` is rejected
- [ ] Actual cost is recorded from usage metadata, with cached input charged at the cached price
- [ ] A model in the routing table with no configured price makes the run refuse to start
- [ ] Groq calls are logged at $0
- [ ] A test fails if any module constructs a chat model outside the router

**Spec:** `docs/specs/ipo-agents.md`

**Carried over from the ticket 01 review:**
- A failed structured parse must still meter its tokens: read usage before raising (e.g. an `LLMParseError` that carries usage), so verifier retries can't spend unmetered.
- `run_daily` needs an explicit `BudgetExceeded` branch ahead of its generic `except`, so a capped IPO becomes terminal `failed_budget` rather than retryable `failed`. Build the metered LLM per IPO by wrapping `adapters.llm`.
- Enforce "missing price ⇒ refuse to start" with a validator on `Config`: every routing value must exist in `models` with all three prices.
- `ScriptedLLM` should reject a `model` that isn't a configured model key, so a wrong routing key can't pass the tests.
