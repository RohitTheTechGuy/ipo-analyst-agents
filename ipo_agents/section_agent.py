"""The generic section agent (plan 5.2): one section group's pages + its prompt + its schema -> verified output.

A section that fits one call is read in one call routed as the group's task. A larger one is read
map -> reduce: each part on the cheap map model (in parallel), then one call (the group's task) merges
the parts. A map part whose reply can't be parsed is skipped and reported; BudgetExceeded stops it all.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pydantic import BaseModel

from ipo_agents.contracts import Chunk
from ipo_agents.ingestion import MAX_CHUNK_CHARS, page_texts
from ipo_agents.llm import LLM, LLMParseError
from ipo_agents.verifier import verify_facts

log = logging.getLogger(__name__)

MAP_TASK = "map_extract"
MAP_WORKERS = 4  # map calls in flight at once; the budget meter is thread-safe
MAX_CALL_CHARS = MAX_CHUNK_CHARS  # section text per call; more than this is read map -> reduce

EXTRACT_RULES = (
    "Use ONLY the text provided; never fill a value from what you know about this company. "
    "The text is pages of the RHP; each page starts with a tag line '[page N]'. For every number give: "
    "value = the number exactly as written, without commas or currency signs (a number in brackets such as "
    "(15,844.00) is negative; 'Nil' is 0); unit = the unit as written on the page (e.g. '₹ million', "
    "'equity shares', '%'); page = N from the nearest '[page N]' tag above the number; verbatim_quote = the "
    "words around the number, copied character for character from that page. "
    "If a value is not present, or is shown as [●], return value null with not_disclosed_reason "
    "'not disclosed in provided pages'. Never calculate, convert units or round; only extract."
)
REDUCE_RULES = (
    "You are given partial extractions, as JSON, each from one part of the section and labelled with its "
    "pages. Merge them into one answer. For each field take it from the part that states it, copying "
    "value, unit, page and verbatim_quote unchanged. List items that describe the same thing (the same "
    "object, the same shareholder) in more than one part are one item: keep one copy. When parts disagree "
    "on a field, prefer the part this section's instructions name as authoritative for it; if none is "
    "named, return the field as not disclosed with not_disclosed_reason 'parts of the section disagree'. "
    "If no part states a field, return it as not disclosed. Never add, calculate or change a number."
)


@dataclass(frozen=True)
class SectionSpec:
    """One section agent. `group` names the SectionedDocument group it reads, its routing task, and
    the prefix of its unverified field paths; `report_field` is the Report field its output fills."""

    group: str
    schema: type[BaseModel]
    prompt: str  # what to extract, in this section's terms
    report_field: str


def run_section(spec: SectionSpec, chunks: list[Chunk], llm: LLM, max_tokens: int) -> tuple[BaseModel | None, list[str]]:
    """Extracts the section, then verifies it against the same pages: (verified output, failed field paths).

    Failed paths also name any part of the section whose reply couldn't be parsed, as
    "<group>.pages[N-M]: could not read". The output is None only if no part could be read."""
    output, unread = extract(spec, chunks, llm, max_tokens)
    failed = [f"{spec.group}.pages[{first}-{last}]: could not read" for first, last in unread]
    if output is None:
        return None, failed
    verified, unverified = verify_facts(output, page_texts(chunks), spec.group)
    return verified, unverified + failed


def extract(spec: SectionSpec, chunks: list[Chunk], llm: LLM, max_tokens: int) -> tuple[BaseModel | None, list[tuple[int, int]]]:
    """(output, (first, last) pages of each map part whose reply couldn't be parsed)."""
    system = f"{spec.prompt}\n\n{EXTRACT_RULES}"
    parts = _parts(chunks)
    if len(parts) == 1:
        return _call(llm, spec.group, system, parts[0][2], spec.schema, max_tokens), []

    def read(part):
        first, last, text = part
        try:
            return first, last, _call(llm, MAP_TASK, system, text, spec.schema, max_tokens)
        except LLMParseError as e:
            # One unreadable part must not lose the rest of the section; it is reported, not hidden.
            log.warning("%s pages %d-%d: reply could not be parsed (%s); skipping that part", spec.group, first, last, e)
            return first, last, None

    with ThreadPoolExecutor(max_workers=MAP_WORKERS) as pool:
        results = list(pool.map(read, parts))  # re-raises BudgetExceeded and any other failure
    partials = [(first, last, p) for first, last, p in results if p is not None]
    unread = [(first, last) for first, last, p in results if p is None]
    if not partials:
        return None, unread
    return _reduce(spec, partials, llm, max_tokens), unread


def _reduce(spec: SectionSpec, partials: list[tuple[int, int, BaseModel]], llm: LLM, max_tokens: int) -> BaseModel:
    """Merges partials in one call (the group's task), or, while they don't fit MAX_CALL_CHARS, in rounds of
    batches on the map model. Every batch holds at least two partials, so each round at least halves them."""
    system = f"{spec.prompt}\n\n{REDUCE_RULES}"
    while True:
        batches = _batches(partials)
        if len(batches) == 1:
            return _call(llm, spec.group, system, _render(batches[0]), spec.schema, max_tokens)
        partials = [batch[0] if len(batch) == 1 else
                    (batch[0][0], batch[-1][1], _call(llm, MAP_TASK, system, _render(batch), spec.schema, max_tokens))
                    for batch in batches]


def _batches(partials):
    batches: list[list] = []
    for p in partials:
        if batches and (len(batches[-1]) == 1 or len(_render(batches[-1] + [p])) <= MAX_CALL_CHARS):
            batches[-1].append(p)
        else:
            batches.append([p])
    return batches


def _render(partials) -> str:
    return "\n\n".join(f"[pages {first}-{last}]\n{p.model_dump_json()}" for first, last, p in partials)


def _parts(chunks: list[Chunk]) -> list[tuple[int, int, str]]:
    """Chunks packed in page order into as few (first page, last page, text) parts as fit MAX_CALL_CHARS."""
    parts: list[tuple[int, int, str]] = []
    for c in sorted(chunks, key=lambda c: c.page_start):
        if parts and len(parts[-1][2]) + len(c.text) <= MAX_CALL_CHARS:
            first, _, text = parts[-1]
            parts[-1] = (first, c.page_end, text + c.text)
        else:
            parts.append((c.page_start, c.page_end, c.text))
    return parts


def _call(llm: LLM, task, system, user, schema, max_tokens) -> BaseModel:
    return llm.invoke_structured(task=task, system=system, user=user, schema=schema, max_tokens=max_tokens).parsed
