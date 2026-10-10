# 23 — Scheduler + single-IPO CLI

**What to build:** A launchd job runs `run_daily` at 10:30 on weekdays on this Mac (catching up after sleep), installed and uninstalled by a Python script, plus a command to run one IPO by ID.

**Blocked by:** 04 — Discovery of open mainboard IPOs, 05 — Telegram notifier + closing reminders

**Status:** ready-for-agent

- [ ] The install script loads the plist, and uninstall removes it
- [ ] The plist runs the project's venv Python at 10:30, Monday–Friday
- [ ] The single-IPO command runs the same pipeline for a given ID
- [ ] Logs go to a file in the project

**Spec:** `docs/specs/ipo-agents.md`
