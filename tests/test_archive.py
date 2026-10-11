"""Every report generated and every Telegram message sent is archived under reports/<ipo_id>/ (seam A)."""
from datetime import date
from pathlib import Path

from ipo_agents.contracts import Fact, OfferObjects, Report
from ipo_agents.runner import run_daily
from tests.fakes import FakeExchange, FakeMarketData, ScriptedLLM, make_ipo, offer_objects

OPEN, MID = date(2026, 10, 13), date(2026, 10, 14)
ISSUE = Fact(value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore")


def offer_llm(config):
    return ScriptedLLM({OfferObjects: offer_objects(issue_size=ISSUE)}, models=config.models)


def test_an_analysed_ipo_is_archived_with_its_report_and_the_exact_summary_sent(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm(config)

    run_daily(OPEN, adapters, config)

    [run_dir] = (Path(config.reports_dir) / "ACME-2026-10-13").glob("2026-10-13_*")
    [sent] = adapters.notifier.messages
    assert (run_dir / "telegram_summary.txt").read_text().rstrip("\n") == sent
    assert Report.model_validate_json((run_dir / "report.json").read_text()).offer.issue_size == ISSUE


def test_a_forced_re_run_is_archived_beside_the_first_not_over_it(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm(config)

    run_daily(OPEN, adapters, config)
    run_daily(OPEN, adapters, config, force_ipo="ACME")

    assert len(list((Path(config.reports_dir) / "ACME-2026-10-13").glob("2026-10-13_*"))) == 2


def test_each_daily_update_sent_is_archived(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm(config)
    run_daily(OPEN, adapters, config)

    adapters.market = FakeMarketData()
    run_daily(MID, adapters, config)

    update = (Path(config.reports_dir) / "ACME-2026-10-13" / "updates" / "2026-10-14.txt").read_text()
    assert update.rstrip("\n") == adapters.notifier.messages[-1]


def test_an_archive_that_cannot_be_written_never_stops_the_summary(adapters, config):
    blocker = Path(config.reports_dir)
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("a file where the reports folder should be")
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm(config)

    summary = run_daily(OPEN, adapters, config)

    assert summary.analysed == ["ACME-2026-10-13"]
    assert len(adapters.notifier.messages) == 1
