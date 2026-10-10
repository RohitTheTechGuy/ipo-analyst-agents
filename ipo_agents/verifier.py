"""Grounding verifier (plan 5.5): every Fact must be found on the page it cites. Plain Python, no LLM."""
from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel
from rapidfuzz import fuzz

from ipo_agents.contracts import Fact

COULD_NOT_VERIFY = "could not verify"
MIN_QUOTE_SCORE = 90  # rapidfuzz partial_ratio, 0-100
# A number as printed: lakh or western commas, optional decimals; brackets mean negative, e.g. (15,844.00).
# Currency signs (₹, Rs.) and unit words (lakh, crore, million) sit outside the match, so they're ignored.
_NUMBER = re.compile(r"(\()?(\d[\d,]*(?:\.\d+)?)(\))?")
_NUMBER_CHARS = "0123456789,.()"
_NIL = re.compile(r"\b(nil|no)\b", re.IGNORECASE)

M = TypeVar("M", bound=BaseModel)


def verify_facts(output: M, pages: dict[int, str], prefix: str) -> tuple[M, list[str]]:
    """Checks every Fact in `output` against `pages` (page number -> text).

    Returns a copy in which each failing Fact is replaced by one marked "could not verify", and the
    dotted paths of those fields (e.g. "offer_objects.objects[1].amount"), so a retry can name them.
    A Fact passes when its quote matches the cited page (fuzzy, whitespace-normalised) and its value
    is a number in the matched part of the page, or is 0 and that part says "Nil"/"no".
    """
    failed: list[str] = []

    def walk(obj, path):
        if isinstance(obj, Fact):
            if obj.value is None or _grounded(obj, pages):
                return obj
            failed.append(path)
            return Fact(value=None, unit=obj.unit, page=None, verbatim_quote=None,
                        not_disclosed_reason=COULD_NOT_VERIFY)
        if isinstance(obj, BaseModel):
            return obj.model_copy(update={name: walk(getattr(obj, name), f"{path}.{name}")
                                          for name in type(obj).model_fields})
        if isinstance(obj, list):
            return [walk(item, f"{path}[{i}]") for i, item in enumerate(obj)]
        return obj

    return walk(output, prefix), failed


def _grounded(fact: Fact, pages: dict[int, str]) -> bool:
    page = pages.get(fact.page)
    if page is None:
        return False  # a page the agent was not given
    quote, text = _normalise(fact.verbatim_quote), _normalise(page)
    match = fuzz.partial_ratio_alignment(quote, text, score_cutoff=MIN_QUOTE_SCORE)
    if match is None:
        return False
    # The value must be on the page itself, not just in the quote: a quote with one digit changed still
    # scores >= 90. Widen the matched span so a number cut at its edge is read whole.
    start, end = match.dest_start, match.dest_end
    while start > 0 and text[start - 1] in _NUMBER_CHARS:
        start -= 1
    while end < len(text) and text[end] in _NUMBER_CHARS:
        end += 1
    span = text[start:end]
    if fact.value == 0 and _NIL.search(span):
        return True
    return any(_same(fact.value, n) for n in _numbers(span))


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUMBER.finditer(text):
        n = float(m.group(2).replace(",", ""))
        out.append(-n if m.group(1) and m.group(3) else n)
    return out


def _same(value: float, number: float) -> bool:
    return abs(value - number) <= 1e-9 * max(1.0, abs(number))
