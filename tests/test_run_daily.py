from datetime import date

from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.runner import run_daily
from tests.fakes import FakeExchange, ScriptedLLM
from tests.test_analyse import make_ipo

TODAY = date(2026, 10, 13)


def offer_llm():
    return ScriptedLLM({OfferObjects: OfferObjects(issue_size_cr=Fact(
        value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))})


def test_new_open_ipo_gets_a_summary_on_telegram(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm()

    summary = run_daily(TODAY, adapters, config)

    assert summary.analysed == ["ACME-2026-10-13"]
    [msg] = adapters.notifier.messages
    assert "Acme Ltd IPO (Mainboard)" in msg
    assert "Not investment advice" in msg


def test_ipo_is_analysed_only_once(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm()

    run_daily(TODAY, adapters, config)
    second = run_daily(TODAY, adapters, config)

    assert second.analysed == []
    assert second.skipped == ["ACME-2026-10-13"]
    assert len(adapters.notifier.messages) == 1
    assert len(adapters.llm.calls) == 1
