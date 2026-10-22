"""Demand check: the market as devil's advocate against the advisor's call and its reasons (seam A, offline).

The demand check runs on grok on the advisor ledger. Analysis day 13 Oct, close 16 Oct; the advisor says
NO BUY both days (test_advisor.WAIT)."""
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from ipo_agents.advisor import ledger_id
from ipo_agents import analysis
from ipo_agents.contracts import (Advice, CheckedDemand, ClaimedFact, DemandCheck, EvidencedPoint, HypothesisCheck,
                                  OfferObjects, Sentiment, SubscriptionStatus)
from ipo_agents.demand_check import contradicts, demand_level, devils_advocate
from ipo_agents.report.render import render_html
from ipo_agents.runner import run_daily
from tests.fakes import FakeExchange, FakeMarketData, ScriptedLLM
from tests.test_advisor import ACME, CLOSE, GO, OPEN, WAIT, closing_ipo, logged_ledgers
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
                    call_holds=True, verdict_vs_demand="Demand predicts the listing day; too little is verified to apply.",
                    hypotheses=[])
# WAIT's reasons: [0] the ₹1,250 crore issue draws institutions, [1] most offer details are not disclosed.
SIZE_HOLDS = HypothesisCheck(reason_id="R:reason[0]", holds=True, why="Issue size: institutions bid 140.2 times, as expected",
                             evidence=["S:subscription"])
OPACITY_WEAKENED = HypothesisCheck(reason_id="R:reason[1]", holds=False,
                                   why="Opacity: big institutions bid heavily anyway, so they may know more",
                                   evidence=["S:subscription"])
DEVIL = HOLDS.model_copy(update={"hypotheses": [OPACITY_WEAKENED, SIZE_HOLDS]})


def hot_market():
    return FakeMarketData(gmp_fact=claim(110.0, "GMP (unofficial)", CLOSE), subscription_status=SubscriptionStatus(
        qib=claim(140.2, "Subscription (QIB)", CLOSE), retail=claim(15.1, "Subscription (Retail)", CLOSE),
        total=claim(62.4, "Subscription (total)", CLOSE)))


def quiet_market(day):
    """2.3× subscribed but a ₹5 GMP (under 5% of ₹105): demand isn't high enough to argue with a NO BUY."""
    return replace(market(day), gmp_fact=claim(5.0, "GMP (unofficial)", day))


def close_day(adapters, config, answer, close_market):
    adapters.exchange = FakeExchange([closing_ipo()])
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: WAIT, DemandCheck: answer},
                                     models=config.models)
    adapters.market = quiet_market(OPEN)  # no devil's advocate on analysis day: only the closing day's check
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
        "🔥 Why demand is high despite the NO BUY (the market as devil's advocate):",
        "• Retail demand of 15.1 times follows the ₹110 GMP",
        "• A ₹1,250 crore issue drew 62.4 times demand",
        "Retail: Retail buyers are chasing listing gains",
        "⚠️ HIGH demand vs NO BUY: the call still holds. Demand predicts the listing day; too little is verified to apply.",
    ]
    [call] = [c for c in llm.calls if c["task"] == "demand_check"]
    assert call["model"] == "grok"
    items = {i["id"]: i for i in json.loads(call["user"].split("\n", 1)[1])["items"]}
    assert items["K:demand"] == {"id": "K:demand", "level": "hot", "contradicts_call": True, "retail": 15.1,
                                 "total": 62.4, "unit": "times subscribed", "devils_advocate": True,
                                 "gmp_pct_of_upper_band": 104.8}
    assert items["R:call"]["call"] == "no_buy" and items["R:reason[1]"]["text"] == "Most offer details are not disclosed"
    assert ("demand_check", ledger_id(ACME)) in logged_ledgers(config)
    saved = CheckedDemand.model_validate_json(
        (Path(config.work_dir) / ACME / "demand_check_2026-10-16.json").read_text(encoding="utf-8"))
    assert saved.level == "hot" and saved.devils_advocate and saved.contradicts and saved.call_holds


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
    assert "🔎" not in close and "🔥" not in close
    tasks = [c["task"] for c in llm.calls]
    assert ("demand_check" in tasks) == (close_market.subscription_status is not None)


