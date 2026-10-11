"""Financial Advisor (ticket 27): BUY / NO BUY on its own ledger, at seams B (analyse_rhp) and A (run_daily).

Prices from config.yaml, USD per 1M tokens: sol in 2.00 / out 10.00; terra 2.00 / 12.00; luna 0.20 / 1.20;
groq 0. Hand-worked costs used throughout:
  offer read on terra, 1,000 in + 200 out:   1,000 x 2 + 200 x 12 = 4,400 / 1M  = $0.0044
  offer read on luna,  1,000 in + 200 out:   1,000 x 0.2 + 200 x 1.2 = 440 / 1M = $0.00044
  advisor on sol,     10,000 in + 2,000 out: 10,000 x 2 + 2,000 x 10 = 40,000 / 1M = $0.04
  the advisor's worst case, reserved before the call: 12,000 out x $10/1M = $0.12, plus its prompt.
The tiny RHP's offer page says the issue is ₹1,250 crore (page 2); make_ipo() is band ₹100–₹105, lot 142,
open 13 Oct.
"""
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from ipo_agents.advisor import ledger_id
from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetMeter
from ipo_agents.contracts import Advice, CheckedAdvice, EvidencedPoint, OfferObjects, Report, SubscriptionStatus, Synthesis
from ipo_agents.runner import run_daily
from ipo_agents.sentiment import ClaimedNumber, SentimentPoints, StanceTag
from ipo_agents.store import Store
from tests.conftest import pdf_text
from tests.fakes import FakeExchange, FakeMarketData, FakeSearch, ScriptedLLM, make_ipo, offer_objects
from tests.test_daily_updates import claim, market
from tests.test_sentiment import hit
from tests.test_synthesizer import ISSUE, OFFER, OFFER_ANSWER, crore, synthesis

ACME = "ACME-2026-10-13"
OPEN, MID, CLOSE = date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 16)

ISSUE_POINT = EvidencedPoint(text="A ₹1,250 crore issue is big enough to draw institutions",
                             evidence=["F:offer_objects.issue_size"])
LOT_POINT = EvidencedPoint(text="At the ₹105 upper band one lot costs ₹14,910", evidence=["K:context"])
ANALYST_POINT = EvidencedPoint(text="The analyst view is to avoid", evidence=["A:verdict"])
OPAQUE = EvidencedPoint(text="Most offer details are not disclosed", evidence=["N:offer_objects"])
GMP_POINTS = [EvidencedPoint(text="A GMP of ₹40 signals demand", evidence=["S:gmp"]),
              EvidencedPoint(text="The grey market is strong", evidence=["S:gmp"])]


def advice(call="buy", conviction="high", horizon="listing_gains", reasons=(ISSUE_POINT, LOT_POINT),
           risks=(OPAQUE,), headline="Apply for listing gains", change="A weak subscription would change it"):
    return Advice(call=call, conviction=conviction, horizon=horizon, headline=headline, reasons=list(reasons),
                  key_risks=list(risks), what_would_change_my_mind=change)


def advisor_calls(llm):
    return [c for c in llm.calls if c["task"] == "advisor"]


def advisor_items(llm, n=0):
    return {i["id"]: i for i in json.loads(advisor_calls(llm)[n]["user"].split("\n", 1)[1])["items"]}


def spent(config, ledger, cap=None):
    return BudgetMeter(ledger, config, cap=cap).spent


def seed(config, ledger, model, input_tokens, cap=None):
    """Logs one past call of `input_tokens` on `model` against `ledger`."""
    meter = BudgetMeter(ledger, config, cap=cap)
    meter.settle(meter.reserve(model, 0, 1), task="seed", model=model,
                 input_tokens=input_tokens, cached_tokens=0, output_tokens=0)


def logged_ledgers(config):
    with closing(sqlite3.connect(config.db_path)) as db:
        return db.execute("SELECT task, ipo_id FROM llm_calls WHERE task != 'seed' ORDER BY id").fetchall()


# --- seam B: routing and the separate ledger --------------------------------------------------

@pytest.mark.advisor
def test_the_advisor_runs_on_sol_and_its_spend_lands_on_its_own_ledger(tiny_rhp, adapters, config):
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: advice()}, models=config.models,
                                     usage=[(1_000, 0, 200), (10_000, 0, 2_000)])

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config, today=OPEN)

    [call] = advisor_calls(llm)
    assert (call["model"], call["max_tokens"]) == ("sol", 12_000)
    assert llm.calls[-1] is call  # after the analysis
    assert logged_ledgers(config) == [("offer_objects", ACME), ("advisor", f"{ACME}:advisor")]
    assert spent(config, ACME) == pytest.approx(0.0044)  # the offer read only
    assert spent(config, ledger_id(ACME), cap=0.50) == pytest.approx(0.04)  # the advisor only
    assert report.advice.call == "buy" and report.advice.conviction == "high"
    assert report.advice.as_of == OPEN
    assert report.advice.reasons == [ISSUE_POINT, LOT_POINT]


