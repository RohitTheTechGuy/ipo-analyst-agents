"""Helpers shared by the exchange sources."""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable
from datetime import date
from urllib.parse import urlsplit

import httpx

from ipo_agents.contracts import Ipo

log = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = httpx.Timeout(10, read=30)
SOURCE_BUDGET_S = 90.0
SYMBOL = re.compile(r"^[A-Z0-9&_-]+$")
# A plain case-insensitive "drhp" would match BSE's real final-RHP names ("...LimitedRHP_2026.pdf").
# So: "DRHP"/"Draft"/"DRAFT" as written anywhere (camelCase names), or drhp/draft in any case
# where not glued to a preceding letter.
DRAFT = re.compile(r"DRHP|Draft|DRAFT|(?i:(?<![a-z])drhp|(?<![a-z])draft)")


def make_client() -> httpx.Client:
    """The real HTTP client: browser-like headers, keeps cookies, follows redirects."""
    return httpx.Client(headers=BROWSER_HEADERS, timeout=TIMEOUT, follow_redirects=True)


class SourceError(Exception):
    """A source gave no usable answer (wrong payload shape, over its time budget, ...)."""


def describe(exc: BaseException) -> str:
    """A short, Telegram-sized reason: 'HTTP 403', 'ReadTimeout', "KeyError 'Market_Lot'"."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TransportError):
        return type(exc).__name__
    text = str(exc)
    return f"{type(exc).__name__} {text}"[:120] if text else type(exc).__name__


def is_open(today: date, open_date: date, close_date: date) -> bool:
    return open_date <= today <= close_date


def is_sme(label: str | None) -> bool:
    """For structured segment/series/category fields; 'SME' as a word, never inside one."""
    return bool(re.search(r"\bSME\b", label or "", re.IGNORECASE))


def ipo_id(symbol: str, open_date: date) -> str:
    """The id becomes a directory name (work_dir / ipo_id), so the symbol must be plain."""
    if not SYMBOL.match(symbol):
        raise ValueError(f"unsafe symbol {symbol!r}")
    return f"{symbol}-{open_date.isoformat()}"


def final_rhp(url: str | None) -> str | None:
    """The link if it points at a final RHP; None for a missing link or a draft (DRHP)."""
    if not url:
        return None
    filename = urlsplit(url).path.rsplit("/", 1)[-1]
    return None if DRAFT.search(filename) else url


def price_band(text: str) -> str:
    """'Rs.258 to Rs.271' / '258.00-271.00' -> '₹258–₹271'; a fixed price -> '₹750'."""
    prices = [f"₹{float(p):g}" for p in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))]
    if not prices:
        raise ValueError(f"no price in {text!r}")
    return "–".join(prices[:2])


def first_int(text: str) -> int:
    match = re.search(r"\d[\d,]*", text)
    if not match:
        raise ValueError(f"no number in {text!r}")
    return int(match.group().replace(",", ""))


def expect_list(value, what: str) -> list:
    if not isinstance(value, list):
        raise SourceError(f"{what}: expected a list, got {type(value).__name__}")
    return value


class Source:
    """One exchange source: lists rows, then turns each row into an Ipo (or None to skip it).

    HTTP errors and the time budget fail the whole source. A malformed row is logged and
    skipped; only if every candidate row is malformed does the source fail, with the first error.
    """

    name = "source"

    def __init__(self, client: httpx.Client, *, clock: Callable[[], float] = time.monotonic,
                 budget_s: float = SOURCE_BUDGET_S):
        self._client = client
        self._clock = clock
        self._budget_s = budget_s
        self._deadline = 0.0

    def _get(self, url: str, headers: dict | None = None) -> httpx.Response:
        if self._clock() > self._deadline:
            raise SourceError(f"over the {self._budget_s:g}s budget")
        resp = self._client.get(url, headers=headers)
        resp.raise_for_status()
        return resp

    def _rows(self, today: date) -> Iterable:
        raise NotImplementedError

    def _parse(self, row, today: date) -> Ipo | None:
        raise NotImplementedError

    def open_ipos(self, today: date) -> list[Ipo]:
        self._deadline = self._clock() + self._budget_s
        ipos, errors = [], []
        for row in self._rows(today):
            try:
                ipo = self._parse(row, today)
            except (httpx.HTTPError, SourceError):
                raise
            except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
                log.warning("%s: skipping malformed row (%s)", self.name, describe(exc))
                errors.append(exc)
                continue
            if ipo is not None:
                ipos.append(ipo)
        if not ipos and errors:
            raise errors[0]
        return ipos
