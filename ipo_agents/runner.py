"""Daily run: discover -> dedupe -> analyse each new IPO -> notify (spec seam A)."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ipo_agents.adapters import Adapters
from ipo_agents.advisor import advisor_llm
from ipo_agents.archive import archive_report
from ipo_agents.analysis import analyse_rhp, report_json_path
from ipo_agents.budget import BudgetExceeded
from ipo_agents.config import Config
from ipo_agents.contracts import CheckedAdvice, Fact, Ipo, Report, RunSummary, Sentiment, SubscriptionStatus
from ipo_agents.discovery import DiscoveryError
from ipo_agents.flags import summary_line as offer_summary_line
from ipo_agents.notify.reminders import send_daily_updates
from ipo_agents.plain_summary import evaluated_summary
from ipo_agents.report.labels import (DISCLAIMER, MARKET_HEADING, VERDICT_LABELS, advice_text, market_read,
                                      subscription_text)
from ipo_agents.store import TERMINAL, Store, same_company
from ipo_agents.valuation import operating_line, peer_lines
from ipo_agents.valuation import summary_line as valuation_summary_line
from ipo_agents.verifier import COULD_NOT_VERIFY

log = logging.getLogger(__name__)

NO_SOURCE_ALERT = "⚠️ Could not list open IPOs from any source today"
BULLET_CHARS = 500  # a reason or risk is shown whole; this only stops a runaway one (Telegram splits long messages)
SUMMARY_RED_FLAGS = 3
SUMMARY_ADVICE_REASONS = 3
SUMMARY_ADVICE_RISKS = 2
SUMMARY_MARKET_POINTS = 2  # bull and bear points each


def _sentiment_line(s: Sentiment) -> str:
    counts = [f"{n} {stance.replace('_only', '').replace('_', ' ')}" for stance, n in s.tally.items() if n]
    line = "Sentiment: " + ((" / ".join(counts) or "no recent coverage found") if s.available else "unavailable")
    if s.gmp and s.gmp.value is not None:
        line += f" · GMP ₹{s.gmp.value:,g} (unofficial)"
    return line


def _subscription_line(sub: SubscriptionStatus) -> str:
    return f"Subscription: {subscription_text(sub)}"


def _market_lines(report: Report) -> list[str]:
    """Its own section: a plain read, the tally/GMP and subscription lines, then the top bull and bear points."""
    s = report.sentiment
    lines = [f"📣 {MARKET_HEADING}", market_read(s, report.ipo.price_band), _sentiment_line(s)]
    if s.subscription and s.subscription.total.value is not None:
        lines.append(_subscription_line(s.subscription))
    if s.bull_points:
        lines += ["Bulls say:", *(_bullet(p) for p in s.bull_points[:SUMMARY_MARKET_POINTS])]
    if s.bear_points:
        lines += ["Bears say:", *(_bullet(p) for p in s.bear_points[:SUMMARY_MARKET_POINTS])]
    return lines


def _fact_text(f: Fact) -> str:
    """A fact as written on its page, e.g. "₹55,000 million (p. 19)", or why it has no value."""
    if f.value is None:
        return COULD_NOT_VERIFY if f.not_disclosed_reason == COULD_NOT_VERIFY else "not disclosed"
    number = f"{f.value:,.0f}" if f.value == int(f.value) else f"{f.value:,.2f}"
    unit = f.unit.strip()
    # A rupee unit reads as "₹1,250 crore"; others follow the number, e.g. "84,941,997 equity shares".
    text = f"₹{number} {unit[1:].strip()}" if unit.startswith("₹") else f"{number} {unit}"
    return f"{text.strip()} (p. {f.page})"


def _bullet(text: str) -> str:
    """One bullet of at most BULLET_CHARS characters, cut at a word boundary so a figure is never cut."""
    text = " ".join(text.split())
    if len(text) <= BULLET_CHARS:
        return "• " + text
    cut = text[:BULLET_CHARS]  # one more than fits: if it ends in a space, the word before it is whole
    head = cut.rsplit(" ", 1)[0] if " " in cut else ""
    return "• " + head.rstrip(" ,;:–-") + "…"


def _verdict_lines(report: Report) -> list[str]:
    """Plan 10: the verdict with its confidence, then why, then the top red flags."""
    s = report.synthesis
    if s is None:
        if report.status == "partial_budget":
            why = "budget cap"
        elif report.synthesis_error:
            why = "synthesizer failed"
        else:
            why = "nothing verified"
        return [f"Analyst view: not reached ({why})"]
    lines = [f"Analyst view: {VERDICT_LABELS[s.verdict]} (confidence: {s.confidence})"]
    if s.reasons:
        lines += ["Why:", *(_bullet(r.text) for r in s.reasons)]
    if s.top_red_flags:
        lines += ["Red flags:", *(_bullet(r.text) for r in s.top_red_flags[:SUMMARY_RED_FLAGS])]
    return lines


def _advice_lines(advice: CheckedAdvice | None) -> list[str]:
    """The advisor's call, headline, top reasons and top risks; or that it was not reached."""
    if advice is None:
        return ["🧭 Advisor: advice not reached"]
    lines = [f"🧭 Advisor: {advice_text(advice)}"]
    if advice.headline:
        lines.append(advice.headline)
    if advice.reasons:
        lines += ["Reasons:", *(_bullet(r.text) for r in advice.reasons[:SUMMARY_ADVICE_REASONS])]
    if advice.key_risks:
        lines += ["Risks:", *(_bullet(r.text) for r in advice.key_risks[:SUMMARY_ADVICE_RISKS])]
    return lines


