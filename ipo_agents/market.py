"""Market data for the sentiment report: GMP and subscription status, by plain HTTP (no model).

Both are third-party claims, reported with their source and as-of date and never mixed with RHP facts.
- GMP: InvestorGain's live GMP report. The table is embedded in the page's Next.js payload.
- Subscription: NSE's consolidated bid details (NSE + BSE bids), then Chittorgarh's subscription page.

Only fixed hosts are fetched. The one discovered link (Chittorgarh's IPO page) must match the
Chittorgarh IPO-page pattern before it is used.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import date, datetime

import httpx

from ipo_agents.adapters import GMP_LABEL
from ipo_agents.contracts import ClaimedFact, Ipo, SubscriptionStatus
from ipo_agents.discovery import chittorgarh, nse
from ipo_agents.discovery.common import SYMBOL, describe

log = logging.getLogger(__name__)

IG_GMP = "https://www.investorgain.com/report/live-ipo-gmp/331/"
IG_HEADERS = {"Referer": "https://www.investorgain.com/"}
NSE_CATEGORY = f"{nse.BASE}/api/ipo-active-category?symbol={{symbol}}"
CG_IPO_PAGE = re.compile(r'href="https://www\.chittorgarh\.com/ipo/([a-z0-9-]+/\d+)/"')
CG_SUBSCRIPTION = "https://www.chittorgarh.com/ipo_subscription/{path}/"
# Subscription categories: NSE's srNo for each, and Chittorgarh's row label (QIB excludes anchors on both).
NSE_ROWS = {"QIB": "1", "NII": "2", "Retail": "3"}
CG_ROWS = {"QIB (Ex Anchor)": "QIB", "NII": "NII", "Retail": "Retail", "Total": "total"}
# Parse failures that mean "this source has no usable answer"; the next source is tried.
PARSE_ERRORS = (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError)
_TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return html.unescape(_TAG.sub("", fragment)).strip()


def _number(text: str) -> float | None:
    """'75' -> 75.0, '1,234.5' -> 1234.5; '--', '-' or '' -> None."""
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _name_key(name: str) -> str:
    """'Orient Cables (India) Ltd.' -> 'orientcablesindia': company names as the sources spell them."""
    words = re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
    return "".join(w for w in words if w not in ("limited", "ltd", "ipo"))


def _same_issue(name: str, open_date: date | None, ipo: Ipo) -> bool:
    """Same open date, and one name is the other plus a suffix (sources drop '(India)' and the like)."""
    a, b = _name_key(name), _name_key(ipo.name)
    return open_date == ipo.open_date and bool(a and b) and (a.startswith(b) or b.startswith(a))


def _next_payload(page: str) -> str:
    return "".join(json.loads(f'"{chunk}"') for chunk in chittorgarh.NEXT_PUSH.findall(page))


def _day_month(text: str, near: date) -> date:
    """'10-Oct 23:02' has no year: take the year that puts it nearest the IPO's open date."""
    day_month = text.split()[0]
    candidates = [datetime.strptime(f"{day_month}-{y}", "%d-%b-%Y").date() for y in (near.year - 1, near.year, near.year + 1)]
    return min(candidates, key=lambda d: abs(d - near))


def _times(value: float | None, category: str, url: str, source: str, as_of: date) -> ClaimedFact | None:
    if value is None:
        return None
    return ClaimedFact(value=value, unit="×", source_url=url, source_name=source, as_of=as_of,
                       label=f"Subscription ({category})")


