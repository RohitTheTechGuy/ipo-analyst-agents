"""Daily run: discover -> dedupe -> analyse each new IPO -> notify (spec seam A)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ipo_agents.adapters import Adapters
from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded
from ipo_agents.config import Config
from ipo_agents.contracts import Fact, Ipo, Report, RunSummary, Sentiment, SubscriptionStatus
from ipo_agents.discovery import DiscoveryError
from ipo_agents.notify.reminders import send_closing_reminders
from ipo_agents.store import TERMINAL, Store, same_company
from ipo_agents.verifier import COULD_NOT_VERIFY

log = logging.getLogger(__name__)

DISCLAIMER = "Not investment advice; generated from RHP + public sources."
NO_SOURCE_ALERT = "⚠️ Could not list open IPOs from any source today"
VERDICT_LABELS = {
    "subscribe_long_term": "SUBSCRIBE (LONG TERM)",
    "subscribe_listing_gains": "SUBSCRIBE – LISTING GAINS ONLY",
    "neutral": "NEUTRAL",
    "avoid": "AVOID",
}


def _sentiment_line(s: Sentiment) -> str:
    counts = [f"{n} {stance.replace('_only', '').replace('_', ' ')}" for stance, n in s.tally.items() if n]
    line = "Sentiment: " + ((" / ".join(counts) or "no recent coverage found") if s.available else "unavailable")
    if s.gmp and s.gmp.value is not None:
        line += f" · GMP ₹{s.gmp.value:,g} (unofficial)"
    return line


def _subscription_line(sub: SubscriptionStatus) -> str:
    parts = [f"{name} {f.value:.1f}×" for name, f in (("QIB", sub.qib), ("NII", sub.nii), ("Retail", sub.retail))
             if f and f.value is not None]
    return f"Subscription: {sub.total.value:.1f}× total" + (f" ({', '.join(parts)})" if parts else "")


def _fact_text(f: Fact) -> str:
    """A fact as written on its page, e.g. "₹55,000 million (p. 19)", or why it has no value."""
    if f.value is None:
        return COULD_NOT_VERIFY if f.not_disclosed_reason == COULD_NOT_VERIFY else "not disclosed"
    number = f"{f.value:,.0f}" if f.value == int(f.value) else f"{f.value:,.2f}"
    unit = f.unit.strip()
    # A rupee unit reads as "₹1,250 crore"; others follow the number, e.g. "84,941,997 equity shares".
    text = f"₹{number} {unit[1:].strip()}" if unit.startswith("₹") else f"{number} {unit}"
    return f"{text.strip()} (p. {f.page})"


def format_summary(report: Report) -> str:
    ipo = report.ipo
    lines = [
        f"📄 {ipo.name} IPO (Mainboard)",
        f"Open: {ipo.open_date:%d %b}–{ipo.close_date:%d %b} · Band {ipo.price_band} · Lot {ipo.lot_size}",
    ]
    if report.offer:
        lines.append(f"Issue: {_fact_text(report.offer.issue_size)} · OFS: {_fact_text(report.offer.ofs_shares)}")
    if report.sentiment:
        lines.append(_sentiment_line(report.sentiment))
        if (sub := report.sentiment.subscription) and sub.total.value is not None:
            lines.append(_subscription_line(sub))
    lines += ["", f"Verdict: {VERDICT_LABELS.get(report.verdict, 'N/A')}", "", DISCLAIMER]
    return "\n".join(lines)


def partial_marker(config: Config) -> str:
    return f"⚠️ PARTIAL: stopped at the ${config.budget.cap_usd:.2f} budget cap"


@dataclass
class RunPlan:
    """What a run would do today. Each Ipo carries its stored ipo_id, and the RHP link an earlier
    source found if today's source has none."""

    analyse: list[Ipo] = field(default_factory=list)
    hold: list[Ipo] = field(default_factory=list)  # no final RHP yet
    skip: list[tuple[Ipo, str]] = field(default_factory=list)  # (ipo, its terminal status)
    remind: list[tuple[str, str, date, str | None]] = field(default_factory=list)  # store.due_reminders rows
    discovery_error: str | None = None  # every source failed to list today's IPOs


def _is_forced(ipo: Ipo, original_id: str, force_ipo: str | None) -> bool:
    return force_ipo is not None and force_ipo.casefold() in {
        ipo.ipo_id.casefold(), original_id.casefold(), (ipo.symbol or "").casefold()}


