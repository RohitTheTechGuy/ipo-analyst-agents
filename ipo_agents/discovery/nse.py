"""NSE: the JSON API behind nseindia.com's IPO current-issues page (primary source)."""
from __future__ import annotations

from datetime import date, datetime

from ipo_agents.contracts import Ipo
from ipo_agents.discovery.common import (
    Source, expect_list, final_rhp, first_int, ipo_id, is_open, is_sme, price_band,
)

BASE = "https://www.nseindia.com"
REFERER = {"Referer": f"{BASE}/market-data/all-upcoming-issues-ipo"}


def _date(text: str) -> date:
    return datetime.strptime(text.strip(), "%d-%b-%Y").date()


class NseSource(Source):
    name = "NSE"

    def _rows(self, today: date) -> list:
        # NSE refuses API calls without the cookies its homepage sets; the homepage itself may 403.
        self._client.get(f"{BASE}/", headers={"Accept": "text/html"})
        return expect_list(self._get(f"{BASE}/api/ipo-current-issue", REFERER).json(), "ipo-current-issue")

    def _parse(self, row: dict, today: date) -> Ipo | None:
        if is_sme(row.get("series")):
            return None
        open_date, close_date = _date(row["issueStartDate"]), _date(row["issueEndDate"])
        if not is_open(today, open_date, close_date):
            return None
        symbol, series = row["symbol"], row.get("series") or "EQ"
        id_ = ipo_id(symbol, open_date)  # validates the symbol before it goes into a URL
        detail = self._details(symbol, series)
        return Ipo(
            ipo_id=id_,
            name=row["companyName"],
            symbol=symbol,
            open_date=open_date,
            close_date=close_date,
            price_band=price_band(row.get("issuePrice") or detail["Price Range"]),
            lot_size=first_int(detail["Bid Lot"]),
            rhp_url=final_rhp(detail.get("Red Herring Prospectus")),
        )

    def _details(self, symbol: str, series: str) -> dict[str, str]:
        data = self._get(f"{BASE}/api/ipo-detail?symbol={symbol}&series={series}", REFERER).json()
        return {item["title"]: item["value"] for item in data["issueInfo"]["dataList"] if item.get("title")}