@pytest.mark.advisor
def test_analysis_spend_never_counts_against_the_advisor(tiny_rhp, adapters, config):
    # The analysis ledger already holds $1.40 (700,000 in x $2/1M on terra): past 70% of $1.50, so the
    # offer read goes to luna ($0.00044). On a shared ledger the advisor's worst case ($0.12 + prompt)
    # would take it to over $1.52 and be refused; on its own ledger it is $0.12 of $0.50 and goes ahead.
    seed(config, ACME, "terra", 700_000)
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: advice()}, models=config.models,
                                     usage=[(1_000, 0, 200), (10_000, 0, 2_000)])

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config, today=OPEN)

    assert [(c["task"], c["model"]) for c in llm.calls] == [("offer_objects", "luna"), ("advisor", "sol")]
    assert report.advice is not None
    assert spent(config, ACME) == pytest.approx(1.40044)  # $1.40 + $0.00044
    assert spent(config, ledger_id(ACME), cap=0.50) == pytest.approx(0.04)


@pytest.mark.advisor
def test_advisor_spend_never_counts_against_the_analysis_and_its_cap_stops_only_the_advice(
        tiny_rhp, adapters, config):
    # The advisor ledger already holds $0.45 (225,000 in x $2/1M on sol): its worst case ($0.12 + prompt)
    # would take it past its $0.50 cap, so the call is never sent. The analysis ledger starts at $0, so
    # the offer read runs on terra, undegraded, and costs exactly $0.0044.
    seed(config, ledger_id(ACME), "sol", 225_000, cap=0.50)
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: advice()}, models=config.models,
                                     usage=[(1_000, 0, 200)])

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config, today=OPEN)

    assert [(c["task"], c["model"]) for c in llm.calls] == [("offer_objects", "terra")]
    assert report.status == "complete" and report.advice is None
    assert report.offer.issue_size.value == 1250.0
    assert spent(config, ACME) == pytest.approx(0.0044)
    assert spent(config, ledger_id(ACME), cap=0.50) == pytest.approx(0.45)  # nothing billed


# --- seam B: the digest and Python's checks ---------------------------------------------------

ARTICLE = "Acme Ltd IPO review. GMP of Rs 40 today. At 45 times earnings the valuation looks rich."


@pytest.mark.synthesizer
@pytest.mark.advisor
def test_the_advisor_sees_only_verified_values_the_analyst_view_and_context(tiny_rhp, adapters, config):
    # The fresh issue cites a ₹999 crore quote that isn't on page 2: it fails verification. The article's
    # GMP of 40 is grounded (its quote is in the article); its "45 times earnings" is a paraphrase no
    # checked number backs, so that reason and the bear point carrying it never reach the advisor.
    wrong = "Fresh issue of up to Rs. 999.00 crore"
    answer = offer_objects(issue_size=crore(1250.0, ISSUE, OFFER), fresh_issue=crore(999.0, wrong, OFFER))
    adapters.search = FakeSearch({"Acme Ltd IPO review": [hit("a", date(2026, 10, 10), content=ARTICLE)]})
    tag = StanceTag(stance="subscribe", key_reasons=["GMP of ₹40 shows demand", "Rich at 45 times earnings",
                                                     "Strong brand"],
                    claimed_numbers=[ClaimedNumber(label="GMP", value=40, unit="₹", quote="GMP of Rs 40")])
    points = SentimentPoints(bull_points=["GMP of ₹40"], bear_points=["Rich at 45 times earnings"])
    adapters.llm = llm = ScriptedLLM({OfferObjects: answer, StanceTag: tag, SentimentPoints: points,
                                      Synthesis: synthesis(), Advice: advice()}, models=config.models)

    analyse_rhp(make_ipo(), tiny_rhp, adapters, config, today=OPEN)

    items = advisor_items(llm)
    prompt = advisor_calls(llm)[0]["user"]
    assert "999" not in prompt and wrong not in prompt
    assert items["F:offer_objects.issue_size"]["value"] == 1250.0
    assert "fresh_issue" in items["N:offer_objects"]["could_not_verify"]
    assert items["A:verdict"] == {"id": "A:verdict", "verdict": "avoid", "confidence": "medium"}
    assert items["A:reason[0]"]["text"] == "The issue is ₹1,250 crore"
    # Context: 13 Oct to the 15 Oct close is 2 days; one lot is 142 x ₹105 = ₹14,910.
    assert items["K:context"] == {"id": "K:context", "today": "2026-10-13", "days_to_close": 2, "upper_band": 105.0,
                                  "lot_size": 142, "min_investment": 14_910.0, "unit": "₹"}
    assert items["S:source[0]"]["reasons"] == ["GMP of ₹40 shows demand", "Strong brand"]
    assert items["S:source[0]"]["published"] == "2026-10-10"
    assert items["S:points"] == {"id": "S:points", "bull": ["GMP of ₹40"], "bear": []}
    assert "45" not in prompt


