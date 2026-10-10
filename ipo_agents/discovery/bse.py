"""BSE: the JSON API behind bseindia.com's public-issues pages (first fallback)."""
from __future__ import annotations

from datetime import date, datetime

from ipo_agents.contracts import Ipo
from ipo_agents.discovery.common import (
    Source, SourceError, expect_list, final_rhp, first_int, ipo_id, is_open, price_band,
)

API = "https://api.bseindia.com/BseIndiaAPI/api"
HEADERS = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"}


def _date(text: str) -> date:
    return datetime.fromisoformat(text).date()


class BseSource(Source):
    name = "BSE"

    def _rows(self, today: date) -> list:
        data = self._get(f"{API}/GetPublicIssue_par_updated/w?flag=1&status=&exchange=&ir_flag=", HEADERS).json()
        if not isinstance(data, dict):
            raise SourceError(f"GetPublicIssue: expected an object, got {type(data).__name__}")
        return expect_list(data.get("Table"), "GetPublicIssue.Table")

    def _parse(self, row: dict, today: date) -> Ipo | None:
        # The list mixes IPOs with rights issues, buybacks, OFS and debt; keep mainboard IPOs only.
        if row.get("IR_flag") != "IPO" or row.get("eXCHANGE_PLATFORM") != "MainBoard":
            return None
        open_date, close_date = _date(row["Start_Dt"]), _date(row["End_Dt"])
        if not is_open(today, open_date, close_date):
            return None
        detail = self._get(f"{API}/GetMkt_ISSUE_BBS_IPO/w?IPO_NO={int(row['IPO_NO'])}", HEADERS).json()["IPONO_0"][0]
        symbol = detail.get("Symbol") or str(row["Scrip_cd"])
        return Ipo(
            ipo_id=ipo_id(symbol, open_date),
            name=row["Scrip_Name"],
            symbol=symbol,
            open_date=open_date,
            close_date=close_date,
            price_band=price_band(detail.get("Price_Band") or row["Price_Band"]),
            lot_size=first_int(detail["Market_Lot"]),
            rhp_url=final_rhp(detail.get("Prospectus_GID")),
        )
