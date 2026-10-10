"""Crash-resume and the partial-budget report (ticket 21; spec stories 9, 52, 53).

Prices from config.yaml, USD per 1M tokens: terra in 2.00 / cached 0.20 / out 12.00; luna 0.20 / 0.02 / 1.20;
groq 0. `offer_objects` routes to terra, `sentiment_stance_tag` to groq, `sentiment_agent` to luna.

The section agents and sentiment run in parallel. Where a test needs one branch to finish before the other
reaches a given point, a scripted response waits on an Event, then sleeps briefly so the finished node's
return (and LangGraph's saving of its writes) completes first.
"""
import threading
import time
from collections import Counter
from datetime import date

import pytest

from ipo_agents.adapters import SearchHit
from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded
from ipo_agents.config import RunEstimate
from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.llm import LLMParseError
from ipo_agents.runner import run_daily
from ipo_agents.sentiment import SentimentPoints, StanceTag
from tests.fakes import FakeExchange, FakeSearch, ScriptedLLM, make_ipo, offer_objects

TODAY = date(2026, 10, 13)
OFFER = offer_objects(issue_size=Fact(
    value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))
POINTS = SentimentPoints(bull_points=["strong order book"], bear_points=["rich valuation"])
SETTLE_S = 0.3  # time for a finished node's writes to be saved before the other branch goes on


def tasks(llm):
    return Counter(c["task"] for c in llm.calls)


def articles(*slugs):
    """Search results for the first sentiment query: one dated article per slug."""
    return {"Acme Ltd IPO review": [
        SearchHit(url=f"https://news.example/{s}", title=f"Acme Ltd IPO: {s}", source_name="Example News",
                  published=date(2026, 10, 10), content=f"Article body for {s}.") for s in slugs]}


def no_estimate(config, **budget):
    """The pre-run estimate zeroed, so a run is degraded only by what it has actually spent."""
    return config.model_copy(update={"budget": config.budget.model_copy(
        update={"estimate": RunEstimate(tokens_per_page=0, output_tokens_per_page=0, fixed_usd=0), **budget})})


class BlockingSearch(FakeSearch):
    """Search that answers only once `ready` is set, holding the sentiment branch back until then."""

    def __init__(self, results, ready: threading.Event):
        super().__init__(results)
        self.ready = ready

    def search(self, query, **kw):
        assert self.ready.wait(10), "the other branch never finished"
        time.sleep(SETTLE_S)
        return super().search(query, **kw)


# --- seam B: analyse_rhp ---

def test_rerun_after_a_crash_does_not_repeat_the_branches_that_finished(tiny_rhp, adapters, config):
    sentiment_done = threading.Event()
    crash = {"on": True}

    def points(user):
        sentiment_done.set()
        return POINTS

    def offer(user):
        if crash["on"]:
            assert sentiment_done.wait(10)
            time.sleep(SETTLE_S)  # the sentiment node returns and its writes are saved
            raise ConnectionError("connection dropped mid-call")
        return OFFER

    adapters.search = FakeSearch(articles("a", "b"))
    adapters.llm = llm = ScriptedLLM({OfferObjects: offer, SentimentPoints: points,
                                      StanceTag: StanceTag(stance="subscribe", key_reasons=["growth"])},
                                     models=config.models)

    with pytest.raises(ConnectionError):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)
    assert tasks(llm) == {"offer_objects": 1, "sentiment_stance_tag": 2, "sentiment_agent": 1}

    crash["on"] = False
    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    # Only the crashed section is read again; sentiment comes from the checkpoint.
    assert tasks(llm) == {"offer_objects": 2, "sentiment_stance_tag": 2, "sentiment_agent": 1}
    assert report.status == "complete"
    assert report.offer.issue_size.value == 1250.0
    assert report.sentiment.tally["subscribe"] == 2
    assert report.sentiment.bull_points == ["strong order book"]


def test_resumed_run_counts_the_spend_from_before_the_crash(tiny_rhp, adapters, config):
    # Each offer call may write up to 40,000 tokens: worst case 40,000 x $12/1M = $0.48 plus its prompt.
    # The crashed call is billed 270,000 in x $2/1M + 40,000 out x $12/1M = $0.54 + $0.48 = $1.02 (under
    # the $1.05 degrade threshold, so the retry stays on terra). The retry's worst case then needs
    # $1.02 + $0.48 + prompt > $1.50: it is never sent. Had spend restarted at $0, it would have been.
    config = no_estimate(config).model_copy(update={"default_max_tokens": 40_000})
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER}, models=config.models, errors=[
        LLMParseError("reply did not match the schema", input_tokens=270_000, cached_tokens=0, output_tokens=40_000)])

    with pytest.raises(LLMParseError):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)
    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert len(llm.calls) == 1