@pytest.mark.advisor
def test_points_citing_an_unknown_id_or_a_number_not_in_their_evidence_are_dropped(tiny_rhp, adapters, config):
    invented_id = EvidencedPoint(text="The company is debt-free", evidence=["F:financials.periods[0].total_borrowings"])
    invented_number = EvidencedPoint(text="The issue is ₹1,500 crore", evidence=["F:offer_objects.issue_size"])
    bad_risk = EvidencedPoint(text="Promoters sell 80% of their stake", evidence=["N:offer_objects"])
    answer = advice(call="no_buy", conviction="medium", horizon="listing_gains",
                    reasons=[ISSUE_POINT, invented_id, invented_number, LOT_POINT], risks=[bad_risk, OPAQUE],
                    headline="Skip it: the ₹2,000 crore valuation is rich")
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: answer}, models=config.models)

    a = analyse_rhp(make_ipo(), tiny_rhp, adapters, config, today=OPEN).advice

    assert a.call == "no_buy" and a.conviction == "medium"
    assert a.horizon == "none"  # a no_buy has no horizon
    assert a.reasons == [ISSUE_POINT, LOT_POINT]
    assert a.key_risks == [OPAQUE]
    assert a.headline == ""  # ₹2,000 crore is nowhere in the digest
    assert a.what_would_change_my_mind == "A weak subscription would change it"
    assert len(a.notes) == 4
    assert any("F:financials.periods[0].total_borrowings" in n for n in a.notes)
    assert any("1,500" in n for n in a.notes) and any("80" in n for n in a.notes)
    assert any("2,000" in n and "headline" in n for n in a.notes)


@pytest.mark.advisor
def test_a_buy_resting_only_on_gmp_and_context_is_downgraded_to_low_conviction(tiny_rhp, adapters, config):
    adapters.market = FakeMarketData(gmp_fact=claim(40.0, "GMP (unofficial)", date(2026, 10, 12)))
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: advice(reasons=[*GMP_POINTS, LOT_POINT])},
                               models=config.models)

    a = analyse_rhp(make_ipo(), tiny_rhp, adapters, config, today=OPEN).advice

    assert a.call == "buy" and a.conviction == "low"
    assert len(a.reasons) == 3
    assert any("set to low" in n and "GMP" in n for n in a.notes)


@pytest.mark.advisor
def test_a_buy_with_an_rhp_reason_beside_gmp_keeps_its_conviction(tiny_rhp, adapters, config):
    adapters.market = FakeMarketData(gmp_fact=claim(40.0, "GMP (unofficial)", date(2026, 10, 12)))
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: advice(reasons=[GMP_POINTS[0], ISSUE_POINT])},
                               models=config.models)

    a = analyse_rhp(make_ipo(), tiny_rhp, adapters, config, today=OPEN).advice

    assert a.conviction == "high" and a.notes == []


@pytest.mark.advisor
def test_fewer_than_two_surviving_reasons_give_no_advice(tiny_rhp, adapters, config):
    invented = EvidencedPoint(text="Margins of 40%", evidence=["F:offer_objects.issue_size"])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: advice(reasons=[ISSUE_POINT, invented])},
                               models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config, today=OPEN)

    assert report.status == "complete" and report.advice is None


@pytest.mark.advisor
def test_an_advisor_crash_gives_no_advice_and_keeps_the_analysis(tiny_rhp, adapters, config):
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: advice()}, models=config.models,
                                     errors=[None, ConnectionError("connection dropped mid-call")])

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config, today=OPEN)

    assert [c["task"] for c in llm.calls] == ["offer_objects", "advisor"]
    assert report.status == "complete" and report.advice is None
    assert report.offer.issue_size.value == 1250.0


# --- seam A: the summary, report.json and the closing-day refresh ------------------------------