class HttpMarketData:
    def __init__(self, client: httpx.Client):
        self._client = client

    def _get(self, url: str, headers: dict | None = None) -> httpx.Response:
        resp = self._client.get(url, headers=headers)
        resp.raise_for_status()
        return resp

    def gmp(self, ipo: Ipo) -> ClaimedFact | None:
        payload = _next_payload(self._get(IG_GMP, IG_HEADERS).text)
        key = '"initialTableResponse":'
        start = payload.find(key)
        if start == -1:
            raise ValueError("InvestorGain: no GMP table on the page")
        table, _ = json.JSONDecoder().raw_decode(payload, start + len(key))
        for row in table["reportTableData"]:
            if not _same_issue(row["~ipo_name"], date.fromisoformat(row["~Srt_Open"]) if row.get("~Srt_Open") else None, ipo):
                continue
            # "₹<b>75</b> (27.68%)<br>..."; a GMP not yet quoted is "₹<b>--</b>".
            match = re.match(r"\s*₹\s*<b>([^<]*)</b>", html.unescape(row["GMP"]))
            value = _number(match.group(1)) if match else None
            if value is None:
                return None
            return ClaimedFact(value=value, unit="₹", source_url=IG_GMP, source_name="InvestorGain",
                               as_of=_day_month(_text(row["Updated-On"]), ipo.open_date), label=GMP_LABEL)
        return None

    def subscription(self, ipo: Ipo) -> SubscriptionStatus | None:
        try:
            if found := self._nse(ipo):
                return found
        except PARSE_ERRORS as exc:
            log.warning("NSE subscription failed for %s (%s); trying Chittorgarh", ipo.ipo_id, describe(exc))
        return self._chittorgarh(ipo)

    def _nse(self, ipo: Ipo) -> SubscriptionStatus | None:
        if not SYMBOL.match(ipo.symbol):
            return None
        # NSE refuses API calls without the cookies its homepage sets; the homepage itself may 403.
        self._client.get(f"{nse.BASE}/", headers={"Accept": "text/html"})
        url = NSE_CATEGORY.format(symbol=ipo.symbol)
        data = self._get(url, nse.REFERER).json()
        # "Updated as on 29-Sep-2026 19:00:00"; before bidding opens it is "Updated as on null".
        stamp = re.search(r"\d{2}-[A-Za-z]{3}-\d{4}", data.get("updateTime") or "")
        if not stamp:
            return None
        as_of = datetime.strptime(stamp.group(), "%d-%b-%Y").date()
        rows = data["dataList"]
        by_sr = {r.get("srNo"): r for r in rows}
        [total] = [r for r in rows if r.get("category") == "Total"]
        total_x = _number(total.get("noOfTotalMeant") or "")
        if not total_x:  # no bids yet
            return None

        def times(row: dict | None, category: str) -> ClaimedFact | None:
            return _times(_number((row or {}).get("noOfTotalMeant") or ""), category, url, "NSE", as_of)

        return SubscriptionStatus(**{c.lower(): times(by_sr.get(sr), c) for c, sr in NSE_ROWS.items()},
                                  total=times(total, "total"))

    def _chittorgarh(self, ipo: Ipo) -> SubscriptionStatus | None:
        listing = self._get(chittorgarh._list_url(ipo.open_date), chittorgarh.HEADERS).json()["reportTableData"]
        for row in listing:
            opened = chittorgarh._date(row["Opening Date"]) if row.get("Opening Date") else None
            if _same_issue(_text(row.get("Company", "")), opened, ipo):
                break
        else:
            return None
        link = CG_IPO_PAGE.search(row["Company"])
        if not link:
            return None
        url = CG_SUBSCRIPTION.format(path=link.group(1))
        payload = _next_payload(self._get(url, chittorgarh.HEADERS).text)
        # The table under "IPO Subscription Status" is absent until bidding opens.
        block = re.search(r"IPO Subscription Status</h2>(.*?)</table>", payload, re.DOTALL)
        stamp = block and re.search(r"times on ([A-Z][a-z]+ \d{1,2}, \d{4})", block.group(1))
        if not stamp:
            return None
        as_of = datetime.strptime(stamp.group(1), "%B %d, %Y").date()
        found = {}
        for tr in re.findall(r"<tr>(.*?)</tr>", block.group(1), re.DOTALL):
            cells = [_text(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)]
            if len(cells) >= 2 and cells[0] in CG_ROWS:
                category = CG_ROWS[cells[0]]
                found[category.lower()] = _times(_number(cells[1]), category, url, "Chittorgarh", as_of)
        if not found.get("total"):
            return None
        return SubscriptionStatus(**found)
