from datetime import date

from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.runner import run_daily
from tests.fakes import FakeExchange, ScriptedLLM, make_ipo

# make_ipo() opens 13 Oct and closes 15 Oct.
OPEN_DAY, DAY_BEFORE_CLOSE, CLOSE_DAY = date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15)


def offer_llm():
    return ScriptedLLM({OfferObjects: OfferObjects(issue_size_cr=Fact(
        value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))})


def reminders(notifier):
    return [m for m in notifier.messages if m.startswith("⏰")]


def test_analysed_ipo_gets_one_reminder_the_day_before_and_one_on_close_day(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm()

    run_daily(OPEN_DAY, adapters, config)
    assert reminders(adapters.notifier) == []

    run_daily(DAY_BEFORE_CLOSE, adapters, config)
    run_daily(DAY_BEFORE_CLOSE, adapters, config)
    assert reminders(adapters.notifier) == ["⏰ Acme Ltd IPO closes TOMORROW. Verdict was: N/A"]

    run_daily(CLOSE_DAY, adapters, config)
    run_daily(CLOSE_DAY, adapters, config)
    run_daily(date(2026, 10, 16), adapters, config)
    assert reminders(adapters.notifier) == [
        "⏰ Acme Ltd IPO closes TOMORROW. Verdict was: N/A",
        "⏰ Acme Ltd IPO closes TODAY. Verdict was: N/A",
    ]
    assert len(adapters.llm.calls) == 1


def test_ipo_analysed_the_day_before_close_gets_its_summary_not_a_reminder_that_day(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm()

    run_daily(DAY_BEFORE_CLOSE, adapters, config)
    assert len(adapters.notifier.messages) == 1
    assert reminders(adapters.notifier) == []

    run_daily(CLOSE_DAY, adapters, config)
    assert reminders(adapters.notifier) == ["⏰ Acme Ltd IPO closes TODAY. Verdict was: N/A"]