@pytest.mark.advisor
@pytest.mark.demand_check
def test_an_ipo_analysed_on_its_closing_day_has_the_demand_check_in_its_summary(adapters, config):
    adapters.exchange = FakeExchange([closing_ipo()])
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: WAIT, DemandCheck: HOLDS},
                                     models=config.models)
    adapters.market = hot_market()
    run_daily(CLOSE, adapters, config)

    [summary] = [m for m in adapters.notifier.messages if m.startswith("📄")]
    lines = summary.splitlines()
    assert "🔥 Why demand is high despite the NO BUY (the market as devil's advocate):" in lines
    assert any(line.startswith("⚠️ HIGH demand vs NO BUY: the call still holds.") for line in lines)
    assert [c["task"] for c in llm.calls].count("demand_check") == 1


@pytest.mark.advisor
@pytest.mark.demand_check
def test_a_failed_check_on_an_ipo_analysed_on_its_closing_day_sends_only_the_summary(adapters, config):
    adapters.exchange = FakeExchange([closing_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: WAIT}, models=config.models)  # no DemandCheck
    adapters.market = hot_market()
    summary = run_daily(CLOSE, adapters, config)

    assert summary.analysed == [ACME] and summary.notify_failed == []
    assert not any("🔎" in m or "🔥" in m for m in adapters.notifier.messages)


@pytest.mark.advisor
def test_after_close_the_update_shows_the_closing_day_call_not_the_analysis_day_one(adapters, config):
    from datetime import date
    close_day(adapters, config, HOLDS, hot_market())  # analysed 13 Oct, refreshed 16 Oct
    run_daily(date(2026, 10, 19), adapters, config)

    head, advisor, *_ = adapters.notifier.messages[-1].splitlines()
    assert head.startswith("📈 Acme Ltd IPO · bidding closed Fri 16 Oct")
    assert advisor.startswith("🧭 Advisor (16 Oct): ")


# --- high demand against a NO BUY: the market as devil's advocate --------------------------------------------------

def sentiment(total, gmp, retail=None):
    return Sentiment(available=True, subscription=subscription(total, retail),
                     gmp=claim(gmp, "GMP (unofficial)", CLOSE) if gmp is not None else None)


@pytest.mark.parametrize("call, total, retail, gmp, band, devil", [
    ("no_buy", 2.0, None, 10.5, "₹100–₹105", True),       # 2× total, GMP 10% of ₹105
    ("no_buy", 2.3, 1.2, 42.0, "₹100–₹105", True),        # retail above 1×: no higher bar on it
    ("no_buy", 2.3, 1.0, 42.0, "₹100–₹105", False),       # retail not above 1×
    ("no_buy", 1.9, None, 42.0, "₹100–₹105", False),      # total under 2×
    ("no_buy", 12.0, None, 9.45, "₹100–₹105", False),     # GMP 9%
    ("buy", 62.4, 15.1, 110.0, "₹100–₹105", False),       # a BUY the market wants
    ("buy", 1.5, 0.4, 5.0, "₹100–₹105", True),            # a BUY the market shuns: under 2× and GMP under 10%
    ("buy", 1.5, None, 42.0, "₹100–₹105", False),         # low bids but a 40% GMP: not both low
    ("buy", 3.0, None, 5.0, "₹100–₹105", False),          # low GMP but 3× bids: not both low
    ("buy", 1.5, None, None, "₹100–₹105", False),         # no GMP
    ("no_buy", 12.0, None, None, "₹100–₹105", False),     # no GMP
    ("no_buy", 12.0, None, 12.6, "to be announced", False),  # no band
])
def test_devils_advocate_when_the_market_disagrees_with_the_call(config, call, total, retail, gmp,
                                                                                   band, devil):
    assert devils_advocate(call, sentiment(total, gmp, retail), band, config) is devil


@pytest.mark.advisor
@pytest.mark.demand_check
def test_each_reason_of_the_call_is_re_tested_and_shown_against_the_market(adapters, config):
    made_up = HypothesisCheck(reason_id="R:reason[0]", holds=True, why="Issue size: 99 times demand", evidence=["S:subscription"])
    stray = HypothesisCheck(reason_id="R:reason[7]", holds=True, why="Not a reason of the call", evidence=["S:subscription"])
    llm, close = close_day(adapters, config, DEVIL.model_copy(update={"hypotheses": [made_up, stray, *DEVIL.hypotheses]}),
                           hot_market())

    lines = close.splitlines()
    i = lines.index("Our reasons vs the market:")
    assert lines[i + 1:i + 3] == ["✅ Holds: Issue size: institutions bid 140.2 times, as expected",
                                  "⚠️ Weakened: Opacity: big institutions bid heavily anyway, so they may know more"]
    assert not any("99" in line or "Not a reason" in line for line in lines)
    saved = CheckedDemand.model_validate_json(
        (Path(config.work_dir) / ACME / "demand_check_2026-10-16.json").read_text(encoding="utf-8"))
    assert [h.reason for h in saved.hypotheses] == [WAIT.reasons[0].text, WAIT.reasons[1].text]


