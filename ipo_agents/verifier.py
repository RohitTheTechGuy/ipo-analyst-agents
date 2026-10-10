"""Grounding verifier (plan 5.5): every Fact must be found on the page it cites. Plain Python, no LLM.

It proves a number and its unit are printed in the quoted text on the cited page. It can't prove the
number means what the field says; the golden hand-checks cover that residual risk.
"""
from __future__ import annotations

import math
import re
from typing import TypeVar

from pydantic import BaseModel
from rapidfuzz import fuzz

from ipo_agents.contracts import Fact

COULD_NOT_VERIFY = "could not verify"
MIN_QUOTE_SCORE = 90  # rapidfuzz partial_ratio, 0-100
MIN_QUOTE_CHARS = 15  # a shorter quote could match almost anywhere
MAX_OCCURRENCES = 20  # fuzzy matches of one quote checked per page
# A number as printed: lakh or western commas, optional decimals. Brackets, e.g. (15,844.00), or a
# leading minus mean negative. Currency signs (₹, Rs.) sit outside the match, so they're ignored.
_NUMBER = re.compile(r"(\()?([-−])?(\d[\d,]*(?:\.\d+)?)(\))?")
_NUMBER_CHARS = "0123456789,.()-−"
_WORD = re.compile(r"[^\W\d_]{2,}")  # a word of two or more letters
_SERIAL = re.compile(r"\d{1,2}")  # a row number such as "1 Fresh Issue"
_NIL = re.compile(r"\bnil\b")
_DASH_CELL = re.compile(r"(?:^|\s)[-–—](?:\s|$)")  # a table cell that is just a dash
_SCALES = ("crore", "million", "lakh", "billion")
_UNIT_WORDS = (*_SCALES, "%", "share")

M = TypeVar("M", bound=BaseModel)


def verify_facts(output: M, pages: dict[int, str], prefix: str) -> tuple[M, list[str]]:
    """Checks every Fact in `output` against `pages` (page number -> text).

    Returns a copy in which each failing Fact is replaced by one marked "could not verify", and the
    dotted paths of those fields (e.g. "offer_objects.objects[1].amount"), so a retry can name them.
    A Fact passes when its quote is printed on the cited page and its value is a number in that
    quote and in the matching text on the page, in the Fact's unit (see `_grounded`).
    Raises TypeError if a Fact sits in a dict, tuple or set, which this walk does not rewrite.
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
        if isinstance(obj, (dict, tuple, set, frozenset)) and _holds_fact(obj):
            raise TypeError(f"{path}: a Fact inside a {type(obj).__name__} can't be verified; "
                            "use a model field or a list")
        return obj

    return walk(output, prefix), failed


def _holds_fact(obj) -> bool:
    if isinstance(obj, Fact):
        return True
    if isinstance(obj, BaseModel):
        return any(_holds_fact(getattr(obj, name)) for name in type(obj).model_fields)
    if isinstance(obj, dict):
        return any(map(_holds_fact, [*obj.keys(), *obj.values()]))
    if isinstance(obj, (list, tuple, set, frozenset)):
        return any(map(_holds_fact, obj))
    return False


def _grounded(fact: Fact, pages: dict[int, str]) -> bool:
    page = pages.get(fact.page)
    if page is None:
        return False  # a page the agent was not given
    quote, text = _normalise(fact.verbatim_quote), _normalise(page)
    if len(quote) < MIN_QUOTE_CHARS or not _WORD.search(quote) or not _in_quote(fact, quote):
        return False
    # The value must also be in the page's matching text: a quote with one digit changed still scores
    # >= 90 against the page. Each place the quote matches is checked, not just the best one.
    return any(_matches(fact, quote, span, text) for span in _occurrences(quote, text))


def _in_quote(fact: Fact, quote: str) -> bool:
    """The value is printed in the quote as a number tied to its label, or is 0 written as Nil / a dash."""
    if fact.value == 0 and (_NIL.search(quote) or _DASH_CELL.search(quote)):
        return True
    if fact.value == int(fact.value) and 1990 <= fact.value <= 2100 and "year" not in fact.unit.casefold():
        return False  # a year such as "FY 2026" is a label, not a value
    hits = [(start, raw) for value, start, raw in _numbers(quote) if _same(fact.value, value)]
    # A leading 1-2 digit integer followed by text is a row number, e.g. "1 Fresh Issue ...".
    return any(not (start == 0 and _SERIAL.fullmatch(raw) and _WORD.search(quote[len(raw):]))
               for start, raw in hits)


def _matches(fact: Fact, quote: str, span: tuple[int, int], text: str) -> bool:
    start, end = span
    # Widen the span so a number cut at its edge is read whole.
    while start > 0 and text[start - 1] in _NUMBER_CHARS:
        start -= 1
    while end < len(text) and text[end] in _NUMBER_CHARS:
        end += 1
    matched = text[start:end]
    if fact.value == 0 and (_NIL.search(matched) or _DASH_CELL.search(matched)):
        found = True
    else:
        found = any(_same(fact.value, value) for value, _, _ in _numbers(matched))
    return found and _unit_ok(fact.unit, f"{quote} {matched}", text)


def _unit_ok(unit: str, near: str, page: str) -> bool:
    """Each unit word of the Fact (crore, million, %, shares...) is printed by the number, or, for a
    table cell whose unit is in a header, on the page; a different rupee scale by the number fails."""
    unit = unit.casefold()
    for word in (w for w in _UNIT_WORDS if w in unit):
        if word in near:
            continue
        if word in _SCALES and any(s in near for s in _SCALES):
            return False  # e.g. the Fact says crore, the quote says million
        if word not in page:
            return False
    return True


def _occurrences(quote: str, text: str) -> list[tuple[int, int]]:
    """(start, end) of each place in `text` the quote is printed: exactly, else fuzzily (score >= 90)."""
    exact = [(m.start(), m.end()) for m in re.finditer(re.escape(quote), text)]
    if exact:
        return exact
    spans, todo = [], [(0, len(text))]
    while todo and len(spans) < MAX_OCCURRENCES:
        lo, hi = todo.pop()
        if hi - lo < len(quote):
            continue  # a shorter stretch would be matched the other way round, inside the quote
        m = fuzz.partial_ratio_alignment(quote, text[lo:hi], score_cutoff=MIN_QUOTE_SCORE)
        if m is None:
            continue
        start, end = lo + m.dest_start, lo + m.dest_end
        spans.append((start, end))
        todo += [(lo, start), (end, hi)]
    return spans


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _numbers(text: str) -> list[tuple[float, int, str]]:
    """(value, start, digits as printed) for each number in `text`."""
    out = []
    for m in _NUMBER.finditer(text):
        n = float(m.group(3).replace(",", ""))
        minus = m.group(2) and (m.start(2) == 0 or not text[m.start(2) - 1].isalnum())
        negative = (m.group(1) and m.group(4)) or minus
        out.append((-n if negative else n, m.start(3), m.group(3)))
    return out


def _same(value: float, number: float) -> bool:
    return math.isclose(value, number, rel_tol=1e-12, abs_tol=1e-9)
