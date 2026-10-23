"""Peer market data for peer valuation (plan 3, "Peer market prices / financials"). Plain HTTP, no model.

- Name -> NSE symbol: config's manual overrides first, then NSE's equity search.
- Price and market cap: NSE's quote API (the NextApi one; the old /api/quote-equity is blocked by
  NSE's bot wall), with Yahoo Finance as the fallback.
- Latest annual revenue, EBITDA, PAT, net worth, debt and cash: Yahoo Finance via yfinance (SYMBOL.NS).

Every number is a ClaimedFact with its source and as-of date; rupee amounts are converted to ₹ crore. A
number a source doesn't give stays None. Nothing is ever filled in from anywhere else.

yfinance makes its own network calls (requests / curl_cffi), so it sits behind `fetch_yfinance`, a seam
that returns a plain JSON-able dict; tests replace it with recorded payloads and test `parse_yfinance`.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from datetime import date, datetime
from urllib.parse import quote as urlquote

import httpx

from ipo_agents.contracts import ClaimedFact, PeerQuote
from ipo_agents.discovery import nse
from ipo_agents.discovery.common import SYMBOL, describe

log = logging.getLogger(__name__)

CRORE = 10_000_000
NSE_SEARCH = f"{nse.BASE}/api/NextApi/globalSearch/equity?symbol={{query}}"
NSE_QUOTE = (f"{nse.BASE}/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&marketType=N"
             f"&series=EQ&symbol={{symbol}}")
YAHOO_PAGE = "https://finance.yahoo.com/quote/{ticker}/financials"
NSE_NAME, YAHOO_NAME = "NSE", "Yahoo Finance"
# yfinance rows for each PeerQuote field, first one present wins. Income statement, then balance sheet.
INCOME_ROWS = {"revenue": ("Total Revenue", "Operating Revenue"), "ebitda": ("EBITDA",), "ebit": ("EBIT", "Operating Income"),
               "pat": ("Net Income Common Stockholders", "Net Income")}
BALANCE_ROWS = {"net_worth": ("Stockholders Equity", "Common Stock Equity"), "debt": ("Total Debt",),
                "cash": ("Cash And Cash Equivalents",)}
PARSE_ERRORS = (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError)
SUMMARY_CHARS = 600  # enough of Yahoo's business summary for the peer-fit check


def name_key(name: str) -> str:
    """'Mahindra & Mahindra Ltd.' -> 'mahindraandmahindra': company names as sources spell them."""
    words = re.sub(r"[^a-z0-9]+", " ", name.lower().replace("&", " and ")).split()
    return "".join(w for w in words if w not in ("limited", "ltd", "the"))


def _crore(rupees: float) -> float:
    return rupees / CRORE


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


# --- NSE ---------------------------------------------------------------------------------------

def parse_search(data: dict, name: str) -> str | None:
    """The EQ-series symbol whose company name is `name`; else the only EQ result; else None (never a guess)."""
    rows = [r for r in data["data"] if r.get("series") == "EQ" and SYMBOL.match(r.get("symbol") or "")]
    exact = [r for r in rows if name_key(r.get("companyName", "")) == name_key(name)]
    if len(exact) == 1:
        return exact[0]["symbol"]
    return rows[0]["symbol"] if len(rows) == 1 and not exact else None


def parse_nse_quote(data: dict, url: str) -> dict[str, ClaimedFact]:
    """Price (₹ per share) and total market cap (₹ crore) as of NSE's last update time."""
    e = data["equityResponse"][0]
    as_of = datetime.strptime(e["lastUpdateTime"], "%d-%b-%Y %H:%M:%S").date()
    found = {}
    price = _number(e["tradeInfo"].get("lastPrice")) or _number(e["metaData"].get("closePrice"))
    if price:
        found["price"] = ClaimedFact(value=price, unit="₹ per share", source_url=url, source_name=NSE_NAME,
                                     as_of=as_of, label="Last price")
    mcap = _number(e["tradeInfo"].get("totalMarketCap"))
    if mcap:
        found["market_cap"] = ClaimedFact(value=_crore(mcap), unit="₹ crore", source_url=url,
                                          source_name=NSE_NAME, as_of=as_of, label="Market cap")
    return found


# --- Yahoo Finance -----------------------------------------------------------------------------

def _frame(df) -> dict[str, dict[str, float | None]]:
    """A yfinance statement (rows x period-end columns) as {"YYYY-MM-DD": {row: value}}."""
    return {col.date().isoformat(): {str(row): _number(df.at[row, col]) for row in df.index} for col in df.columns}


