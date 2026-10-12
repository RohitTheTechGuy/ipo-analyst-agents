"""The Telegram summary in plain English: the editor's checks, its fallbacks and the eval's rulebook."""
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import ipo_agents.runner as runner
from ipo_agents.advisor import advisor_llm
from ipo_agents.budget import BudgetExceeded
from ipo_agents.contracts import PlainSummary
from ipo_agents.summary_judge import Score, SummaryScores
from ipo_agents.plain_summary import MAX_LINES, plain_summary, problems
from ipo_agents.runner import format_summary, run_daily
from tests.fakes import FakeExchange, ScriptedLLM
from tests.report_fixture import full_report

TODAY = date(2026, 10, 13)

GOOD = PlainSummary(
    one_line_why="The shares look cheap next to similar listed companies, and demand is strong.",
    reasons=["Similar listed companies are valued at 38 times profit; this one at 15 times.",
             "Sales grew from ₹295 cr to ₹412.6 cr in two years.",
             "Ordinary investors have asked for 5.1 times the shares kept for them."],
    watch_outs=["The business brought in far less cash (₹12 cr) than the profit it reports (₹41.3 cr).",
                "Everything is made in one factory in Pune."],
    listing_signal="Unofficial trading before listing points to a 40% gain on the first day.")


def _llm(config, reply=GOOD):
    return ScriptedLLM({PlainSummary: reply}, models=config.models)


def _metered(config, reply=GOOD):
    """As run_daily calls it: metered on the IPO's advisor ledger, which picks the model by routing."""
    return advisor_llm(_llm(config, reply), "ACME-2026-10-13", config)


def test_a_plain_summary_is_short_shows_pythons_call_and_passes_the_rulebook(config):
    report = full_report()
    full = format_summary(report)
    scripted = _llm(config)

    text = plain_summary(report, full, advisor_llm(scripted, report.ipo.ipo_id, config))

    assert text is not None
    assert "🧭 Our call: BUY (medium confidence, to sell on the first trading day)" in text
    assert "Smallest application: 142 shares = ₹14,910" in text
    assert len([line for line in text.splitlines() if line.strip()]) <= MAX_LINES
    assert problems(text, full, report) == []
    assert scripted.calls[0]["task"] == "summary_editor" and scripted.calls[0]["model"] == "terra"


def test_an_item_with_a_number_not_in_the_full_summary_is_dropped(config):
    reply = GOOD.model_copy(update={"reasons": [GOOD.reasons[0], "Profit will double to ₹90 cr next year."]})

    text = plain_summary(full_report(), format_summary(full_report()), _metered(config, reply))

    assert "₹90 cr" not in text
    assert GOOD.reasons[0] in text


def test_an_item_with_a_flag_code_is_dropped(config):
    reply = GOOD.model_copy(update={"watch_outs": ["OFS_HEAVY: owners are selling.", GOOD.watch_outs[1]]})

    text = plain_summary(full_report(), format_summary(full_report()), _metered(config, reply))

    assert "OFS_HEAVY" not in text and GOOD.watch_outs[1] in text


def test_too_little_surviving_the_checks_means_no_plain_summary(config):
    reply = GOOD.model_copy(update={"one_line_why": "Profit will be ₹90 cr."})

    assert plain_summary(full_report(), format_summary(full_report()), _metered(config, reply)) is None


@pytest.mark.parametrize("error", [RuntimeError("provider down"), BudgetExceeded("advisor cap")])
def test_a_failed_editor_call_means_no_plain_summary(config, error):
    assert plain_summary(full_report(), format_summary(full_report()), _metered(config, error)) is None


def test_with_no_call_and_no_analyst_view_the_editor_is_not_asked(config):
    report = full_report().model_copy(update={"advice": None, "synthesis": None})
    scripted = _llm(config)

    assert plain_summary(report, format_summary(report), advisor_llm(scripted, report.ipo.ipo_id, config)) is None
    assert scripted.calls == []


def _scores(faithfulness=5, others=5) -> SummaryScores:
    def sc(n):
        return Score(score=n, reason="r")
    return SummaryScores(readability_non_finance=sc(others), decision_relevance=sc(others), coverage=sc(others),
                         faithfulness=sc(faithfulness), no_repetition=sc(others),
                         leftover_jargon=[], missing_decisive_points=["the single plant"], filler=[])


def _run(adapters, config, monkeypatch, editor=GOOD, judge=None, errors=()):
    report = full_report()
    monkeypatch.setattr(runner, "analyse_rhp", lambda ipo, rhp, adapters, config, today: report)
    adapters.exchange = FakeExchange([report.ipo])
    adapters.llm = ScriptedLLM({PlainSummary: editor, SummaryScores: judge or _scores()}, models=config.models,
                               errors=list(errors))
    run_daily(TODAY, adapters, config)
    return report


