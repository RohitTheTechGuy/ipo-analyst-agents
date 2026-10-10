"""Chittorgarh: the mainboard IPO report API plus each IPO's page (last fallback).

Before listing, Chittorgarh has no NSE symbol for an IPO, so the symbol falls back to one
derived from the page slug. The ipo_id can then differ from NSE's; Store.known_id matches the issue by company name + open date.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

from ipo_agents.contracts import Ipo
from ipo_agents.discovery.common import (
    Source, SourceError, expect_list, final_rhp, ipo_id, is_open, price_band,
)

LIST_API = "https://webnodejs.chittorgarh.com/cloud/report/data-read/82/1/{month}/{year}/{fy}/0/mainboard/0?search="
HEADERS = {"Referer": "https://www.chittorgarh.com/", "Origin": "https://www.chittorgarh.com"}
NEXT_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def _date(text: str) -> date:
    return datetime.strptime(text.strip(), "%d-%b-%Y").date()


def _list_url(today: date) -> str:
    start = today.year if today.month >= 4 else today.year - 1  # Indian financial year, Apr-Mar
    return LIST_API.format(month=today.month, year=today.year, fy=f"{start}-{(start + 1) % 100:02d}")


def _record(page: str, slug: str) -> dict:
    """The IPO's own record from the page's Next.js payload: the object whose
    urlrewrite_folder_name is this IPO's slug (peer and FAQ records sit on the same page)."""
    payload = "".join(json.loads(f'"{chunk}"') for chunk in NEXT_PUSH.findall(page))
    decoder = json.JSONDecoder()
    marker = f'"urlrewrite_folder_name":{json.dumps(slug)}'
    for hit in re.finditer(re.escape(marker), payload):
        start = hit.start()
        while (start := payload.rfind("{", 0, start)) != -1:
            try:
                obj, _ = decoder.raw_decode(payload, start)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("urlrewrite_folder_name") == slug and "market_lot_size" in obj:
                return obj
    raise ValueError(f"no record for {slug} on its page")


def _optional(record: dict, key: str) -> str | None:
    value = record.get(key)
    return str(value) if value not in (None, "") else None


class ChittorgarhSource(Source):
    name = "Chittorgarh"

    def _rows(self, today: date) -> list:
        data = self._get(_list_url(today), HEADERS).json()
        if not isinstance(data, dict):
            raise SourceError(f"report: expected an object, got {type(data).__name__}")
        return expect_list(data.get("reportTableData"), "report.reportTableData")

    def _parse(self, row: dict, today: date) -> Ipo | None:
        company = row.get("Company", "")
        name = re.sub(r"<[^>]+>", "", company).strip()
        if row.get("Issue Category") != "Mainboard":  # SME issues are "SME"
            return None
        if not row.get("Opening Date") or not row.get("Closing Date"):
            return None  # dates not announced yet
        open_date, close_date = _date(row["Opening Date"]), _date(row["Closing Date"])
        if not is_open(today, open_date, close_date):
            return None
        slug = row["~URLRewrite_Folder_Name"]
        url = re.search(r'href="(https://www\.chittorgarh\.com/ipo/[^"]+)"', company).group(1)
        record = _record(self._get(url, HEADERS).text, slug)
        symbol = (row.get("~nse_symbol") or _optional(record, "nse_symbol")
                  or slug.removesuffix("-ipo").replace("-", "").upper())
        rhp = final_rhp(_optional(record, "prospectus_rhp"))
        return Ipo(
            ipo_id=ipo_id(symbol, open_date),
            name=name,
            symbol=symbol,
            open_date=open_date,
            close_date=close_date,
            price_band=price_band(f"{record['issue_price_lower']} to {record['issue_price_upper']}"),
            lot_size=int(record["market_lot_size"]),
            rhp_url=rhp,
            is_draft=not rhp and bool(_optional(record, "prospectus_drhp")),
        )