@pytest.mark.synthesizer
@pytest.mark.advisor
def test_the_summary_leads_with_the_advisor_and_keeps_the_analyst_view(adapters, config):
    reasons = [ISSUE_POINT, LOT_POINT, ANALYST_POINT,
               EvidencedPoint(text="A fourth reason the summary leaves out", evidence=["E:ipo"])]
    risks = [OPAQUE, EvidencedPoint(text="Only the issue size is verified", evidence=["F:offer_objects.issue_size"]),
             EvidencedPoint(text="A third risk the summary leaves out", evidence=["E:ipo"])]
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: synthesis(),
                                Advice: advice(reasons=reasons, risks=risks)}, models=config.models)

    run_daily(OPEN, adapters, config)

    [msg] = adapters.notifier.messages
    assert msg.startswith("\n".join([
        "📄 Acme Ltd IPO (Mainboard)",
        "Open: 13 Oct–15 Oct · Band ₹100–₹105 · Lot 142",
        "",
        "🧭 Advisor: BUY · conviction high · listing gains",
        "Apply for listing gains",
        "Reasons:",
        "• A ₹1,250 crore issue is big enough to draw institutions",
        "• At the ₹105 upper band one lot costs ₹14,910",
        "• The analyst view is to avoid",
        "Risks:",
        "• Most offer details are not disclosed",
        "• Only the issue size is verified",
        "",
        "Issue: ₹1,250 crore (p. 2)",
    ]))
    assert "left out" not in msg.split("Analyst view:")[0]
    assert "Analyst view: AVOID (confidence: medium)" in msg
    assert msg.endswith("Not investment advice; generated from RHP + public sources.")


@pytest.mark.advisor
def test_the_report_is_saved_as_json_and_its_path_stored(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: advice()}, models=config.models)

    run_daily(OPEN, adapters, config)

    store = Store(config.db_path)
    path = store.report_path(ACME)
    store.close()
    assert path == str(Path(config.work_dir) / ACME / "report.json")
    saved = Report.model_validate_json(Path(path).read_text(encoding="utf-8"))
    assert saved.offer.issue_size.value == 1250.0
    assert saved.advice.call == "buy"


@pytest.mark.advisor
@pytest.mark.renderer
def test_an_advisor_cap_stop_still_sends_the_summary_and_the_pdf(adapters, config):
    seed(config, ledger_id(ACME), "sol", 225_000, cap=0.50)  # $0.45 of $0.50: the $0.12 worst case won't fit
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Advice: advice()}, models=config.models)

    summary = run_daily(OPEN, adapters, config)

    assert summary.analysed == [ACME] and summary.notify_failed == []
    assert advisor_calls(llm) == []
    [msg] = adapters.notifier.messages
    assert "🧭 Advisor: advice not reached" in msg
    assert "Issue: ₹1,250 crore (p. 2)" in msg
    [pdf] = adapters.notifier.documents
    page_one = pdf_text(pdf).split("2 Offer structure")[0]
    assert "FINANCIAL ADVISOR ADVICE NOT REACHED" in page_one
    assert "₹1,250 cr (p. 2)" in page_one


WAIT = advice(call="no_buy", conviction="medium", horizon="none", reasons=[ISSUE_POINT, OPAQUE],
              headline="Wait: too little is verified")
GO = advice(call="buy", conviction="medium", horizon="listing_gains", reasons=[ISSUE_POINT, LOT_POINT],
            headline="Apply: demand has built up")


def by_day(analysis_day: Advice, close_day):
    """The advisor's answer, by the day in its K:context; `close_day` may be an exception to raise."""
    return lambda user: close_day if '"today":"2026-10-16"' in user else analysis_day


def closing_ipo():
    return make_ipo().model_copy(update={"close_date": CLOSE})


