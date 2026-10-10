# 05 — Telegram notifier + closing reminders

**What to build:** The real Telegram adapter sends the one-screen summary and the PDF as a document. Analysed IPOs get a "closes today/tomorrow" reminder at most once per day, with no LLM spend.

**Blocked by:** 01 — Walking skeleton

**Status:** ready-for-agent

- [ ] The real notifier uses the Bot API (`sendMessage` + `sendDocument`) with token and chat ID from `.env`
- [ ] At seam A, an IPO closing tomorrow, then today, gets exactly one reminder on each of those days
- [ ] Every summary ends with the "not investment advice" footer
- [ ] Reminders make no model calls

**Spec:** `docs/specs/ipo-agents.md`
