"""Market Sentiment agent: dated public articles -> per-source stance -> tally (plan 5.4).

YouTube is out of scope for v1; GMP and subscription status are ticket 19.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ipo_agents.adapters import Adapters, SearchHit
from ipo_agents.config import Config
from ipo_agents.contracts import STANCES, ClaimedFact, Ipo, Sentiment, SentimentSource, Stance

log = logging.getLogger(__name__)

QUERIES = ("{name} IPO review", "{name} IPO subscribe or avoid", "{name} IPO broker note")
RESULTS_PER_QUERY = 10
MAX_SOURCES = 8
MAX_SOURCES_DEGRADED = 4  # past the degrade threshold (spec: Budget rules)
# Article text sent per stance call: enough for a review's argument, small enough for Groq's per-call limit.
MAX_ARTICLE_CHARS = 8_000
STANCE_MAX_TOKENS = 800
# Ipo carries no RHP date yet, so it is approximated as the open date minus 7 days (the RHP is usually
# filed with the RoC 3-7 days before the issue opens). This may admit a few articles from just before the
# RHP. TODO: use the RHP's own date once ingestion extracts it.
RHP_DATE_PROXY = timedelta(days=7)

# Spam heuristic, deliberately simple: a disclosure phrase anywhere in the title or text, or a bare
# "sponsored" in the title. The text needs the full phrase because "government-sponsored" is ordinary prose.
_PAID_PHRASES = r"sponsored (?:content|post|article|feature)|advertorial|paid partnership"
_SPAM_TEXT = re.compile(rf"\b(?:{_PAID_PHRASES})\b", re.IGNORECASE)
_SPAM_TITLE = re.compile(rf"\b(?:{_PAID_PHRASES}|sponsored)\b", re.IGNORECASE)

STANCE_SYSTEM = (
    "You read one third-party article about an Indian IPO and record its stance: subscribe, avoid, neutral, "
    "or listing_gains_only. Give at most 3 key reasons, paraphrased in your own words. List any numbers the "
    "article claims (GMP, subscription, valuation, targets) exactly as the article states them; never "
    "calculate or add numbers that are not in the article. Use ONLY the article text provided."
)
AGGREGATE_SYSTEM = (
    "You summarise the market view on an Indian IPO from stance-tagged sources. Return the common bull "
    "points and common bear points (at most 5 each), paraphrased. Use ONLY the reasons provided; do not "
    "add numbers or new arguments."
)


class ClaimedNumber(BaseModel):
    label: str = Field(description="what the number is, e.g. 'GMP' or 'P/E'")
    value: float
    unit: str


# List limits are applied in Python, not in the schemas: an extra item should be trimmed, not fail the parse.
MAX_REASONS = 3
MAX_POINTS = 5


class StanceTag(BaseModel):
    stance: Stance
    key_reasons: list[str] = Field(default=[], description=f"at most {MAX_REASONS}, paraphrased")
    claimed_numbers: list[ClaimedNumber] = []


class SentimentPoints(BaseModel):
    bull_points: list[str] = Field(default=[], description=f"at most {MAX_POINTS}")
    bear_points: list[str] = Field(default=[], description=f"at most {MAX_POINTS}")


def _is_spam(hit: SearchHit, denylist: list[str]) -> bool:
    host = (urlparse(hit.url).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in (d.lower() for d in denylist)):
        return True
    return bool(_SPAM_TITLE.search(hit.title) or _SPAM_TEXT.search(hit.content))


def _search(ipo: Ipo, adapters: Adapters, config: Config, cutoff: date) -> list[SearchHit]:
    """Dated, non-spam results published after the cut-off, across all queries in query order, each URL once.

    Undated hits are dropped: there is no way to show they post-date the RHP, and a claimed number
    needs an as-of date. The cut-off is re-checked here because providers filter loosely.
    """
    seen: dict[str, SearchHit] = {}
    for q in QUERIES:
        for h in adapters.search.search(q.format(name=ipo.name), max_results=RESULTS_PER_QUERY, published_after=cutoff):
            if h.published is not None and h.published > cutoff and not _is_spam(h, config.sentiment.denylist_domains):
                seen.setdefault(h.url, h)
    return list(seen.values())


def _tag(hit: SearchHit, adapters: Adapters, config: Config) -> SentimentSource:
    tag: StanceTag = adapters.llm.invoke_structured(
        model=config.routing["sentiment_stance_tag"],
        system=STANCE_SYSTEM,
        user=f"URL: {hit.url}\nSource: {hit.source_name}\nPublished: {hit.published}\nTitle: {hit.title}\n\n"
             f"{hit.content[:MAX_ARTICLE_CHARS]}",
        schema=StanceTag,
        max_tokens=STANCE_MAX_TOKENS,
        task="sentiment_stance_tag",
    ).parsed
    # Provenance comes from the search hit, not the model: every number is pinned to the article it came from.
    claimed = [ClaimedFact(label=n.label, value=n.value, unit=n.unit, source_url=hit.url,
                           source_name=hit.source_name, as_of=hit.published) for n in tag.claimed_numbers]
    return SentimentSource(url=hit.url, title=hit.title, source_name=hit.source_name, published=hit.published,
                           stance=tag.stance, key_reasons=tag.key_reasons[:MAX_REASONS], claimed_numbers=claimed)


def _points(sources: list[SentimentSource], adapters: Adapters, config: Config) -> SentimentPoints:
    lines = [f"- {s.source_name} ({s.published}): {s.stance}; " + "; ".join(s.key_reasons) for s in sources]
    return adapters.llm.invoke_structured(
        model=config.routing["sentiment_agent"],
        system=AGGREGATE_SYSTEM,
        user="\n".join(lines),
        schema=SentimentPoints,
        max_tokens=config.default_max_tokens,
        task="sentiment_agent",
    ).parsed


def _max_sources(adapters: Adapters) -> int:
    # Read defensively: the meter's `degraded` flag arrives with ticket 10 (model router).
    meter = getattr(adapters.llm, "meter", None)
    return MAX_SOURCES_DEGRADED if getattr(meter, "degraded", False) else MAX_SOURCES


def gather_sentiment(ipo: Ipo, adapters: Adapters, config: Config) -> Sentiment:
    if adapters.search is None:
        return Sentiment(available=False, note="sentiment unavailable: no web search configured")
    cutoff = ipo.open_date - RHP_DATE_PROXY
    note = f"sources published after {cutoff:%d %b %Y} (RHP date approximated as the open date minus 7 days)"
    try:
        hits = _search(ipo, adapters, config, cutoff)[:_max_sources(adapters)]
    except Exception as e:
        # Sentiment is optional: a search outage must not fail the RHP analysis. Model-call errors
        # (including BudgetExceeded) are not caught here and propagate as for any other agent.
        log.warning("web search failed for %s: %s", ipo.ipo_id, e)
        return Sentiment(available=False, note=f"sentiment unavailable: web search failed ({type(e).__name__})")
    sources = [_tag(h, adapters, config) for h in hits]
    # Counting is Python's job, not the model's.
    counts = Counter(s.stance for s in sources)
    points = _points(sources, adapters, config) if sources else SentimentPoints()
    return Sentiment(available=True, note=note, sources=sources, tally={k: counts[k] for k in STANCES},
                     bull_points=points.bull_points[:MAX_POINTS], bear_points=points.bear_points[:MAX_POINTS])
