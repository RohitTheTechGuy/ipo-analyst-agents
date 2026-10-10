"""Real web search adapter: Tavily's search API, with raw page text so no separate fetch is needed."""
from __future__ import annotations

import os
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from ipo_agents.adapters import SearchHit

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _published(raw: str | None) -> date | None:
    """Tavily's news results carry an RFC 2822 date; anything unparseable counts as undated."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


class TavilySearch:
    """Reads TAVILY_API_KEY from the environment. Uses the "news" topic, the one that returns publish dates."""

    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        self._api_key = api_key or os.environ["TAVILY_API_KEY"]
        self._timeout = timeout

    def search(self, query: str, *, max_results: int, published_after: date) -> list[SearchHit]:
        resp = httpx.post(
            TAVILY_SEARCH_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "query": query,
                "topic": "news",
                "max_results": max_results,
                "start_date": published_after.isoformat(),
                "include_raw_content": "text",
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        hits = []
        for r in resp.json().get("results", []):
            host = urlparse(r["url"]).hostname or ""
            hits.append(SearchHit(
                url=r["url"],
                title=r.get("title") or "",
                source_name=host.removeprefix("www."),
                published=_published(r.get("published_date")),
                # raw_content is the full page; fall back to Tavily's snippet when it couldn't extract one.
                content=r.get("raw_content") or r.get("content") or "",
            ))
        return hits