def fetch_yfinance(ticker: str) -> dict:
    """The network seam: what yfinance returns for `ticker`, as plain JSON-able data. Tests replace it."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    history = t.history(period="5d")
    fast = t.fast_info
    try:
        info = t.info or {}
        profile = {"sector": info.get("sector"), "industry": info.get("industry"),
                   "summary": info.get("longBusinessSummary")}
    except Exception as exc:  # the profile is optional: without it the peer-fit check goes on the name alone
        log.warning("Yahoo Finance profile failed for %s (%s)", ticker, describe(exc))
        profile = None
    return {
        "profile": profile,
        "ticker": ticker,
        "currency": fast.currency,
        "last_price": _number(fast.last_price),
        "market_cap": _number(fast.market_cap),
        "price_date": history.index[-1].date().isoformat() if len(history) else None,
        "income_stmt": _frame(t.income_stmt),
        "balance_sheet": _frame(t.balance_sheet),
    }


def parse_yfinance(payload: dict) -> dict[str, ClaimedFact]:
    """Price, market cap and the latest fiscal year's figures, each as of its own date. A figure the
    latest year lacks is missing: an older year's number is never used in its place."""
    if payload.get("currency") != "INR":
        return {}  # not an NSE listing in rupees; nothing here is comparable
    ticker = payload["ticker"]
    url = YAHOO_PAGE.format(ticker=urlquote(ticker))
    found = {}

    def claim(field, rupees, as_of, unit="₹ crore", label=None):
        value = _number(rupees)
        if value is not None:
            found[field] = ClaimedFact(value=_crore(value) if unit == "₹ crore" else value, unit=unit,
                                       source_url=url, source_name=YAHOO_NAME, as_of=as_of, label=label)

    if payload.get("price_date"):
        price_date = date.fromisoformat(payload["price_date"])
        claim("price", payload.get("last_price"), price_date, unit="₹ per share", label="Last price")
        claim("market_cap", payload.get("market_cap"), price_date, label="Market cap")
    for statement, rows in (("income_stmt", INCOME_ROWS), ("balance_sheet", BALANCE_ROWS)):
        periods = sorted(payload.get(statement) or {}, reverse=True)
        if not periods:
            continue
        latest = payload[statement][periods[0]]
        for field, names in rows.items():
            value = next((latest[n] for n in names if latest.get(n) is not None), None)
            claim(field, value, date.fromisoformat(periods[0]), label=f"FY ending {periods[0]}")
        if statement == "income_stmt" and len(periods) > 1:
            prior = payload[statement][periods[1]]
            value = next((prior[n] for n in INCOME_ROWS["revenue"] if prior.get(n) is not None), None)
            claim("revenue_prior_year", value, date.fromisoformat(periods[1]), label=f"FY ending {periods[1]}")
    return found


def parse_profile(payload: dict) -> dict[str, str]:
    """Sector, industry and the start of the business summary, where Yahoo Finance gives them."""
    p = payload.get("profile") or {}
    found = {"sector": p.get("sector"), "industry": p.get("industry"),
             "business_summary": (p.get("summary") or "")[:SUMMARY_CHARS]}
    return {k: v.strip() for k, v in found.items() if isinstance(v, str) and v.strip()}


# --- the adapter -------------------------------------------------------------------------------

class HttpPeerData:
    """The real PeerData adapter: NSE over the shared httpx client, Yahoo Finance via `yf_fetch`."""

    def __init__(self, client: httpx.Client, *, overrides: dict[str, str] | None = None,
                 yf_fetch: Callable[[str], dict] = fetch_yfinance):
        self._client = client
        self._overrides = {name_key(k): v for k, v in (overrides or {}).items()}
        self._yf_fetch = yf_fetch

    def _json(self, url: str) -> dict:
        resp = self._client.get(url, headers={"Referer": f"{nse.BASE}/"})
        resp.raise_for_status()
        return resp.json()

    def resolve(self, symbol_or_name: str) -> str | None:
        """The NSE symbol: a manual override, the text itself if it is a symbol, else NSE's search."""
        if (key := name_key(symbol_or_name)) in self._overrides:
            return self._overrides[key]
        if SYMBOL.match(symbol_or_name):
            return symbol_or_name
        try:
            return parse_search(self._json(NSE_SEARCH.format(query=urlquote(symbol_or_name.lower()))), symbol_or_name)
        except PARSE_ERRORS as exc:
            log.warning("NSE search failed for %r (%s)", symbol_or_name, describe(exc))
            return None

    def quote(self, symbol_or_name: str) -> PeerQuote | None:
        symbol = self.resolve(symbol_or_name)
        if symbol is None or not SYMBOL.match(symbol):
            return None
        found: dict[str, ClaimedFact] = {}
        url = NSE_QUOTE.format(symbol=urlquote(symbol))
        try:
            found.update(parse_nse_quote(self._json(url), url))
        except PARSE_ERRORS as exc:
            log.warning("NSE quote failed for %s (%s); trying Yahoo Finance", symbol, describe(exc))
        profile: dict[str, str] = {}
        try:
            payload = self._yf_fetch(f"{symbol}.NS")
            yahoo, profile = parse_yfinance(payload), parse_profile(payload)
        except Exception as exc:  # third-party library, optional source: any failure means "missing"
            log.warning("Yahoo Finance failed for %s (%s)", symbol, describe(exc))
            yahoo = {}
        for field, fact in yahoo.items():
            found.setdefault(field, fact)  # NSE's price and market cap win; Yahoo's are the fallback
        return PeerQuote(name=symbol_or_name, symbol=symbol, **found, **profile) if found else None
