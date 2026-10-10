# 22 — Full PDF report layout

**What to build:** Renders the 9-section report (summary page with verdict badge, offer, business, financials, valuation, risks, governance, sentiment, appendix) with charts, Indian number formatting and page references, without any model call.

**Blocked by:** 20 — Synthesizer verdict + cross-section checks

**Status:** ready-for-agent

- [ ] At seam B, the PDF contains all 9 sections for a golden IPO
- [ ] Every RHP number shows its page reference; third-party numbers carry the "claimed by" style
- [ ] The appendix lists unverified fields, sources with timestamps and the RHP hash
- [ ] Red/green callouts also carry a text label (not colour alone)
- [ ] No model is called during rendering

**Spec:** `docs/specs/ipo-agents.md`

**Environment note:** WeasyPrint on python.org Python needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` set before the process starts (Homebrew pango). Run PDF-rendering tests with it, and fail with a clear message if the import fails.
