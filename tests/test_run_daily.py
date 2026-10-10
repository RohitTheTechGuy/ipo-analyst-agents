from datetime import date

from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.runner import run_daily
from tests.fakes import FakeExchange, ScriptedLLM
from tests.fakes import make_ipo

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


class BrokenLLM:
    def invoke_structured(self, **kwargs):
        raise RuntimeError("provider down")


class FlakyNotifier:
    def __init__(self):
        self.messages = []

    def send_message(self, text):
        if not self.messages:
            self.messages.append(None)
            raise RuntimeError("telegram down")
        self.messages.append(text)

    def send_document(self, path, caption=""):
        pass


def test_failed_analysis_sends_nothing_and_is_retried_next_run(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = BrokenLLM()

    first = run_daily(TODAY, adapters, config)
    assert first.failed == ["ACME-2026-10-13"]
    assert adapters.notifier.messages == []

    adapters.llm = offer_llm()
    second = run_daily(TODAY, adapters, config)
    assert second.analysed == ["ACME-2026-10-13"]


def test_telegram_failure_does_not_stop_other_ipos(adapters, config):
    other = make_ipo().model_copy(update={"ipo_id": "BETA-2026-10-13", "name": "Beta Ltd"})
    adapters.exchange = FakeExchange([make_ipo(), other])
    adapters.llm = offer_llm()
    adapters.notifier = FlakyNotifier()

    summary = run_daily(TODAY, adapters, config)

    assert summary.analysed == ["ACME-2026-10-13", "BETA-2026-10-13"]
    assert summary.notify_failed == ["ACME-2026-10-13"]
    assert "Beta Ltd IPO (Mainboard)" in adapters.notifier.messages[-1]
