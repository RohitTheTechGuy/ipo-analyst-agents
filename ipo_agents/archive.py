"""Local archive of every report generated and every Telegram message sent: reports/<ipo_id>/
(gitignored). Writing it never stops a send: a failure is logged and the run carries on."""
from __future__ import annotations

import logging
import shutil
from datetime import date, datetime
from pathlib import Path

from ipo_agents.contracts import Report

log = logging.getLogger(__name__)


def archive_report(report: Report, summary_text: str, reports_dir: str | Path, today: date,
                   partial: bool = False) -> Path | None:
    """reports/<ipo_id>/<run date>_<time>[_partial]/: report.json, the PDF, and the exact summary sent.
    A re-run gets its own folder, so earlier reports are kept."""
    try:
        base = Path(reports_dir) / report.ipo.ipo_id
        stem = f"{today.isoformat()}_{datetime.now():%H%M%S}" + ("_partial" if partial else "")
        run_dir, n = base / stem, 1
        while run_dir.exists():
            n += 1
            run_dir = base / f"{stem}-{n}"
        run_dir.mkdir(parents=True)
        (run_dir / "report.json").write_text(report.model_dump_json(indent=2))
        (run_dir / "telegram_summary.txt").write_text(summary_text + "\n")
        if report.pdf_path and Path(report.pdf_path).exists():
            shutil.copy2(report.pdf_path, run_dir / Path(report.pdf_path).name)
        return run_dir
    except Exception:
        log.warning("%s: report could not be archived", report.ipo.ipo_id, exc_info=True)
        return None


def archive_update(ipo_id: str, text: str, reports_dir: str | Path, today: date) -> None:
    """reports/<ipo_id>/updates/<date>.txt: the daily GMP + subscription update sent that day."""
    try:
        folder = Path(reports_dir) / ipo_id / "updates"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{today.isoformat()}.txt").write_text(text + "\n")
    except Exception:
        log.warning("%s: daily update could not be archived", ipo_id, exc_info=True)