@pytest.mark.advisor
def test_on_the_closing_day_the_advice_is_refreshed_from_report_json_and_fresh_sentiment(adapters, config):
    adapters.exchange = FakeExchange([closing_ipo()])
    # Calls: 0 offer read (terra), 1 advisor (analysis day), then on the closing day 2 stance tag (groq, $0),
    # 3 bull/bear points (luna), 4 advisor.
    adapters.llm = llm = ScriptedLLM(
        {OfferObjects: OFFER_ANSWER, Advice: by_day(WAIT, GO),
         StanceTag: StanceTag(stance="subscribe", key_reasons=["Strong demand"], claimed_numbers=[]),
         SentimentPoints: SentimentPoints(bull_points=["Strong demand"], bear_points=[])},
        models=config.models,
        usage=[(1_000, 0, 200), (10_000, 0, 2_000), (1_000, 0, 200), (1_000, 0, 200), (10_000, 0, 2_000)])
    adapters.market = market(OPEN)
    run_daily(OPEN, adapters, config)
    adapters.market = market(MID)
    run_daily(MID, adapters, config)  # a plain update: the analysis-day call, no model call
    assert len(llm.calls) == 2

    adapters.exchange = FakeExchange([])
    adapters.search = FakeSearch({"Acme Ltd IPO review": [hit("late", date(2026, 10, 15))]})
    adapters.market = replace(market(CLOSE), gmp_fact=claim(55.0, "GMP (unofficial)", CLOSE))
    run_daily(CLOSE, adapters, config)

    *_, mid, close = adapters.notifier.messages
    assert mid.splitlines()[:3] == ["📊 Acme Ltd IPO update · closes Fri 16 Oct",
                                    "🧭 Advisor (13 Oct): NO BUY · conviction medium", "Wait: too little is verified"]
    assert close.splitlines() == [
        "⏰ Acme Ltd IPO closes TODAY",
        "🧭 Advisor (refreshed today): BUY · conviction medium · listing gains",
        "Change: was NO BUY → now BUY",
        "Apply: demand has built up",
        "Verdict was: N/A",
        "GMP ₹55 (unofficial, InvestorGain, as of 16 Oct)",
        "Subscription: 2.3× total (QIB 4.1×, NII 1.8×, Retail 1.2×) as of 16 Oct",
    ]
    assert [c["task"] for c in llm.calls[2:]] == ["sentiment_stance_tag", "sentiment_agent", "advisor"]
    # The refresh read the analysis from report.json (no section was re-read) and today's market.
    items = advisor_items(llm, 1)
    assert items["F:offer_objects.issue_size"]["value"] == 1250.0
    assert items["S:gmp"]["value"] == 55.0 and items["S:source[0]"]["published"] == "2026-10-15"
    saved = CheckedAdvice.model_validate_json(
        (Path(config.work_dir) / ACME / "advice_2026-10-16.json").read_text(encoding="utf-8"))
    assert saved.call == "buy" and saved.as_of == CLOSE
    # Everything the refresh spent is on the advisor ledger: $0.04 + $0 (groq) + $0.00044 (luna) + $0.04.
    assert spent(config, ACME) == pytest.approx(0.0044)
    assert spent(config, ledger_id(ACME), cap=0.50) == pytest.approx(0.08044)
    assert {ledger for task, ledger in logged_ledgers(config) if task != "offer_objects"} == {f"{ACME}:advisor"}


@pytest.mark.advisor
@pytest.mark.parametrize("failure", ["search crashes", "advisor call fails"])
def test_a_failed_refresh_still_sends_the_normal_update_with_the_analysis_day_advice(adapters, config, failure):
    adapters.exchange = FakeExchange([closing_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER,
                                Advice: by_day(WAIT, ConnectionError("connection dropped mid-call"))},
                               models=config.models)
    run_daily(OPEN, adapters, config)

    adapters.exchange = FakeExchange([])
    if failure == "search crashes":
        adapters.search = FakeSearch(error=RuntimeError("search provider bug"))  # not a handled search error
    adapters.market = market(CLOSE)
    run_daily(CLOSE, adapters, config)

    *_, close = adapters.notifier.messages
    assert close.splitlines() == [
        "⏰ Acme Ltd IPO closes TODAY",
        "🧭 Advisor (13 Oct): NO BUY · conviction medium",
        "Wait: too little is verified",
        "Verdict was: N/A",
        "GMP ₹42 (unofficial, InvestorGain, as of 16 Oct)",
        "Subscription: 2.3× total (QIB 4.1×, NII 1.8×, Retail 1.2×) as of 16 Oct",
    ]
    assert not (Path(config.work_dir) / ACME / "advice_2026-10-16.json").exists()


def test_without_a_saved_report_the_closing_day_update_is_unchanged(adapters, config):
    # An IPO analysed before report.json existed: no refresh, no advisor lines, the normal update.
    adapters.exchange = FakeExchange([closing_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER}, models=config.models)
    run_daily(OPEN, adapters, config)
    store = Store(config.db_path)
    store.set_report_path(ACME, None)
    store.close()

    adapters.exchange = FakeExchange([])
    adapters.market = market(CLOSE)
    run_daily(CLOSE, adapters, config)

    *_, close = adapters.notifier.messages
    assert close.splitlines()[:2] == ["⏰ Acme Ltd IPO closes TODAY", "Verdict was: N/A"]