def plan(today: date, adapters: Adapters, config: Config, force_ipo: str | None = None) -> RunPlan:
    """Decides, without writing anything, which open IPOs to analyse, hold or skip, and which reminders
    are due. `force_ipo` (an ipo_id or symbol) is analysed even if it is already done or failed_budget."""
    result = RunPlan()
    try:
        ipos = adapters.exchange.open_ipos(today)
    except DiscoveryError as exc:
        log.exception("no source could list open IPOs")
        result.discovery_error = str(exc)
        ipos = []
    store = Store(config.db_path)
    try:
        seen: list[Ipo] = []
        for listed in ipos:
            ipo = listed.model_copy(update={"ipo_id": store.known_id(listed)})
            if any(s.ipo_id == ipo.ipo_id or (same_company(s.name, ipo.name) and s.open_date == ipo.open_date
                                              and s.close_date == ipo.close_date) for s in seen):
                continue  # listed twice today: decide once
            seen.append(ipo)
            status = store.status(ipo.ipo_id)
            if status in TERMINAL and not _is_forced(ipo, listed.ipo_id, force_ipo):
                result.skip.append((ipo, status))
                continue
            if ipo.rhp_url is None and (known_rhp := store.rhp_url(ipo.ipo_id)):
                # A fallback source may lack the link an earlier source found; don't hold the IPO again.
                ipo = ipo.model_copy(update={"rhp_url": known_rhp, "is_draft": False})
            if ipo.rhp_url is None or ipo.is_draft:
                # Never analyse a draft: hold until the exchange page links the final RHP.
                result.hold.append(ipo)
            else:
                result.analyse.append(ipo)
        result.remind = store.due_reminders(today)
    finally:
        store.close()
    return result


def _send(ipo: Ipo, text: str, pdf_path: str | None, adapters: Adapters, summary: RunSummary) -> None:
    """Sends the summary, then the PDF if one was rendered; a failed send is recorded, never raised."""
    try:
        adapters.notifier.send_message(text)
    except Exception:
        log.exception("telegram summary send failed for %s", ipo.ipo_id)
        summary.notify_failed.append(ipo.ipo_id)
        return
    if pdf_path:
        try:
            adapters.notifier.send_document(Path(pdf_path), caption=f"{ipo.name} IPO report")
        except Exception:
            log.exception("telegram report upload failed for %s", ipo.ipo_id)
            summary.notify_failed.append(ipo.ipo_id)


def run_daily(today: date, adapters: Adapters, config: Config, force_ipo: str | None = None) -> RunSummary:
    """`force_ipo` (ipo_id or symbol) re-runs that IPO even if it is done or failed_budget. Spend is
    logged per ipo_id and never resets, so a forced re-run can only spend what is left of the cap."""
    todo = plan(today, adapters, config, force_ipo)
    summary = RunSummary(skipped=[ipo.ipo_id for ipo, _ in todo.skip])
    if todo.discovery_error is not None:
        try:
            adapters.notifier.send_message(f"{NO_SOURCE_ALERT}\n{todo.discovery_error}")
        except Exception:
            log.exception("telegram send failed for the no-source alert")
    store = Store(config.db_path)
    for ipo in todo.hold:
        store.upsert(ipo, "waiting_rhp")
        summary.waiting_rhp.append(ipo.ipo_id)
    for ipo in todo.analyse:
        store.upsert(ipo, "analysing")
        try:
            rhp = adapters.downloader.download(ipo.rhp_url, Path(config.work_dir) / ipo.ipo_id)
            report = analyse_rhp(ipo, rhp, adapters, config)
        except BudgetExceeded as exc:
            # Terminal: retrying would only spend more. Send what was verified before the stop.
            log.warning("budget cap reached for %s: %s", ipo.ipo_id, exc)
            store.upsert(ipo, "failed_budget")
            summary.failed.append(ipo.ipo_id)
            partial = exc.report  # None only if the stop came from outside the graph
            text = format_summary(partial) if partial else f"{ipo.name}: nothing was verified"
            _send(ipo, f"{partial_marker(config)}\n{text}", partial and partial.pdf_path, adapters, summary)
            continue
        except Exception:
            log.exception("analysis failed for %s", ipo.ipo_id)
            store.upsert(ipo, "failed")  # retried on the next run, resuming from its checkpoint
            summary.failed.append(ipo.ipo_id)
            continue
        store.mark_done(ipo.ipo_id, report.verdict, report.rhp_sha256)
        summary.analysed.append(ipo.ipo_id)
        # The summary is today's notice, even if sending fails: no reminder on top of it.
        store.mark_reminded(ipo.ipo_id, today)
        _send(ipo, format_summary(report), report.pdf_path, adapters, summary)
    summary.notify_failed += send_closing_reminders(today, store, adapters.notifier, VERDICT_LABELS)
    store.close()
    return summary