def format_summary(report: Report) -> str:
    ipo = report.ipo
    # The IPO's name stays the first line (the chat preview); the advisor's call leads what follows.
    lines = [
        f"📄 {ipo.name} IPO (Mainboard)",
        f"Open: {ipo.open_date:%d %b}–{ipo.close_date:%d %b} · Band {ipo.price_band} · Lot {ipo.lot_size}",
        "",
        *_advice_lines(report.advice),
        "",
    ]
    if report.offer:
        lines.append(f"Issue: {_fact_text(report.offer.issue_size)} · OFS: {_fact_text(report.offer.ofs_shares)}")
        if offer_line := offer_summary_line(report):
            lines.append(offer_line)
    lines += ["", *_verdict_lines(report)]
    if valuation_line := valuation_summary_line(report):
        lines.append(valuation_line)
    if operating := operating_line(report):
        lines.append(operating)
    if peers := peer_lines(report):
        lines += ["Peers:", *(_bullet(p) for p in peers)]
    if report.sentiment:
        lines += ["", *_market_lines(report)]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def _summary_text(report: Report) -> str:
    """format_summary, or a minimal stand-in if it raises: a formatting bug must not stop the run."""
    try:
        return format_summary(report)
    except Exception:
        log.exception("formatting the summary failed for %s", report.ipo.ipo_id)
        return f"📄 {report.ipo.name} IPO\nsummary could not be formatted; see report"


def _plain_text(report: Report, full: str, adapters: Adapters, config: Config) -> tuple[str, list]:
    """(the one message to send, the evaluated drafts): the plain-English summary that passed the summary
    judge (billed to the IPO's advisor ledger), else the full one. Never fails the run."""
    try:
        text, drafts = evaluated_summary(report, full, advisor_llm(adapters.llm, report.ipo.ipo_id, config))
        return text or full, drafts
    except Exception:
        log.exception("plain summary failed for %s; sending the full summary", report.ipo.ipo_id)
        return full, []


def partial_marker(config: Config) -> str:
    return f"⚠️ PARTIAL: stopped at the ${config.budget.cap_usd:.2f} budget cap"


@dataclass
class RunPlan:
    """What a run would do today. Each Ipo carries its stored ipo_id, and the RHP link an earlier
    source found if today's source has none."""

    analyse: list[Ipo] = field(default_factory=list)
    hold: list[Ipo] = field(default_factory=list)  # no final RHP yet
    skip: list[tuple[Ipo, str]] = field(default_factory=list)  # (ipo, its terminal status)
    remind: list[tuple[Ipo, str | None]] = field(default_factory=list)  # store.due_updates: daily GMP/subscription
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
        result.remind = store.due_updates(today)
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


def run_daily(today: date, adapters: Adapters, config: Config, force_ipo: str | None = None,
              daily_updates: bool = True, select: Callable[[list[Ipo]], list[Ipo]] | None = None) -> RunSummary:
    """`force_ipo` (ipo_id or symbol) re-runs that IPO even if it is done or failed_budget. Spend is
    logged per ipo_id and never resets, so a forced re-run can only spend what is left of the cap.
    `daily_updates=False` skips the GMP/subscription updates for analysed IPOs (a single-IPO run).
    `select` picks which of the IPOs ready for analysis to analyse; the rest are left untouched in the
    store, so a later run offers them again while they are open."""
    todo = plan(today, adapters, config, force_ipo)
    if select is not None and todo.analyse:
        todo.analyse = select(list(todo.analyse))
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
            report = analyse_rhp(ipo, rhp, adapters, config, today=today)
        except BudgetExceeded as exc:
            # Terminal: retrying would only spend more. Send what was verified before the stop.
            log.warning("budget cap reached for %s: %s", ipo.ipo_id, exc)
            store.upsert(ipo, "failed_budget")
            summary.failed.append(ipo.ipo_id)
            partial = exc.report  # None only if the stop came from outside the graph
            text = _summary_text(partial) if partial else f"{ipo.name}: nothing was verified"
            text = f"{partial_marker(config)}\n{text}"
            if partial:
                archive_report(partial, text, config.reports_dir, today, partial=True)
            _send(ipo, text, partial and partial.pdf_path, adapters, summary)
            continue
        except Exception:
            log.exception("analysis failed for %s", ipo.ipo_id)
            store.upsert(ipo, "failed")  # retried on the next run, resuming from its checkpoint
            summary.failed.append(ipo.ipo_id)
            continue
        store.mark_done(ipo.ipo_id, report.verdict, report.rhp_sha256)
        if (saved := report_json_path(config, ipo.ipo_id)).exists():
            store.set_report_path(ipo.ipo_id, str(saved))  # the closing-day advisor refresh reloads it
        summary.analysed.append(ipo.ipo_id)
        # The summary is today's notice, even if sending fails: no reminder on top of it.
        store.mark_reminded(ipo.ipo_id, today)
        full = _summary_text(report)
        text, drafts = _plain_text(report, full, adapters, config)
        archive_report(report, text, config.reports_dir, today, full_text=full if text != full else None,
                       review=[d.as_dict() for d in drafts] or None)
        _send(ipo, text, report.pdf_path, adapters, summary)
    if daily_updates:
        summary.notify_failed += send_daily_updates(today, store, adapters, config, VERDICT_LABELS)
    store.close()
    return summary
