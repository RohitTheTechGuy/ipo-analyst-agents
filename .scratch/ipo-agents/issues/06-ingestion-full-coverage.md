# 06 — Ingestion with 100% coverage

**What to build:** `ingest` turns a real RHP into sections mapped from its table of contents (falling back to headings), page-tagged chunks, boilerplate marks and OCR'd text for image-only pages, and fails loudly if any page is unassigned.

**Blocked by:** 01 — Walking skeleton, 02 — Golden set draft

**Status:** ready-for-agent

- [ ] At seam C, all 3 golden RHPs reach 100% page coverage with each page in exactly one section group
- [ ] Section boundaries for the golden RHPs match hand-checked page ranges
- [ ] Definitions/abbreviations pages are marked covered-as-boilerplate
- [ ] Image-only pages go through OCR
- [ ] A PDF with an unmappable gap raises a coverage error

**Spec:** `docs/specs/ipo-agents.md`