@pytest.mark.advisor
@pytest.mark.demand_check
def test_high_demand_against_a_no_buy_at_analysis_is_explained_in_the_summary_and_the_report(adapters, config, monkeypatch):
    adapters.exchange = FakeExchange([closing_ipo().model_copy(update={"close_date": date(2026, 10, 20)})])
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: WAIT, DemandCheck: DEVIL}, models=config.models)
    adapters.market = hot_market()
    html = []
    monkeypatch.setattr(analysis, "render_pdf", lambda report, out: html.append(render_html(report)) or _pdf(out))
    run_daily(OPEN, adapters, config)

    [summary] = [m for m in adapters.notifier.messages if m.startswith("📄")]
    assert "🔥 Why demand is high despite the NO BUY (the market as devil's advocate):" in summary
    assert "⚠️ Weakened: Opacity: big institutions bid heavily anyway, so they may know more" in summary
    assert [c["task"] for c in llm.calls].count("demand_check") == 1
    assert "Why demand is high despite the NO BUY: the market as devil's advocate" in html[0]
    assert "A ₹1,250 crore issue drew 62.4 times demand" in html[0] and "Weakened:" in html[0]


@pytest.mark.advisor
@pytest.mark.demand_check
@pytest.mark.parametrize("answer, analysis_market", [(WAIT, quiet_market(OPEN)), (GO, hot_market())])
def test_no_check_at_analysis_when_the_market_agrees_with_the_call(adapters, config, answer, analysis_market):
    adapters.exchange = FakeExchange([closing_ipo().model_copy(update={"close_date": date(2026, 10, 20)})])
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: answer, DemandCheck: DEVIL}, models=config.models)
    adapters.market = analysis_market
    run_daily(OPEN, adapters, config)

    assert "demand_check" not in [c["task"] for c in llm.calls]
    assert not any("🔥" in m or "🔎" in m for m in adapters.notifier.messages)


@pytest.mark.advisor
@pytest.mark.demand_check
@pytest.mark.parametrize("close_market, resent", [(hot_market(), True), (quiet_market(CLOSE), False)])
def test_high_demand_against_a_no_buy_on_the_closing_day_re_sends_the_report(adapters, config, monkeypatch, close_market, resent):
    monkeypatch.setattr(analysis, "render_pdf", lambda report, out: _pdf(out))
    close_day(adapters, config, DEVIL, close_market)

    sent_on_close = adapters.notifier.documents[1:]  # [0] is the analysis-day report
    assert len(sent_on_close) == (1 if resent else 0)


def _pdf(out_dir):
    path = Path(out_dir) / "report.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4")
    return path


@pytest.mark.advisor
@pytest.mark.demand_check
def test_low_demand_against_a_buy_at_analysis_is_explained_in_the_summary(adapters, config):
    adapters.exchange = FakeExchange([closing_ipo().model_copy(update={"close_date": date(2026, 10, 20)})])
    shunned = DemandCheck(demand_reasons=[EvidencedPoint(text="Bids are only 1.5 times the shares on offer",
                                                         evidence=["S:subscription"])],
                          retail_reading="", call_holds=True, verdict_vs_demand="",
                          hypotheses=[HypothesisCheck(reason_id="R:reason[0]", holds=True,
                                                      why="Issue size: still big enough to draw institutions later",
                                                      evidence=["F:offer_objects.issue_size"])])
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: GO, DemandCheck: shunned}, models=config.models)
    adapters.market = FakeMarketData(gmp_fact=claim(5.0, "GMP (unofficial)", OPEN), subscription_status=SubscriptionStatus(
        total=claim(1.5, "Subscription (total)", OPEN)))
    run_daily(OPEN, adapters, config)

    [summary] = [m for m in adapters.notifier.messages if m.startswith("📄")]
    lines = summary.splitlines()
    assert "🔥 Why demand is low despite the BUY (the market as devil's advocate):" in lines
    assert "• Bids are only 1.5 times the shares on offer" in lines
    assert "✅ Holds: Issue size: still big enough to draw institutions later" in lines
    assert "⚠️ LOW demand vs BUY: the call still holds." in lines
    assert [c["task"] for c in llm.calls].count("demand_check") == 1
