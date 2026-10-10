# 08 — Verifier retry loop

**What to build:** A section agent whose numbers fail verification is sent back with the specific failed fields, up to 2 times, before the remaining failures are marked "could not verify".

**Blocked by:** 07 — Offer & Objects grounded facts

**Status:** ready-for-agent

- [ ] At seam B, a fake LLM that fixes a field on retry 1 produces a verified field
- [ ] A fake LLM that never fixes it gets exactly 2 retries, and then the field is marked "could not verify"
- [ ] The retry prompt lists the specific failed fields
- [ ] Retries go through the budget meter

**Spec:** `docs/specs/ipo-agents.md`