def test_budget_stop_carries_a_partial_report_of_what_was_verified(tiny_rhp, adapters, config):
    offer_done = threading.Event()

    def offer(user):
        offer_done.set()
        return OFFER

    # The offer read is billed 125,000 out x $12/1M = $1.50: the whole cap. Sentiment searches only after
    # it; its stance tag runs on groq ($0), then the luna summary call can't fit and stops the run.
    adapters.search = BlockingSearch(articles("a"), offer_done)
    adapters.llm = llm = ScriptedLLM({OfferObjects: offer, SentimentPoints: POINTS,
                                      StanceTag: StanceTag(stance="subscribe")},
                                     models=config.models, usage=[(0, 0, 125_000)])

    with pytest.raises(BudgetExceeded) as stop:
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    report = stop.value.report
    assert report.status == "partial_budget"
    assert report.offer.issue_size.value == 1250.0
    assert report.offer.issue_size.page == 2
    assert report.sentiment is None
    assert tasks(llm) == {"offer_objects": 1, "sentiment_stance_tag": 1}


# --- seam A: run_daily ---

def test_crashed_ipo_resumes_on_the_next_run_and_is_reported_once(adapters, config):
    sentiment_done = threading.Event()
    crash = {"on": True}

    def offer(user):
        if crash["on"]:
            assert sentiment_done.wait(10)
            time.sleep(SETTLE_S)
            raise ConnectionError("connection dropped mid-call")
        return OFFER

    def points(user):
        sentiment_done.set()
        return POINTS

    adapters.exchange = FakeExchange([make_ipo()])
    adapters.search = FakeSearch(articles("a"))
    adapters.llm = llm = ScriptedLLM({OfferObjects: offer, SentimentPoints: points,
                                      StanceTag: StanceTag(stance="avoid")}, models=config.models)

    first = run_daily(TODAY, adapters, config)
    crash["on"] = False
    second = run_daily(TODAY, adapters, config)

    assert first.failed == ["ACME-2026-10-13"]
    assert second.analysed == ["ACME-2026-10-13"]
    assert tasks(llm) == {"offer_objects": 2, "sentiment_stance_tag": 1, "sentiment_agent": 1}
    [msg] = adapters.notifier.messages
    assert "Issue: ₹1,250 crore (p. 2)" in msg
    assert "Sentiment: 1 avoid" in msg


def test_budget_stop_sends_the_partial_summary_and_is_never_retried(adapters, config):
    offer_done = threading.Event()

    def offer(user):
        offer_done.set()
        return OFFER

    adapters.exchange = FakeExchange([make_ipo()])
    adapters.search = BlockingSearch(articles("a"), offer_done)
    # As above: the offer read spends the whole $1.50, so sentiment's luna call stops the run.
    adapters.llm = llm = ScriptedLLM({OfferObjects: offer, SentimentPoints: POINTS,
                                      StanceTag: StanceTag(stance="subscribe")},
                                     models=config.models, usage=[(0, 0, 125_000)])

    first = run_daily(TODAY, adapters, config)
    second = run_daily(TODAY, adapters, config)

    assert first.failed == ["ACME-2026-10-13"]
    [msg] = adapters.notifier.messages
    assert msg.startswith("⚠️ PARTIAL: stopped at the $1.50 budget cap\n")
    assert "Acme Ltd IPO (Mainboard)" in msg
    assert "Issue: ₹1,250 crore (p. 2)" in msg
    assert second.skipped == ["ACME-2026-10-13"]
    assert len(llm.calls) == 2


def test_forced_rerun_of_a_done_ipo_analyses_it_again(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER}, models=config.models)

    run_daily(TODAY, adapters, config)
    unforced = run_daily(TODAY, adapters, config)
    forced = run_daily(TODAY, adapters, config, force_ipo="ACME")

    assert unforced.skipped == ["ACME-2026-10-13"]
    assert forced.analysed == ["ACME-2026-10-13"]
    assert len(llm.calls) == 2
    assert len(adapters.notifier.messages) == 2
    assert adapters.notifier.messages[1] == adapters.notifier.messages[0]


def test_forced_rerun_can_only_spend_what_is_left_of_the_cap(adapters, config):
    # The first run's offer read is billed 124,995 out x $12/1M = $1.49994, leaving $0.00006. Past 70% of
    # the cap the re-run is degraded to luna, whose worst case alone is 4,000 x $1.20/1M = $0.0048: no call fits.
    config = no_estimate(config)
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER}, models=config.models, usage=[(0, 0, 124_995)])

    first = run_daily(TODAY, adapters, config)
    forced = run_daily(TODAY, adapters, config, force_ipo="ACME-2026-10-13")

    assert first.analysed == ["ACME-2026-10-13"]
    assert forced.failed == ["ACME-2026-10-13"]
    assert len(llm.calls) == 1
    assert adapters.notifier.messages[1].startswith("⚠️ PARTIAL: stopped at the $1.50 budget cap")
    assert run_daily(TODAY, adapters, config).skipped == ["ACME-2026-10-13"]
