# 24 — Live golden runs

**What to build:** With real keys and the verified golden numbers, each golden IPO is analysed live end to end, confirming accuracy, grounding, cost and delivery.

**Blocked by:** All previous tickets (01–23)

**Status:** ready-for-agent

- [ ] **HITL:** the investor has added the OpenAI, Groq, Tavily and Telegram keys to `.env` and set the OpenAI monthly spend limit
- [ ] **HITL:** the golden numbers are verified (`needs_human_review` cleared)
- [ ] Each golden IPO's verified numbers match the expected values
- [ ] Zero ungrounded facts in any report
- [ ] Logged cost is ≤ $1.50 per IPO; actual vs estimated cost per model is recorded
- [ ] The summary and PDF arrive on Telegram

**Spec:** `docs/specs/ipo-agents.md`
