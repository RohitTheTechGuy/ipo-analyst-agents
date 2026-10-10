"""SQLite store for IPO status (spec: Data contracts, status lifecycle)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ipo_agents.contracts import Ipo

SCHEMA = """
CREATE TABLE IF NOT EXISTS ipos (
  ipo_id TEXT PRIMARY KEY,
  name TEXT, open_date DATE, close_date DATE,
  price_band TEXT, lot_size INT,
  rhp_url TEXT, rhp_sha256 TEXT,
  status TEXT NOT NULL,  -- discovered | waiting_rhp | analysing | done | failed | failed_budget
  verdict TEXT, report_path TEXT,
  analysed_at TIMESTAMP, last_reminded_at TIMESTAMP
);
"""

TERMINAL = {"done", "failed_budget"}


class Store:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path)
        self._db.executescript(SCHEMA)

    def status(self, ipo_id: str) -> str | None:
        row = self._db.execute("SELECT status FROM ipos WHERE ipo_id = ?", (ipo_id,)).fetchone()
        return row[0] if row else None

    def upsert(self, ipo: Ipo, status: str) -> None:
        self._db.execute(
            """INSERT INTO ipos (ipo_id, name, open_date, close_date, price_band, lot_size, rhp_url, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(ipo_id) DO UPDATE SET status = excluded.status""",
            (ipo.ipo_id, ipo.name, ipo.open_date, ipo.close_date, ipo.price_band, ipo.lot_size, ipo.rhp_url, status),
        )
        self._db.commit()

    def mark_done(self, ipo_id: str, verdict: str | None, rhp_sha256: str) -> None:
        self._db.execute(
            """UPDATE ipos SET status = 'done', verdict = ?, rhp_sha256 = ?, analysed_at = CURRENT_TIMESTAMP
               WHERE ipo_id = ?""",
            (verdict, rhp_sha256, ipo_id),
        )
        self._db.commit()
