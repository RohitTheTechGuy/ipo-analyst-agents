"""The generic section agent (plan 5.2): one section group's pages + its prompt + its schema -> verified output.

A section that fits one call is read in one call routed as the group's task. A larger one is read
map -> reduce: each part on the cheap map model (in parallel), then one call (the group's task) merges
the parts. A map part whose reply can't be parsed is skipped and reported; BudgetExceeded stops it all.

Facts that fail verification are re-asked, at most MAX_RETRIES times, on the group's task (see `_retry`).
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pydantic import BaseModel

from ipo_agents.contracts import Chunk, Fact, Quote, SectionedDocument
from ipo_agents.ingestion import MAX_CHUNK_CHARS, page_texts
from ipo_agents.llm import LLM, LLMParseError
from ipo_agents.verifier import Rejection, check_facts

log = logging.getLogger(__name__)

MAP_TASK = "map_extract"
MAP_WORKERS = 4  # map calls in flight at once; the budget meter is thread-safe
MAX_CALL_CHARS = MAX_CHUNK_CHARS  # section text per call; more than this is read map -> reduce
MAX_RETRIES = 2  # re-asks for facts that failed verification, per section

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
RETRY_RULES = (
    "Some fields of your previous answer failed verification against the pages above. Return the whole "
    "section again in the same structure, with list items in the same order as in your previous answer "
    "(below). For each failed field, give a value, unit, page and verbatim_quote copied from that page so "
    "that the value is printed in the quote, or return it as not disclosed if these pages don't state it. "
    "Field paths start with the section name."
)


@dataclass(frozen=True)
class SectionSpec:
    """One section agent. `group` names the SectionedDocument group it reads, its routing task, and
    the prefix of its unverified field paths; `report_field` is the Report field its output fills.
    `borrow_from` / `borrow_if`: the agent also reads each page of those groups whose text matches the
    regex `borrow_if` (case-insensitive), e.g. notes to accounts printed inside another section."""

    group: str
    schema: type[BaseModel]
    prompt: str  # what to extract, in this section's terms
    report_field: str
    borrow_from: tuple[str, ...] = ()
    borrow_if: str = ""
    max_tokens: int | None = None  # output tokens per call; None: config.default_max_tokens


def section_chunks(spec: SectionSpec, doc: SectionedDocument) -> list[Chunk]:
    """The chunks the agent reads: its own group's, plus one chunk per borrowed page, in page order."""
    own = doc.sections.get(spec.group, [])
    if not own or not spec.borrow_from:
        return own
    pattern, have = re.compile(spec.borrow_if, re.IGNORECASE), set(page_texts(own))
    borrowed = [Chunk(section=spec.group, page_start=p, page_end=p, text=f"[page {p}]\n{text}")
                for group in spec.borrow_from
                for p, text in page_texts(doc.sections.get(group, [])).items()
                if p not in have and pattern.search(text)]
    return sorted(own + borrowed, key=lambda c: c.page_start)


def run_section(spec: SectionSpec, chunks: list[Chunk], llm: LLM, max_tokens: int) -> tuple[BaseModel | None, list[str]]:
    """Extracts the section, then verifies it against the same pages: (verified output, failed field paths).

    Facts that fail are re-asked up to MAX_RETRIES times; a retry's value replaces only a failed field, and
    only if it verifies. What still fails is left "could not verify" and its path returned.
    Failed paths also name any part of the section whose reply couldn't be parsed, as
    "<group>.pages[N-M]: could not read". The output is None only if no part could be read."""
    output, unread = extract(spec, chunks, llm, max_tokens)
    failed = [f"{spec.group}.pages[{first}-{last}]: could not read" for first, last in unread]
    if output is None:
        return None, failed
    pages = page_texts(chunks)
    verified, rejections = check_facts(output, pages, spec.group)
    for _ in range(MAX_RETRIES):
        if not rejections:
            break
        verified, rejections = _retry(spec, chunks, pages, verified, rejections, llm, max_tokens)
    return verified, [r.path for r in rejections] + failed


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


# --- verifier retry ----------------------------------------------------------------------------------

