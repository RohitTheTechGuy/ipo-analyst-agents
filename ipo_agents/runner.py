"""Daily run: discover -> dedupe -> analyse each new IPO -> notify (spec seam A)."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from ipo_agents.adapters import Adapters
from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded
from ipo_agents.config import Config
from ipo_agents.contracts import Report, RunSummary, Sentiment
from ipo_agents.notify.reminders import send_closing_reminders
from ipo_agents.store import TERMINAL, Store

log = logging.getLogger(__name__)

DISCLAIMER = "Not investment advice; generated from RHP + public sources."
VERDICT_LABELS = {
    "subscribe_long_term": "SUBSCRIBE (LONG TERM)",
    "subscribe_listing_gains": "SUBSCRIBE – LISTING GAINS ONLY",
    "neutral": "NEUTRAL",
    "avoid": "AVOID",
}


def _sentiment_line(s: Sentiment) -> str:
    if not s.available:
        return "Sentiment: unavailable"
    counts = [f"{n} {stance.replace('_only', '').replace('_', ' ')}" for stance, n in s.tally.items() if n]
    return "Sentiment: " + (" / ".join(counts) or "no recent coverage found")


def format_summary(report: Report) -> str:
    ipo = report.ipo
    lines = [
        f"📄 {ipo.name} IPO (Mainboard)",
        f"Open: {ipo.open_date:%d %b}–{ipo.close_date:%d %b} · Band {ipo.price_band} · Lot {ipo.lot_size}",
    ]
    if report.offer and report.offer.issue_size_cr.value is not None:
        f = report.offer.issue_size_cr
        lines.append(f"Issue: ₹{f.value:,.2f} cr (p. {f.page})")
    if report.sentiment:
        lines.append(_sentiment_line(report.sentiment))
    lines += ["", f"Verdict: {VERDICT_LABELS.get(report.verdict, 'N/A')}", "", DISCLAIMER]
    return "\n".join(lines)


def run_daily(today: date, adapters: Adapters, config: Config) -> RunSummary:
    store = Store(config.db_path)
    summary = RunSummary()
    for ipo in adapters.exchange.open_ipos(today):
        if store.status(ipo.ipo_id) in TERMINAL:
            summary.skipped.append(ipo.ipo_id)
            continue
        store.upsert(ipo, "analysing")
        try:
            rhp = adapters.downloader.download(ipo.rhp_url, Path(config.work_dir) / ipo.ipo_id)
            report = analyse_rhp(ipo, rhp, adapters, config)
        except BudgetExceeded:
            # Terminal: retrying would only spend more. The partial report arrives with ticket 21.
            log.warning("budget cap reached for %s", ipo.ipo_id)
            store.upsert(ipo, "failed_budget")
            summary.failed.append(ipo.ipo_id)
            try:
                adapters.notifier.send_message(
                    f"⚠️ {ipo.name}: analysis stopped at the ${config.budget.cap_usd:.2f} budget cap"
                )
            except Exception:
                log.exception("telegram send failed for %s", ipo.ipo_id)
                summary.notify_failed.append(ipo.ipo_id)
            continue
        except Exception:
            log.exception("analysis failed for %s", ipo.ipo_id)
            store.upsert(ipo, "failed")
            summary.failed.append(ipo.ipo_id)
            continue
        store.mark_done(ipo.ipo_id, report.verdict, report.rhp_sha256)
        summary.analysed.append(ipo.ipo_id)
        # The summary is today's notice, even if sending fails: no reminder on top of it.
        store.mark_reminded(ipo.ipo_id, today)
        try:
            adapters.notifier.send_message(format_summary(report))
        except Exception:
            log.exception("telegram summary send failed for %s", ipo.ipo_id)
            summary.notify_failed.append(ipo.ipo_id)
            continue
        if report.pdf_path:
            try:
                adapters.notifier.send_document(Path(report.pdf_path), caption=f"{ipo.name} IPO report")
            except Exception:
                log.exception("telegram report upload failed for %s", ipo.ipo_id)
                summary.notify_failed.append(ipo.ipo_id)
    summary.notify_failed += send_closing_reminders(today, store, adapters.notifier, VERDICT_LABELS)
    store.close()
    return summary
