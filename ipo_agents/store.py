"""SQLite store for IPO status (spec: Data contracts, status lifecycle)."""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from ipo_agents.contracts import Ipo

SCHEMA = """
CREATE TABLE IF NOT EXISTS ipos (
  ipo_id TEXT PRIMARY KEY,
  name TEXT, symbol TEXT, open_date DATE, close_date DATE,
  price_band TEXT, lot_size INT,
  rhp_url TEXT, rhp_sha256 TEXT,
  status TEXT NOT NULL,  -- discovered | waiting_rhp | analysing | done | failed | failed_budget
  verdict TEXT, report_path TEXT,
  analysed_at TIMESTAMP, last_reminded_at TIMESTAMP
);
-- Telegram picks: the last update read and the latest offer of IPOs to analyse
CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

def _symbol_from_id(ipo_id: str) -> str:
    """Rows stored before the symbol column: ids are "<SYMBOL>-<open date>" (discovery)."""
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", ipo_id)


TERMINAL = {"done", "failed_budget"}


def same_company(a: str, b: str) -> bool:
    """'HD Fire Protect Ltd.' and 'HD FIRE PROTECT LIMITED' name the same company, as do
    'Acevector Ltd. (Snapdeal IPO)' and 'Acevector Limited'."""
    def norm(name: str) -> str:
        name = re.sub(r"\([^)]*\)", " ", name.lower())  # parentheticals: "(Snapdeal IPO)"
        words = re.sub(r"[^a-z0-9 ]", " ", name).split()
        if words and words[-1] == "ipo":
            words = words[:-1]
        return " ".join(w for w in words if w not in {"ltd", "limited", "pvt", "private"})
    return norm(a) == norm(b)


class Store:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path)
        self._db.executescript(SCHEMA)
        # Migration: stores created before daily updates have no `symbol` column (rows get it on next upsert).
        if "symbol" not in {row[1] for row in self._db.execute("PRAGMA table_info(ipos)")}:
            self._db.execute("ALTER TABLE ipos ADD COLUMN symbol TEXT")
            self._db.commit()

    def known_id(self, ipo: Ipo) -> str:
        """The stored id for this issue: sources disagree on ids, so match company name + open and close dates."""
        if self.status(ipo.ipo_id) is not None:
            return ipo.ipo_id
        rows = self._db.execute(
            "SELECT ipo_id, name FROM ipos WHERE open_date = ? AND close_date = ?",
            (ipo.open_date.isoformat(), ipo.close_date.isoformat()),
        ).fetchall()
        return next((row_id for row_id, name in rows if same_company(name, ipo.name)), ipo.ipo_id)

    def rhp_url(self, ipo_id: str) -> str | None:
        row = self._db.execute("SELECT rhp_url FROM ipos WHERE ipo_id = ?", (ipo_id,)).fetchone()
        return row[0] if row else None

    def status(self, ipo_id: str) -> str | None:
        row = self._db.execute("SELECT status FROM ipos WHERE ipo_id = ?", (ipo_id,)).fetchone()
        return row[0] if row else None

    def upsert(self, ipo: Ipo, status: str) -> None:
        self._db.execute(
            """INSERT INTO ipos (ipo_id, name, symbol, open_date, close_date, price_band, lot_size, rhp_url, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(ipo_id) DO UPDATE SET
                 name = excluded.name, symbol = excluded.symbol, open_date = excluded.open_date, close_date = excluded.close_date,
                 price_band = excluded.price_band, lot_size = excluded.lot_size,
                 rhp_url = COALESCE(excluded.rhp_url, ipos.rhp_url), status = excluded.status""",
            (ipo.ipo_id, ipo.name, ipo.symbol, ipo.open_date.isoformat(), ipo.close_date.isoformat(), ipo.price_band, ipo.lot_size, ipo.rhp_url, status),
        )
        self._db.commit()

    def mark_done(self, ipo_id: str, verdict: str | None, rhp_sha256: str) -> None:
        self._db.execute(
            """UPDATE ipos SET status = 'done', verdict = ?, rhp_sha256 = ?, analysed_at = CURRENT_TIMESTAMP
               WHERE ipo_id = ?""",
            (verdict, rhp_sha256, ipo_id),
        )
        self._db.commit()

    def set_report_path(self, ipo_id: str, path: str) -> None:
        self._db.execute("UPDATE ipos SET report_path = ? WHERE ipo_id = ?", (path, ipo_id))
        self._db.commit()

    def report_path(self, ipo_id: str) -> str | None:
        """Where the IPO's report.json was saved; None if it never was."""
        row = self._db.execute("SELECT report_path FROM ipos WHERE ipo_id = ?", (ipo_id,)).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._db.close()

    def state(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM telegram_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        self._db.execute("INSERT INTO telegram_state (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        self._db.commit()

    # --- closing reminders (ticket 05) ---

    def due_updates(self, today: date) -> list[tuple[Ipo, str | None]]:
        """(ipo, verdict) for analysed IPOs still open today (open <= today <= close), not yet updated today."""
        rows = self._db.execute(
            """SELECT ipo_id, name, symbol, open_date, close_date, price_band, lot_size, rhp_url, verdict FROM ipos
               WHERE status = 'done' AND open_date <= ? AND close_date >= ?
                 AND (last_reminded_at IS NULL OR last_reminded_at != ?)
               ORDER BY close_date, ipo_id""",
            (today.isoformat(), today.isoformat(), today.isoformat()),
        ).fetchall()
        return [(Ipo(ipo_id=i, name=n, symbol=sym or _symbol_from_id(i), open_date=date.fromisoformat(o),
                     close_date=date.fromisoformat(c), price_band=band or "", lot_size=lot or 0, rhp_url=url), v)
                for i, n, sym, o, c, band, lot, url, v in rows]

    def mark_reminded(self, ipo_id: str, today: date) -> None:
        self._db.execute("UPDATE ipos SET last_reminded_at = ? WHERE ipo_id = ?", (today.isoformat(), ipo_id))
        self._db.commit()
