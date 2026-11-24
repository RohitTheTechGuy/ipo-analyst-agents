"""The watch list: a company with no public offer document is reported once, and never analysed unasked."""
from __future__ import annotations

from datetime import date

import pytest

from ipo_agents.config import WatchItem
from ipo_agents.discovery.common import ListedIssue
from ipo_agents.runner import run_daily
from ipo_agents.watch import best, message, state_text

TODAY = date(2026, 11, 15)
RAZORPAY = "Razorpay Software Limited"
RHP = "https://www.sebi.gov.in/sebi_data/commondocs/nov-2026/Razorpay-RHP_p.pdf"


def hit(**fields) -> ListedIssue:
    return ListedIssue(name=RAZORPAY, source="Chittorgarh", **fields)


def watch_config(config):
    return config.model_copy(update={"watch": [WatchItem(name=RAZORPAY, aliases=["Razorpay"])]})


def test_nothing_is_sent_while_the_company_is_not_listed(adapters, config):
    adapters.exchange.listed_issues = []

    summary = run_daily(TODAY, adapters, watch_config(config))

    assert summary.watched == []
    assert adapters.notifier.messages == []
    assert adapters.llm.calls == []


def test_the_final_rhp_is_reported_once_and_never_analysed(adapters, config):
    adapters.exchange.listed_issues = [hit(open_date=date(2026, 12, 8), close_date=date(2026, 12, 10), rhp_url=RHP)]
    cfg = watch_config(config)

    first = run_daily(TODAY, adapters, cfg)
    second = run_daily(TODAY, adapters, cfg)

    assert first.watched == [RAZORPAY]
    [text] = adapters.notifier.messages
    assert "the final RHP is public" in text
    assert RHP in text
    assert "8 Dec–10 Dec" in text
    assert second.watched == [] and len(adapters.notifier.messages) == 1  # the same news is not repeated
    assert adapters.llm.calls == []  # no analysis, no spend


def test_the_call_to_action_points_at_the_daily_list(adapters, config):
    # "analyse Razorpay Software Limited" would be read as an RHP question, and only an open IPO can be picked.
    adapters.exchange.listed_issues = [hit(rhp_url=RHP)]

    run_daily(TODAY, adapters, watch_config(config))

    [text] = adapters.notifier.messages
    assert "Once bidding opens it appears in the 10:30 list" in text
    assert "Say “analyse" not in text


def test_dates_announced_without_an_rhp_are_reported(adapters, config):
    adapters.exchange.listed_issues = [hit(open_date=date(2026, 12, 8), close_date=date(2026, 12, 10))]

    run_daily(TODAY, adapters, watch_config(config))

    [text] = adapters.notifier.messages
    assert "on the IPO calendar: 8 Dec–10 Dec" in text
    assert "No final RHP linked yet" in text


def test_a_filed_draft_is_reported_and_never_analysed(adapters, config):
    adapters.exchange.listed_issues = [hit(draft_url="https://www.sebi.gov.in/.../Razorpay-DRHP.pdf")]

    summary = run_daily(TODAY, adapters, watch_config(config))

    [text] = adapters.notifier.messages
    assert "draft RHP is filed" in text
    assert summary.analysed == [] and adapters.llm.calls == []


def test_the_best_row_wins_when_a_source_has_several(adapters, config):
    adapters.exchange.listed_issues = [hit(open_date=date(2026, 12, 8)), hit(rhp_url=RHP)]

    run_daily(TODAY, adapters, watch_config(config))

    [text] = adapters.notifier.messages
    assert "the final RHP is public" in text  # the RHP beats a dated row without one


def test_a_failed_watch_check_is_recorded_and_retried(adapters, config):
    def boom(names, today):
        raise RuntimeError("source down")

    adapters.exchange.listed = boom

    summary = run_daily(TODAY, adapters, watch_config(config))

    assert summary.watch_failed == [RAZORPAY]
    assert summary.notify_failed == []  # a failed check is not a failed send
    assert summary.watched == []
    assert adapters.notifier.messages == []


def test_a_day_the_source_is_down_does_not_repeat_the_news(adapters, config):
    cfg = watch_config(config)
    adapters.exchange.listed_issues = [hit(rhp_url=RHP)]
    run_daily(TODAY, adapters, cfg)

    def boom(names, today):
        raise RuntimeError("source down")

    working = adapters.exchange.listed
    adapters.exchange.listed = boom
    run_daily(TODAY, adapters, cfg)
    adapters.exchange.listed = working
    third = run_daily(TODAY, adapters, cfg)

    assert len(adapters.notifier.messages) == 1  # the RHP news went out once, before the outage
    assert third.watched == []


def test_the_exchange_raises_when_a_watch_source_fails():
    from ipo_agents.discovery import DiscoveryError, FallbackExchange

    class Down:
        name = "Down"

        def listed(self, names, today):
            raise RuntimeError("HTTP 403")

    with pytest.raises(DiscoveryError, match="Down"):
        FallbackExchange([Down()]).listed(["Razorpay"], TODAY)


def test_a_quiet_day_with_a_failed_watch_check_still_gets_its_line():
    from ipo_agents import cli
    from ipo_agents.contracts import RunSummary

    summary = RunSummary(watch_failed=[RAZORPAY])

    assert cli.quiet_day(summary)
    text = cli.quiet_day_text(summary)
    assert text.startswith("😴 No mainboard IPO open today")
    assert "ran fine" not in text and f"watch-list check failed for {RAZORPAY}" in text
    assert cli.quiet_day_text(RunSummary()) == cli.QUIET_DAY


def test_the_message_text_is_short_and_plain():
    assert message(WatchItem(name=RAZORPAY), hit()) == (
        f"👀 {RAZORPAY} now appears in the IPO list, with no dates or RHP yet.")
    assert state_text(None) == "not listed by the sources yet"
    assert best([]) is None


def test_the_picks_job_does_not_check_the_watch_list(adapters, config):
    # --telegram-picks runs every few minutes: the watch is a once-a-day check, so it never runs there.
    adapters.exchange.listed_issues = [hit(rhp_url=RHP)]

    summary = run_daily(TODAY, adapters, watch_config(config), daily_updates=False)

    assert summary.watched == []
    assert adapters.notifier.messages == []
