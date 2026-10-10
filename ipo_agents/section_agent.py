"""The generic section agent (plan 5.2): one section group's pages + its prompt + its schema -> verified output.

A section that fits one call is read in one call routed as the group's task. A larger one is read
map -> reduce: each part on the cheap map model, then one call (the group's task) merges the parts.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from ipo_agents.contracts import Chunk
from ipo_agents.ingestion import MAX_CHUNK_CHARS, page_texts
from ipo_agents.llm import LLM
from ipo_agents.verifier import verify_facts

MAP_TASK = "map_extract"
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
    "You are given partial extractions, as JSON, each from one part of the section. Merge them into one "
    "answer. For each field take it from the part that states it, copying value, unit, page and "
    "verbatim_quote unchanged. If no part states a field, return it as not disclosed. Never add, "
    "calculate or change a number."
)


@dataclass(frozen=True)
class SectionSpec:
    """One section agent. `group` names the SectionedDocument group it reads, its routing task, and
    the prefix of its unverified field paths; `report_field` is the Report field its output fills."""

    group: str
    schema: type[BaseModel]
    prompt: str  # what to extract, in this section's terms
    report_field: str


def run_section(spec: SectionSpec, chunks: list[Chunk], llm: LLM, max_tokens: int) -> tuple[BaseModel, list[str]]:
    """Extracts the section, then verifies it against the same pages: (verified output, failed field paths)."""
    output = extract(spec, chunks, llm, max_tokens)
    return verify_facts(output, page_texts(chunks), spec.group)


def extract(spec: SectionSpec, chunks: list[Chunk], llm: LLM, max_tokens: int) -> BaseModel:
    system = f"{spec.prompt}\n\n{EXTRACT_RULES}"
    parts = _parts(chunks)
    if len(parts) == 1:
        return _call(llm, spec.group, system, parts[0][2], spec.schema, max_tokens)
    partials = [(first, last, _call(llm, MAP_TASK, system, text, spec.schema, max_tokens))
                for first, last, text in parts]
    merged = "\n\n".join(f"[pages {first}-{last}]\n{p.model_dump_json()}" for first, last, p in partials)
    return _call(llm, spec.group, f"{spec.prompt}\n\n{REDUCE_RULES}", merged, spec.schema, max_tokens)


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
