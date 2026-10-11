"""Number grounding for model-written prose (ticket 26, S3). Plain Python, no LLM.

The verifier checks Facts and Quotes; the prose a model writes beside them (a risk's topic, a claim's
paraphrase, a promoter's background) is not checked by it. This pass runs on each section's verified
output, before the report and the synthesizer digest see it. One rule per kind of prose:

- Prose beside a Quote (`_BESIDE_QUOTE`): every number in it must be in that quote's verified text, or be
  the value of a verified Fact in the same item (e.g. an IndustryClaim's figure). Otherwise the prose is
  replaced by the quote text itself, or blanked if the quote did not verify.
- Prose with no quote beside it (`_FREE_PROSE`, e.g. what_it_sells): blanked if it holds any number that is
  not the value of a verified Fact of the same section. Numbers are never edited out of a sentence: a
  sentence with a number taken out can say something else.

A number matches an allowed value at the precision written (40% matches 40.3, 40.0% does not), as in the
synthesizer's checks.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel

from ipo_agents.contracts import (AuditorRemark, BusinessIndustry, CitedText, Fact, IndustryClaim, MaterialItem,
                                  Promoter, Quote, RiskFactor)

log = logging.getLogger(__name__)

# A number as written in prose: 1,250 or 72.5; not the digits inside a word such as FY2024 or top10.
NUMBER = re.compile(r"(?<![A-Za-z\d,])\d[\d,]*(?:\.\d+)?")

# model -> (its verified Quote field, the prose fields written beside it)
_BESIDE_QUOTE: dict[type[BaseModel], tuple[str, tuple[str, ...]]] = {
    CitedText: ("source", ("text",)),
    IndustryClaim: ("source", ("claim",)),
    AuditorRemark: ("source", ("summary",)),
    RiskFactor: ("title", ("topic",)),
    MaterialItem: ("source", ("summary", "why_material")),
    Promoter: ("source", ("background",)),
}
# model -> prose fields with no quote beside them
_FREE_PROSE: dict[type[BaseModel], tuple[str, ...]] = {
    BusinessIndustry: ("what_it_sells",),
}


def numbers(text: str | None) -> set[float]:
    return {float(n.replace(",", "")) for n in NUMBER.findall(text or "")}


def unsupported(text: str, allowed: set[float]) -> list[str]:
    """The numbers in the text that match none of the allowed values at the precision written."""
    bad = []
    for raw in NUMBER.findall(text):
        number = float(raw.replace(",", ""))
        decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
        if not any(abs(round(a, decimals) - number) < 1e-9 for a in allowed):
            bad.append(raw)
    return bad


def fact_values(obj) -> set[float]:
    """The absolute values of every verified Fact under obj."""
    if isinstance(obj, Fact):
        return {abs(obj.value)} if obj.value is not None else set()
    if isinstance(obj, BaseModel):
        return set().union(set(), *(fact_values(getattr(obj, n)) for n in type(obj).model_fields))
    if isinstance(obj, list):
        return set().union(set(), *(fact_values(x) for x in obj))
    return set()


def grounded_or_blank(text: str, allowed: set[float]) -> str:
    """The text if every number in it is allowed, else ""."""
    return "" if unsupported(text, allowed) else text


def ground_section(output: BaseModel, group: str) -> tuple[BaseModel, list[str]]:
    """The section's output with its prose grounded (see the module docstring), and a note per change."""
    notes: list[str] = []
    grounded = _ground(output, group, fact_values(output), notes)
    for n in notes:
        log.warning("prose grounding: %s", n)
    return grounded, notes


def _ground(obj, path: str, section_facts: set[float], notes: list[str]):
    if isinstance(obj, list):
        return [_ground(x, f"{path}[{i}]", section_facts, notes) for i, x in enumerate(obj)]
    if not isinstance(obj, BaseModel) or isinstance(obj, (Fact, Quote)):
        return obj
    update = {name: _ground(getattr(obj, name), f"{path}.{name}", section_facts, notes)
              for name in type(obj).model_fields}
    if (rule := _BESIDE_QUOTE.get(type(obj))) is not None:
        quote_field, prose_fields = rule
        quote: Quote = getattr(obj, quote_field)
        verified = quote.page is not None and bool(quote.verbatim_quote)
        allowed = (numbers(quote.verbatim_quote) if verified else set()) | fact_values(obj)
        for name in prose_fields:
            bad = unsupported(getattr(obj, name), allowed)
            if bad:
                update[name] = quote.verbatim_quote if verified else ""
                notes.append(f"{path}.{name}: {', '.join(bad)} not in its quote; "
                             + ("replaced by the quote" if verified else "dropped (quote not verified)"))
    for name in _FREE_PROSE.get(type(obj), ()):
        bad = unsupported(getattr(obj, name), section_facts)
        if bad:
            update[name] = ""
            notes.append(f"{path}.{name}: {', '.join(bad)} not a verified fact of the section; dropped")
    return obj.model_copy(update=update)
