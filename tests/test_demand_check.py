"""Closing-day demand check: the advisor's call judged against the final subscription (seam A, offline).

The demand check runs on grok on the advisor ledger. Analysis day 13 Oct, close 16 Oct; the advisor says
NO BUY both days (test_advisor.WAIT)."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ipo_agents.advisor import ledger_id
from ipo_agents.contracts import Advice, CheckedDemand, DemandCheck, EvidencedPoint, OfferObjects, SubscriptionStatus
from ipo_agents.demand_check import contradicts, demand_level
from ipo_agents.runner import run_daily
from tests.fakes import FakeExchange, FakeMarketData, ScriptedLLM
from tests.test_advisor import ACME, CLOSE, OPEN, WAIT, closing_ipo, logged_ledgers
from tests.test_daily_updates import claim, market
from tests.test_synthesizer import OFFER_ANSWER


def subscription(total, retail=None):
    return SubscriptionStatus(total=claim(total, "Subscription (total)", CLOSE),
                              retail=claim(retail, "Subscription (Retail)", CLOSE) if retail is not None else None)


@pytest.mark.parametrize("total, retail, level", [
    (62.4, 15.1, "hot"), (3.0, 6.0, "hot"),  # retail alone makes it hot
    (2.3, 1.2, "normal"), (2.3, None, "normal"),
    (0.8, None, "weak"), (1.5, 0.6, "weak"),  # undersubscribed retail book
])
def test_demand_level_reads_total_and_retail(config, total, retail, level):
    assert demand_level(subscription(total, retail), config) == level


def test_no_total_figure_means_no_level_and_only_a_mismatch_contradicts(config):
    assert demand_level(None, config) is None
    assert demand_level(SubscriptionStatus(total=claim(None, "Subscription (total)", CLOSE)), config) is None
    assert contradicts("hot", "no_buy") and contradicts("weak", "buy")
    assert not contradicts("hot", "buy") and not contradicts("normal", "no_buy") and not contradicts(None, "buy")


RETAIL_POINT = EvidencedPoint(text="Retail demand of 15.1 times follows the ₹110 GMP",
                              evidence=["S:subscription", "S:gmp"])
ISSUE_POINT = EvidencedPoint(text="A ₹1,250 crore issue drew 62.4 times demand",
                             evidence=["F:offer_objects.issue_size", "S:subscription"])
HOLDS = DemandCheck(demand_reasons=[RETAIL_POINT, ISSUE_POINT], retail_reading="Retail buyers are chasing listing gains",
                    call_holds=True, verdict_vs_demand="Demand predicts the listing day; too little is verified to apply.")


def hot_market():
    return FakeMarketData(gmp_fact=claim(110.0, "GMP (unofficial)", CLOSE), subscription_status=SubscriptionStatus(
        qib=claim(140.2, "Subscription (QIB)", CLOSE), retail=claim(15.1, "Subscription (Retail)", CLOSE),
        total=claim(62.4, "Subscription (total)", CLOSE)))


def close_day(adapters, config, answer, close_market):
    adapters.exchange = FakeExchange([closing_ipo()])
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: WAIT, DemandCheck: answer},
                                     models=config.models)
    adapters.market = market(OPEN)
    run_daily(OPEN, adapters, config)
    adapters.exchange = FakeExchange([])
    adapters.market = close_market
    run_daily(CLOSE, adapters, config)
    return llm, adapters.notifier.messages[-1]


@pytest.mark.advisor
@pytest.mark.demand_check
def test_hot_demand_against_a_no_buy_is_explained_on_the_closing_update(adapters, config):
    llm, close = close_day(adapters, config, HOLDS, hot_market())

    assert close.splitlines()[-5:] == [
        "🔎 Demand check: HIGH demand",
        "• Retail demand of 15.1 times follows the ₹110 GMP",
        "• A ₹1,250 crore issue drew 62.4 times demand",
        "Retail: Retail buyers are chasing listing gains",
        "⚠️ HIGH demand vs NO BUY: the call still holds. Demand predicts the listing day; too little is verified to apply.",
    ]
    [call] = [c for c in llm.calls if c["task"] == "demand_check"]
    assert call["model"] == "grok"
    items = {i["id"]: i for i in json.loads(call["user"].split("\n", 1)[1])["items"]}
    assert items["K:demand"] == {"id": "K:demand", "level": "hot", "contradicts_call": True, "retail": 15.1,
                                 "total": 62.4, "unit": "times subscribed"}
    assert items["R:call"]["call"] == "no_buy" and items["R:reason[1]"]["text"] == "Most offer details are not disclosed"
    assert ("demand_check", ledger_id(ACME)) in logged_ledgers(config)
    saved = CheckedDemand.model_validate_json(
        (Path(config.work_dir) / ACME / "demand_check_2026-10-16.json").read_text(encoding="utf-8"))
    assert saved.level == "hot" and saved.contradicts and saved.call_holds


@pytest.mark.advisor
@pytest.mark.demand_check
def test_a_check_that_says_demand_weakens_the_call_is_shown_as_a_challenge(adapters, config):
    weakens = HOLDS.model_copy(update={"call_holds": False, "retail_reading": "",
                                       "verdict_vs_demand": "Institutions bid 140.2 times; the market sees value."})
    _, close = close_day(adapters, config, weakens, hot_market())

    assert close.splitlines()[-1] == ("❗ HIGH demand vs NO BUY: the check thinks demand weakens the call. "
                                      "Institutions bid 140.2 times; the market sees value.")
    assert not any(line.startswith("Retail:") for line in close.splitlines())


@pytest.mark.advisor
@pytest.mark.demand_check
def test_numbers_not_in_the_cited_items_are_dropped(adapters, config):
    made_up = EvidencedPoint(text="Retail demand hit 99 times", evidence=["S:subscription"])
    answer = HOLDS.model_copy(update={"demand_reasons": [made_up, ISSUE_POINT],
                                      "verdict_vs_demand": "The listing could open 45% up."})
    _, close = close_day(adapters, config, answer, hot_market())

    lines = close.splitlines()
    assert "• A ₹1,250 crore issue drew 62.4 times demand" in lines
    assert not any("99" in line for line in lines)
    assert lines[-1] == "⚠️ HIGH demand vs NO BUY: the call still holds."


@pytest.mark.advisor
@pytest.mark.demand_check
@pytest.mark.parametrize("answer, close_market", [
    (ConnectionError("connection dropped mid-call"), hot_market()),  # the check fails: the update still goes
    (HOLDS.model_copy(update={"demand_reasons": [EvidencedPoint(text="Demand is strong", evidence=["S:nope"])]}),
     hot_market()),  # no reason survives
    (HOLDS, replace(market(CLOSE), subscription_status=None)),  # no subscription figure: no call at all
])
def test_no_check_still_sends_the_normal_closing_update(adapters, config, answer, close_market):
    llm, close = close_day(adapters, config, answer, close_market)

    assert close.startswith("⏰ Acme Ltd IPO closes TODAY")
    assert "🔎" not in close
    tasks = [c["task"] for c in llm.calls]
    assert ("demand_check" in tasks) == (close_market.subscription_status is not None)


@pytest.mark.advisor
@pytest.mark.demand_check
def test_an_ipo_analysed_on_its_closing_day_gets_the_demand_check_after_its_summary(adapters, config):
    adapters.exchange = FakeExchange([closing_ipo()])
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: WAIT, DemandCheck: HOLDS},
                                     models=config.models)
    adapters.market = hot_market()
    run_daily(CLOSE, adapters, config)

    *_, summary, check = adapters.notifier.messages
    assert "🔎" not in summary
    assert check.splitlines()[:3] == ["⏰ Acme Ltd IPO closes TODAY",
                                      "Subscription: 62.4× total (QIB 140.2×, Retail 15.1×) as of 16 Oct",
                                      "🔎 Demand check: HIGH demand"]
    assert check.splitlines()[-1].startswith("⚠️ HIGH demand vs NO BUY: the call still holds.")
    assert [c["task"] for c in llm.calls].count("demand_check") == 1


@pytest.mark.advisor
@pytest.mark.demand_check
def test_a_failed_check_on_an_ipo_analysed_on_its_closing_day_sends_only_the_summary(adapters, config):
    adapters.exchange = FakeExchange([closing_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: WAIT}, models=config.models)  # no DemandCheck
    adapters.market = hot_market()
    summary = run_daily(CLOSE, adapters, config)

    assert summary.analysed == [ACME] and summary.notify_failed == []
    assert not any("🔎" in m for m in adapters.notifier.messages)


@pytest.mark.advisor
def test_after_close_the_update_shows_the_closing_day_call_not_the_analysis_day_one(adapters, config):
    from datetime import date
    close_day(adapters, config, HOLDS, hot_market())  # analysed 13 Oct, refreshed 16 Oct
    run_daily(date(2026, 10, 19), adapters, config)

    head, advisor, *_ = adapters.notifier.messages[-1].splitlines()
    assert head.startswith("📈 Acme Ltd IPO · bidding closed Fri 16 Oct")
    assert advisor.startswith("🧭 Advisor (16 Oct): ")