def _retry(spec: SectionSpec, chunks: list[Chunk], pages: dict[int, str], current: BaseModel,
           rejections: list[Rejection], llm: LLM, max_tokens: int) -> tuple[BaseModel, list[Rejection]]:
    """One re-ask on the group's task: the section's pages, then the failed fields, each with the value,
    page and quote that were rejected and the verifier's reason. The system prompt is the extract call's
    and, for a one-part section, the pages are its exact text, so the provider can reuse the cached prefix.
    Returns `current` with only the failed fields that now verify replaced, and what still fails."""
    failures = "\n".join(
        f"- {r.path}: you gave "
        + (f"value {r.fact.value} ({r.fact.unit}) " if isinstance(r.fact, Fact) else "")
        + f"on page {r.fact.page}, quote "
        f"{json.dumps(r.fact.verbatim_quote, ensure_ascii=False)}; rejected because {r.reason}"
        for r in rejections)
    user = (f"{_retry_text(chunks, rejections)}\n\n{RETRY_RULES}\n\nFailed fields:\n{failures}\n\n"
            f"Your previous answer (failed fields marked could not verify):\n{current.model_dump_json()}")
    try:
        answer = _call(llm, spec.group, f"{spec.prompt}\n\n{EXTRACT_RULES}", user, spec.schema, max_tokens)
    except LLMParseError as e:
        log.warning("%s retry: reply could not be parsed (%s); that retry is spent", spec.group, e)
        return current, rejections
    return _merge_fixes(current, answer, rejections, pages, spec.group)


def _retry_text(chunks: list[Chunk], rejections: list[Rejection]) -> str:
    """The pages a retry reads. A one-part section is sent whole. A multi-part one sends each chunk a failed
    fact cites plus the chunk before and after it (a table often runs onto the next page, and a wrong page
    number is usually off by one), in page order; if no failed fact cites a page of the section, its first
    part. Not capped at MAX_CALL_CHARS (a full-size chunk's neighbours would never fit); the budget meter
    still bounds the cost."""
    ordered = sorted(chunks, key=lambda c: c.page_start)
    parts = _parts(ordered)
    if len(parts) == 1:
        return parts[0][2]
    cited = {r.fact.page for r in rejections if r.fact.page is not None}
    keep = {j for i, c in enumerate(ordered) if any(c.page_start <= p <= c.page_end for p in cited)
            for j in (i - 1, i, i + 1) if 0 <= j < len(ordered)}
    if not keep:
        return parts[0][2]
    return "".join(ordered[i].text for i in sorted(keep))


def _merge_fixes(current: BaseModel, answer: BaseModel, rejections: list[Rejection], pages: dict[int, str],
                 prefix: str) -> tuple[BaseModel, list[Rejection]]:
    """Walks `current` and the retry's `answer` side by side, pairing list items by identity (`_same_item`),
    not index. A failed field takes the answer's Fact in the same place if it has a value that verifies (a
    list item with no match, or more than one, gives nothing); every other field, including ones the
    answer changed, is kept. An answer of "not disclosed" for a field first given a number doesn't clear it: the two answers
    disagree, so it stays "could not verify". Still-failing fields carry their latest rejection."""
    pending = {r.path: r for r in rejections}
    still: list[Rejection] = []

    def walk(cur, new, path):
        if isinstance(cur, Fact):
            if path not in pending:
                return cur
            if isinstance(new, Fact) and new.value is not None:
                fixed, rejected = check_facts(new, pages, path)
                if not rejected:
                    return fixed
                still.extend(rejected)
            else:
                still.append(pending[path])
            return cur
        if isinstance(cur, Quote):
            if path not in pending:
                return cur
            if isinstance(new, Quote) and new.verbatim_quote:
                fixed, rejected = check_facts(new, pages, path)
                if not rejected:
                    return fixed
                still.extend(rejected)
            else:
                still.append(pending[path])
            return cur
        if isinstance(cur, BaseModel):
            return cur.model_copy(update={
                name: walk(getattr(cur, name), getattr(new, name, None) if isinstance(new, BaseModel) else None,
                           f"{path}.{name}")
                for name in type(cur).model_fields})
        if isinstance(cur, list):
            new = new if isinstance(new, list) else []
            return [walk(item, _same_item(item, new), f"{path}[{i}]") for i, item in enumerate(cur)]
        return cur

    return walk(current, answer, prefix), still


def _same_item(item, candidates: list):
    """The one item of the retry's list with the same identity as `item`, else None. Matching by identity,
    not index, stops a grounded number being filed under another item when the retry reorders a list."""
    key = _identity(item)
    if not key:
        return None  # no identity (e.g. a bare Fact): nothing to match on, so nothing is merged
    matches = [c for c in candidates if _identity(c) == key]
    return matches[0] if len(matches) == 1 else None


def _identity(item) -> tuple:
    """A list item's identity: its str fields (e.g. ProceedsObject.purpose, SellingShareholder.name),
    case- and whitespace-normalised. Empty for a Fact or a model without str fields."""
    if not isinstance(item, BaseModel) or isinstance(item, Fact):
        return ()
    return tuple((name, " ".join(value.split()).casefold()) for name in type(item).model_fields
                 if isinstance(value := getattr(item, name), str))
