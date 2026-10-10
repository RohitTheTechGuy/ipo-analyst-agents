"""NSE: the JSON API behind nseindia.com's IPO current-issues page (primary source)."""
from __future__ import annotations

from datetime import date, datetime

import httpx

from ipo_agents.contracts import Ipo
from ipo_agents.discovery.common import first_int, is_open, is_sme, price_band

BASE = "https://www.nseindia.com"
REFERER = {"Referer": f"{BASE}/market-data/all-upcoming-issues-ipo"}


def _date(text: str) -> date:
    return datetime.strptime(text.strip(), "%d-%b-%Y").date()


class NseSource:
    def __init__(self, client: httpx.Client):
        self._client = client

    def _json(self, url: str):
        resp = self._client.get(url, headers=REFERER)
        resp.raise_for_status()
        return resp.json()

    def open_ipos(self, today: date) -> list[Ipo]:
        # NSE refuses API calls without the cookies its homepage sets; the homepage itself may 403.
        self._client.get(f"{BASE}/", headers={"Accept": "text/html"})
        ipos = []
        for row in self._json(f"{BASE}/api/ipo-current-issue"):
            if is_sme(row.get("series"), row.get("companyName")):
                continue
            open_date, close_date = _date(row["issueStartDate"]), _date(row["issueEndDate"])
            if not is_open(today, open_date, close_date):
                continue
            symbol = row["symbol"]
            detail = self._details(symbol, row.get("series") or "EQ")
            ipos.append(Ipo(
                ipo_id=f"{symbol}-{open_date.isoformat()}",
                name=row["companyName"],
                symbol=symbol,
                open_date=open_date,
                close_date=close_date,
                price_band=price_band(row.get("issuePrice") or detail["Price Range"]),
                lot_size=first_int(detail["Bid Lot"]),
                rhp_url=detail.get("Red Herring Prospectus") or None,
            ))
        return ipos

    def _details(self, symbol: str, series: str) -> dict[str, str]:
        data = self._json(f"{BASE}/api/ipo-detail?symbol={symbol}&series={series}")
        return {item["title"]: item["value"] for item in data["issueInfo"]["dataList"] if item.get("title")}
