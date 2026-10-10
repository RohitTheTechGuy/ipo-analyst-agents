"""Helpers shared by the exchange sources."""
from __future__ import annotations

import re
from datetime import date

import httpx

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def make_client() -> httpx.Client:
    """The real HTTP client: browser-like headers, keeps cookies, follows redirects."""
    return httpx.Client(headers=BROWSER_HEADERS, timeout=30, follow_redirects=True)


def is_open(today: date, open_date: date, close_date: date) -> bool:
    return open_date <= today <= close_date


def is_sme(*labels: str | None) -> bool:
    return any("SME" in (label or "").upper() for label in labels)


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