def _calls(adapters, task):
    return [c for c in adapters.llm.calls if c["task"] == task]


@pytest.mark.summary_editor
def test_run_daily_evaluates_the_plain_summary_then_sends_only_it_and_archives_the_review(adapters, config,
                                                                                         monkeypatch):
    report = _run(adapters, config, monkeypatch)

    [msg] = adapters.notifier.messages
    assert msg.startswith("📄 Acme Ltd IPO\n") and "Our call: BUY" in msg
    assert len(_calls(adapters, "summary_editor")) == 1 and len(_calls(adapters, "summary_judge")) == 1
    assert _calls(adapters, "summary_judge")[0]["model"] == "sol"
    [run_dir] = Path(config.reports_dir, report.ipo.ipo_id).iterdir()
    assert (run_dir / "telegram_summary.txt").read_text() == msg + "\n"
    assert (run_dir / "telegram_summary_full.txt").read_text() == format_summary(report) + "\n"
    [draft] = json.loads((run_dir / "summary_review.json").read_text())
    assert draft["passed"] and draft["text"] == msg and draft["scores"]["faithfulness"]["score"] == 5
    with sqlite3.connect(config.db_path) as db:
        ledgers = {row[0] for row in db.execute(
            "SELECT ipo_id FROM llm_calls WHERE task IN ('summary_editor', 'summary_judge')")}
    assert ledgers == {"ACME-2026-10-13:advisor"}


@pytest.mark.summary_editor
def test_a_failed_draft_is_revised_with_the_judges_feedback_and_the_passing_one_is_sent(adapters, config,
                                                                                       monkeypatch):
    second = GOOD.model_copy(update={"one_line_why": "Cheap next to similar companies, with strong demand."})
    editor = lambda user: second if "previous draft" in user else GOOD  # noqa: E731
    judge = lambda user: _scores() if second.one_line_why in user else _scores(faithfulness=2)  # noqa: E731

    _run(adapters, config, monkeypatch, editor=editor, judge=judge)

    [_, revision] = _calls(adapters, "summary_editor")
    assert "faithfulness 2/5" in revision["user"] and "Add these decisive points: the single plant" in revision["user"]
    [msg] = adapters.notifier.messages
    assert second.one_line_why in msg


@pytest.mark.summary_editor
def test_when_no_draft_is_fully_faithful_the_full_summary_is_sent_once(adapters, config, monkeypatch):
    report = _run(adapters, config, monkeypatch, judge=_scores(faithfulness=4))

    assert len(_calls(adapters, "summary_editor")) == 2  # one revision, then give up
    assert adapters.notifier.messages == [format_summary(report)]


@pytest.mark.summary_editor
def test_a_faithful_draft_that_misses_the_bar_is_still_sent_after_the_last_round(adapters, config, monkeypatch):
    _run(adapters, config, monkeypatch, judge=_scores(others=3))

    [msg] = adapters.notifier.messages
    assert "Our call: BUY" in msg


@pytest.mark.summary_editor
@pytest.mark.parametrize("failing", ["editor", "judge"])
def test_a_failed_editor_or_judge_call_sends_the_full_summary(adapters, config, monkeypatch, failing):
    errors = [RuntimeError("provider down")] if failing == "editor" else [None, RuntimeError("provider down")]

    report = _run(adapters, config, monkeypatch, errors=errors)

    assert adapters.notifier.messages == [format_summary(report)]


# --- the rulebook the eval runs (problems) ---------------------------------------------------------

def test_the_rulebook_names_each_problem_in_the_full_summary():
    report = full_report()
    full = format_summary(report)

    found = " | ".join(problems(full, full, report))

    assert "lines (max 15)" in found
    assert "flag codes: " in found and "OFS_HEAVY" in found
    assert "jargon: " in found and "P/E" in found and "PAT" in found
    assert "the call is not shown as" in found


def test_the_rulebook_catches_a_number_not_in_the_full_summary():
    report = full_report()
    full = format_summary(report)

    assert any("₹90" in p or "90" in p for p in problems("Profit will be ₹90 cr.", full, report))


def test_the_peer_multiples_line_is_kept_as_is_and_not_counted_as_jargon(config):
    report = full_report()
    full = format_summary(report)

    text = plain_summary(report, full, _metered(config))

    assert "📊 Price vs similar listed companies: P/E 15.0× vs peer median 38.0× (60% discount)" in text
    assert problems(text, full, report) == []
