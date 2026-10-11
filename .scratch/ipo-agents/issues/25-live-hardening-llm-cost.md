# 25 — Pre-live hardening: LLM boundary, output size, cost, rate limits

**What to build:** make the real OpenAI/Groq path fail soft and stay inside the $1.50 cap on a real 500-page RHP, from the whole-system pre-live review (B1, B2, S1, S2).

**Blocked by:** 01–23

**Status:** ready-for-agent

- [ ] Real provider errors (pydantic `ValidationError`, `openai.LengthFinishReasonError`, `OpenAIRefusalError`) become `LLMParseError` carrying real usage when known; tested by driving the real `LangChainLLM` with `httpx.MockTransport`
- [ ] Reduce/final calls for large sections get enough output tokens; risk factors capped to a top-N in schema/prompt; `reasoning_effort` configurable per model and passed through
- [ ] A failing map part no longer keeps queued parts running and re-spending
- [ ] Pre-run estimate recalibrated to ~$0.85–1.0 for a 520-page RHP; retries stop early enough to protect the synthesizer's reservation
- [ ] 429s honor Retry-After with up to 5 attempts / ~30s backoff; map concurrency limited
- [ ] `scripts`-free smoke command: `run_daily.py --smoke` makes one tiny call per LLM schema on Luna (~$0.02) and reports model-ID / schema / usage problems
- [ ] Explicit dev reset: `run_daily.py --ipo X --reset-budget` deletes that IPO's llm_calls rows and checkpoint after a typed confirmation

**Spec:** `docs/specs/ipo-agents.md`
