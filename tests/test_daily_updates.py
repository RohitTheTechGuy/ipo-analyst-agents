"""Daily GMP + subscription updates for analysed IPOs until they close (seam A, no model calls)."""
import sqlite3
from datetime import date

from ipo_agents.contracts import ClaimedFact, Fact, OfferObjects, SubscriptionStatus
from ipo_agents.runner import run_daily
from tests.fakes import FakeExchange, FakeMarketData, ScriptedLLM, make_ipo, offer_objects

# Open Tue 13 Oct, close Fri 16 Oct: 14th is a mid day, 15th the day before close, 16th the close day.
OPEN, MID, DAY_BEFORE, CLOSE, AFTER = (date(2026, 10, d) for d in (13, 14, 15, 16, 17))


def ipo():
    return make_ipo().model_copy(update={"close_date": CLOSE})


def offer_llm(config):
    issue = Fact(value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore")
    return ScriptedLLM({OfferObjects: offer_objects(issue_size=issue)}, models=config.models)


def claim(value, label, as_of):
    return ClaimedFact(value=value, unit="×" if "Subscription" in label else "₹", source_url="https://gmp.example",
                       source_name="InvestorGain", as_of=as_of, label=label)


def market(day):
    return FakeMarketData(
        gmp_fact=claim(42.0, "GMP (unofficial)", day),
        subscription_status=SubscriptionStatus(
            qib=claim(4.1, "Subscription (QIB)", day), nii=claim(1.83, "Subscription (NII)", day),
            retail=claim(1.2, "Subscription (Retail)", day), total=claim(2.3, "Subscription (total)", day)))


def updates(notifier):
    return [m for m in notifier.messages if m.startswith(("📊", "⏰"))]


def test_analysed_ipo_gets_one_gmp_and_subscription_update_each_day_until_it_closes(adapters, config):
    adapters.exchange = FakeExchange([ipo()])
    adapters.llm = offer_llm(config)

    run_daily(OPEN, adapters, config)  # analysis day: the summary is the day's message
    assert updates(adapters.notifier) == []

    for day in (MID, MID, DAY_BEFORE, CLOSE, CLOSE, AFTER):
        adapters.market = market(day)
        run_daily(day, adapters, config)

    mid, before, close = updates(adapters.notifier)
    assert mid.splitlines() == [
        "📊 Acme Ltd IPO update · closes Fri 16 Oct",
        "Verdict was: N/A",
        "GMP ₹42 (unofficial, InvestorGain, as of 14 Oct)",
        "Subscription: 2.3× total (QIB 4.1×, NII 1.8×, Retail 1.2×) as of 14 Oct",
    ]
    assert before.splitlines()[0] == "⏰ Acme Ltd IPO closes TOMORROW"
    assert "as of 15 Oct" in before.splitlines()[2]
    assert close.splitlines()[0] == "⏰ Acme Ltd IPO closes TODAY"
    assert len(adapters.llm.calls) == 1  # updates never call a model


def test_update_says_not_available_when_market_data_is_missing_or_failing(adapters, config):
    adapters.exchange = FakeExchange([ipo()])
    adapters.llm = offer_llm(config)
    run_daily(OPEN, adapters, config)

    adapters.market = FakeMarketData(error=RuntimeError("investorgain down"))
    run_daily(MID, adapters, config)

    [update] = updates(adapters.notifier)
    assert update.splitlines()[2:] == ["GMP: not available", "Subscription: not available yet"]


def test_update_without_a_market_source_still_reminds_on_the_closing_days(adapters, config):
    adapters.exchange = FakeExchange([ipo()])
    adapters.llm = offer_llm(config)
    run_daily(OPEN, adapters, config)

    run_daily(CLOSE, adapters, config)

    [update] = updates(adapters.notifier)
    assert update.splitlines()[0] == "⏰ Acme Ltd IPO closes TODAY"
    assert "GMP: not available" in update.splitlines()


def test_an_ipo_analysed_before_the_symbol_was_stored_still_gets_updates(adapters, config):
    # A store created by an earlier version has no `symbol` column; its rows must keep working.
    db = sqlite3.connect(config.db_path)
    db.executescript("""
        CREATE TABLE ipos (ipo_id TEXT PRIMARY KEY, name TEXT, open_date DATE, close_date DATE,
          price_band TEXT, lot_size INT, rhp_url TEXT, rhp_sha256 TEXT, status TEXT NOT NULL,
          verdict TEXT, report_path TEXT, analysed_at TIMESTAMP, last_reminded_at TIMESTAMP);
        INSERT INTO ipos (ipo_id, name, open_date, close_date, price_band, lot_size, status)
          VALUES ('ACME-2026-10-13', 'Acme Ltd', '2026-10-13', '2026-10-16', '₹100–₹105', 142, 'done');
    """)
    db.close()
    seen = []

    class RecordingMarket(FakeMarketData):
        def gmp(self, ipo):
            seen.append(ipo.symbol)
            return None

    adapters.market = RecordingMarket()
    run_daily(MID, adapters, config)

    assert seen == ["ACME"]
    assert len(updates(adapters.notifier)) == 1


def test_after_close_an_update_goes_out_each_weekday_until_the_expected_listing_day(adapters, config):
    # Closes Fri 16 Oct: T+3 working days is Wed 21 Oct; Sat 17 and Thu 22 send nothing.
    adapters.exchange = FakeExchange([ipo()])
    adapters.llm = offer_llm(config)
    run_daily(OPEN, adapters, config)
    adapters.exchange = FakeExchange([])
    for d in (17, 19, 19, 20, 21, 22):
        adapters.market = market(date(2026, 10, d))
        run_daily(date(2026, 10, d), adapters, config)

    mon, tue, wed = [m for m in adapters.notifier.messages if m.startswith(("📈", "🔔"))]
    assert mon.splitlines() == [
        "📈 Acme Ltd IPO · bidding closed Fri 16 Oct, expected to list Wed 21 Oct",
        "Verdict was: N/A",
        "GMP ₹42 (unofficial, InvestorGain, as of 19 Oct)",
        "Final subscription: 2.3× total (QIB 4.1×, NII 1.8×, Retail 1.2×) as of 19 Oct",
    ]
    assert tue.startswith("📈") and wed.splitlines()[0] == "🔔 Acme Ltd is expected to list TODAY"
    assert len(adapters.llm.calls) == 1  # post-close updates never call a model


def test_expected_listing_skips_weekends():
    from ipo_agents.store import expected_listing
    assert expected_listing(date(2026, 10, 16)) == date(2026, 10, 21)  # Fri -> Wed
    assert expected_listing(date(2026, 10, 19)) == date(2026, 10, 22)  # Mon -> Thu
    assert expected_listing(date(2026, 10, 21)) == date(2026, 10, 26)  # Wed -> Mon
