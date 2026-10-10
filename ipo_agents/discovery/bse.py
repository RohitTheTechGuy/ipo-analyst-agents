"""BSE: the JSON API behind bseindia.com's public-issues pages (first fallback)."""
from __future__ import annotations

from datetime import date, datetime

import httpx

from ipo_agents.contracts import Ipo
from ipo_agents.discovery.common import first_int, is_open, is_sme, price_band

API = "https://api.bseindia.com/BseIndiaAPI/api"
HEADERS = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"}


def _date(text: str) -> date:
    return datetime.fromisoformat(text).date()


class BseSource:
    def __init__(self, client: httpx.Client):
        self._client = client

    def _json(self, url: str):
        resp = self._client.get(url, headers=HEADERS)
        resp.raise_for_status()
        return resp.json()

    def open_ipos(self, today: date) -> list[Ipo]:
        # The list mixes IPOs with rights issues, buybacks, OFS and debt; keep mainboard IPOs only.
        rows = self._json(f"{API}/GetPublicIssue_par_updated/w?flag=1&status=&exchange=&ir_flag=")["Table"]
        ipos = []
        for row in rows:
            if row.get("IR_flag") != "IPO" or row.get("eXCHANGE_PLATFORM") != "MainBoard":
                continue
            if is_sme(row.get("Scrip_Name")):
                continue
            open_date, close_date = _date(row["Start_Dt"]), _date(row["End_Dt"])
            if not is_open(today, open_date, close_date):
                continue
            detail = self._json(f"{API}/GetMkt_ISSUE_BBS_IPO/w?IPO_NO={row['IPO_NO']}")["IPONO_0"][0]
            symbol = detail.get("Symbol") or str(row["Scrip_cd"])
            ipos.append(Ipo(
                ipo_id=f"{symbol}-{open_date.isoformat()}",
                name=row["Scrip_Name"],
                symbol=symbol,
                open_date=open_date,
                close_date=close_date,
                price_band=price_band(detail.get("Price_Band") or row["Price_Band"]),
                lot_size=first_int(detail["Market_Lot"]),
                rhp_url=detail.get("Prospectus_GID") or None,
            ))
        return ipos
