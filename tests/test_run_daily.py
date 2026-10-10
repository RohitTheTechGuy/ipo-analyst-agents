from datetime import date

from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.runner import run_daily
from tests.conftest import make_tiny_rhp
from tests.fakes import FakeDownloader, FakeExchange, ScriptedLLM, make_ipo, offer_objects

TODAY = date(2026, 10, 13)


def offer_llm(config):
    return ScriptedLLM({OfferObjects: offer_objects(issue_size=Fact(
        value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))},
        models=config.models)


def test_new_open_ipo_gets_a_summary_on_telegram(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm(config)

    summary = run_daily(TODAY, adapters, config)

    assert summary.analysed == ["ACME-2026-10-13"]
    [msg] = adapters.notifier.messages
    assert "Acme Ltd IPO (Mainboard)" in msg
    assert "Not investment advice" in msg


def test_ipo_is_analysed_only_once(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm(config)

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

    adapters.llm = offer_llm(config)
    second = run_daily(TODAY, adapters, config)
    assert second.analysed == ["ACME-2026-10-13"]


def test_budget_stop_alerts_once_and_is_never_retried(adapters, config):
    # 130,000 max output tokens x $12/1M (terra) = $1.56 > $1.50: stopped before the first call.
    config = config.model_copy(update={"default_max_tokens": 130_000})
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm(config)

    first = run_daily(TODAY, adapters, config)
    second = run_daily(TODAY, adapters, config)

    assert first.failed == ["ACME-2026-10-13"]
    assert adapters.notifier.messages == ["⚠️ Acme Ltd: analysis stopped at the $1.50 budget cap"]
    assert second.skipped == ["ACME-2026-10-13"]
    assert adapters.llm.calls == []


def test_telegram_failure_does_not_stop_other_ipos(adapters, config):
    other = make_ipo().model_copy(update={"ipo_id": "BETA-2026-10-13", "name": "Beta Ltd"})
    adapters.exchange = FakeExchange([make_ipo(), other])
    adapters.llm = offer_llm(config)
    adapters.notifier = FlakyNotifier()

    summary = run_daily(TODAY, adapters, config)

    assert summary.analysed == ["ACME-2026-10-13", "BETA-2026-10-13"]
    assert summary.notify_failed == ["ACME-2026-10-13"]
    assert "Beta Ltd IPO (Mainboard)" in adapters.notifier.messages[-1]


def test_rendered_report_pdf_follows_the_summary(adapters, config, rendered_pdf):
    pdf = rendered_pdf
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm(config)

    run_daily(TODAY, adapters, config)

    assert len(adapters.notifier.messages) == 1
    assert adapters.notifier.documents == [pdf]


def test_summary_shows_the_issue_size_and_ofs_shares_with_their_pages(adapters, config, tmp_path):
    offer_page = "THE OFFER\nTotal issue size of up to Rs. 1,250.00 crore\nOffer for Sale of up to 84,941,997 equity shares"
    adapters.downloader = FakeDownloader(make_tiny_rhp(tmp_path / "offer_rhp.pdf", (offer_page,)))
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: offer_objects(
        issue_size=Fact(value=1250.0, unit="₹ crore", page=2,
                        verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"),
        ofs_shares=Fact(value=84941997, unit="equity shares", page=2,
                        verbatim_quote="Offer for Sale of up to 84,941,997 equity shares"))}, models=config.models)

    run_daily(TODAY, adapters, config)

    [msg] = adapters.notifier.messages
    assert "Issue: ₹1,250 crore (p. 2) · OFS: 84,941,997 equity shares (p. 2)" in msg


def test_summary_shows_undisclosed_and_unverified_facts_as_such(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: offer_objects(
        # Not on page 2 (the tiny RHP's offer page has no OFS line): could not verify.
        ofs_shares=Fact(value=84941997, unit="equity shares", page=2,
                        verbatim_quote="Offer for Sale of up to 84,941,997 equity shares"))}, models=config.models)

    run_daily(TODAY, adapters, config)

    [msg] = adapters.notifier.messages
    assert "Issue: not disclosed · OFS: could not verify" in msg
