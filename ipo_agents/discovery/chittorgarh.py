"""Chittorgarh: the mainboard IPO report API plus each IPO's page (last fallback).

Before listing, Chittorgarh has no NSE symbol for an IPO, so the symbol falls back to one
derived from the page slug. The ipo_id can then differ from NSE's; Store.known_id matches the issue by company name + open date.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from ipo_agents.contracts import Ipo
from ipo_agents.discovery.common import is_open, is_sme, price_band

LIST_API = "https://webnodejs.chittorgarh.com/cloud/report/data-read/82/1/{month}/{year}/{fy}/0/mainboard/0?search="
HEADERS = {"Referer": "https://www.chittorgarh.com/", "Origin": "https://www.chittorgarh.com"}


def _date(text: str) -> date:
    return datetime.strptime(text.strip(), "%d-%b-%Y").date()


def _list_url(today: date) -> str:
    start = today.year if today.month >= 4 else today.year - 1  # Indian financial year, Apr-Mar
    return LIST_API.format(month=today.month, year=today.year, fy=f"{start}-{(start + 1) % 100:02d}")


def _field(page: str, key: str) -> str:
    """A field from the IPO record embedded (JSON-escaped) in the page's Next.js payload."""
    match = re.search(rf'\\"{key}\\":(?:\\"(.*?)\\"|([\d.]+))', page)
    if not match:
        raise ValueError(f"{key} not on page")
    return match.group(1) if match.group(1) is not None else match.group(2)


class ChittorgarhSource:
    def __init__(self, client: httpx.Client):
        self._client = client

    def _get(self, url: str) -> httpx.Response:
        resp = self._client.get(url, headers=HEADERS)
        resp.raise_for_status()
        return resp

    def open_ipos(self, today: date) -> list[Ipo]:
        ipos = []
        for row in self._get(_list_url(today)).json()["reportTableData"]:
            company = row.get("Company", "")
            name = re.sub(r"<[^>]+>", "", company).strip()
            if row.get("Issue Category") != "Mainboard" or is_sme(name):
                continue
            if not row.get("Opening Date") or not row.get("Closing Date"):
                continue  # dates not announced yet
            open_date, close_date = _date(row["Opening Date"]), _date(row["Closing Date"])
            if not is_open(today, open_date, close_date):
                continue
            page = self._get(re.search(r'href="([^"]+)"', company).group(1)).text
            slug = row["~URLRewrite_Folder_Name"].removesuffix("-ipo")
            symbol = row.get("~nse_symbol") or _field(page, "nse_symbol") or slug.replace("-", "").upper()
            rhp = _field(page, "prospectus_rhp")
            ipos.append(Ipo(
                ipo_id=f"{symbol}-{open_date.isoformat()}",
                name=name,
                symbol=symbol,
                open_date=open_date,
                close_date=close_date,
                price_band=price_band(f"{_field(page, 'issue_price_lower')} to {_field(page, 'issue_price_upper')}"),
                lot_size=int(_field(page, "market_lot_size")),
                rhp_url=rhp or None,
                is_draft=not rhp and bool(_field(page, "prospectus_drhp")),
            ))
        return ipos
