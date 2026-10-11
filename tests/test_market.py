"""GMP and subscription status (ticket 19): seam B with a fake market-data adapter, seam A for the
summary lines, and the real parsers replaying recorded HTTP fixtures.

Fixtures in tests/fixtures/market were fetched live on 2026-10-11 (a Sunday):
- investorgain_live_gmp.html: InvestorGain's live GMP report. HD Fire Protect (opens 13 Oct) shows
  GMP ₹75, updated 10-Oct 23:02; Orient Cables (closed 29 Sep) shows ₹110, updated 5-Oct 9:37.
- nse_ipo_active_category_*.json: NSE's consolidated bid details. ORIENTCABL is the final day-3
  book; HDFIRE has not opened, so every count is zero and the time is "null".
- chittorgarh_subscription_*.html: Chittorgarh's subscription pages for the same two IPOs.
- chittorgarh_mainboard_sep2026.json: Chittorgarh's September mainboard list (October's is the
  discovery fixture).
Expected values below are read by hand from those files.
"""
from datetime import date
from pathlib import Path

import httpx

from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import ClaimedFact, Ipo, OfferObjects, SubscriptionStatus
from ipo_agents.market import HttpMarketData
from ipo_agents.runner import run_daily
from tests.fakes import FakeExchange, FakeMarketData, ScriptedLLM, make_ipo
from tests.test_discovery import Web
from tests.test_sentiment import OFFER

FIXTURES = Path(__file__).parent / "fixtures"
IG_GMP = "https://www.investorgain.com/report/live-ipo-gmp/331/"
NSE_HOME = "https://www.nseindia.com/"
NSE_CAT = "https://www.nseindia.com/api/ipo-active-category?symbol={}"
CG_LIST = "https://webnodejs.chittorgarh.com/cloud/report/data-read/82/1/{}/2026/2026-27/0/mainboard/0?search="
CG_SUB_OC = "https://www.chittorgarh.com/ipo_subscription/orient-cables-ipo/2532/"
CG_SUB_HD = "https://www.chittorgarh.com/ipo_subscription/hd-fire-protect-ipo/2628/"

HD_FIRE = Ipo(ipo_id="HDFIRE-2026-10-13", name="HD Fire Protect Limited", symbol="HDFIRE",
              open_date=date(2026, 10, 13), close_date=date(2026, 10, 15), price_band="₹258–₹271",
              lot_size=55, rhp_url="https://example.test/rhp.pdf")
ORIENT = Ipo(ipo_id="ORIENTCABL-2026-09-25", name="Orient Cables (India) Limited", symbol="ORIENTCABL",
             open_date=date(2026, 9, 25), close_date=date(2026, 9, 29), price_band="₹258–₹272",
             lot_size=55, rhp_url="https://example.test/rhp.pdf")


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def claim(value, label, source="Example GMP", as_of=date(2026, 10, 12), unit="×"):
    return ClaimedFact(value=value, unit=unit, source_url="https://gmp.example/acme", source_name=source,
                       as_of=as_of, label=label)


def a_subscription():
    return SubscriptionStatus(qib=claim(4.1, "Subscription (QIB)"), nii=claim(1.83, "Subscription (NII)"),
                              retail=claim(1.2, "Subscription (Retail)"), total=claim(2.3, "Subscription (total)"))


def offer_only(config):
    return ScriptedLLM({OfferObjects: OFFER}, models=config.models)


# --- seam B: fake market data ---

def test_gmp_is_labelled_unofficial_with_its_source_and_date(tiny_rhp, adapters, config):
    # The adapter's own label is not trusted: the GMP is always marked unofficial.
    adapters.market = FakeMarketData(gmp_fact=claim(75.0, "GMP", unit="₹"))
    adapters.llm = offer_only(config)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    gmp = report.sentiment.gmp
    assert gmp.label == "GMP (unofficial)"
    assert (gmp.value, gmp.unit) == (75.0, "₹")
    assert (gmp.source_name, gmp.source_url, gmp.as_of) == ("Example GMP", "https://gmp.example/acme", date(2026, 10, 12))
    # Third-party numbers stay out of the RHP-verified figures.
    assert report.offer == OFFER


def test_subscription_appears_by_investor_category(tiny_rhp, adapters, config):
    adapters.market = FakeMarketData(subscription_status=a_subscription())
    adapters.llm = offer_only(config)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    sub = report.sentiment.subscription
    assert [(f.value, f.source_name, f.as_of) for f in (sub.qib, sub.nii, sub.retail, sub.total)] == [
        (4.1, "Example GMP", date(2026, 10, 12)), (1.83, "Example GMP", date(2026, 10, 12)),
        (1.2, "Example GMP", date(2026, 10, 12)), (2.3, "Example GMP", date(2026, 10, 12))]
    assert report.sentiment.gmp is None  # not available


def test_market_data_is_not_available_when_the_source_has_none(tiny_rhp, adapters, config):
    adapters.market = FakeMarketData()
    adapters.llm = offer_only(config)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.sentiment.gmp is None
    assert report.sentiment.subscription is None


def test_without_a_market_adapter_market_data_is_not_available(tiny_rhp, adapters, config):
    adapters.llm = offer_only(config)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.status == "complete"
    assert (report.sentiment.gmp, report.sentiment.subscription) == (None, None)


def test_a_failing_market_adapter_never_fails_the_run(tiny_rhp, adapters, config):
    adapters.market = FakeMarketData(error=RuntimeError("layout changed"))
    adapters.llm = offer_only(config)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.status == "complete"
    assert (report.sentiment.gmp, report.sentiment.subscription) == (None, None)


# --- seam A: the Telegram summary ---

def test_summary_appends_the_unofficial_gmp_and_a_subscription_line(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.market = FakeMarketData(gmp_fact=claim(75.0, "GMP", unit="₹"), subscription_status=a_subscription())
    adapters.llm = offer_only(config)

    run_daily(date(2026, 10, 13), adapters, config)

    [msg] = adapters.notifier.messages
    lines = msg.splitlines()
    assert "Sentiment: unavailable · GMP ₹75 (unofficial)" in lines
    assert "Subscription: 2.3× total (QIB 4.1×, NII 1.8×, Retail 1.2×)" in lines


def test_summary_has_no_gmp_or_subscription_when_not_available(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.market = FakeMarketData()
    adapters.llm = offer_only(config)

    run_daily(date(2026, 10, 13), adapters, config)

    [msg] = adapters.notifier.messages
    assert "Sentiment: unavailable" in msg.splitlines()
    assert "GMP ₹" not in msg and "Subscription:" not in msg
    assert "no GMP or subscription data yet" in msg


# --- real parsers, replaying the recorded responses ---

def real_market(routes: dict) -> tuple[HttpMarketData, Web]:
    web = Web(routes)
    return HttpMarketData(httpx.Client(transport=httpx.MockTransport(web))), web


def orient_routes() -> dict:
    return {
        IG_GMP: fixture("market/investorgain_live_gmp.html"),
        NSE_HOME: 403,  # as live: the homepage refuses, the API still answers
        NSE_CAT.format("ORIENTCABL"): fixture("market/nse_ipo_active_category_ORIENTCABL.json"),
        CG_LIST.format(9): fixture("market/chittorgarh_mainboard_sep2026.json"),
        CG_SUB_OC: fixture("market/chittorgarh_subscription_orient_cables.html"),
    }


def test_real_gmp_comes_from_investorgain_for_an_upcoming_ipo(tiny_rhp, adapters, config):
    adapters.market, _ = real_market({
        IG_GMP: fixture("market/investorgain_live_gmp.html"),
        NSE_CAT.format("HDFIRE"): fixture("market/nse_ipo_active_category_HDFIRE.json"),
        CG_LIST.format(10): fixture("discovery/chittorgarh_mainboard.json"),
        CG_SUB_HD: fixture("market/chittorgarh_subscription_hd_fire_protect.html"),
    })
    adapters.llm = offer_only(config)

    report = analyse_rhp(HD_FIRE, tiny_rhp, adapters, config)

    # Row "HD Fire Protect": GMP "&#8377;<b>75</b> (27.68%)", Updated-On "10-Oct 23:02".
    assert report.sentiment.gmp == ClaimedFact(value=75.0, unit="₹", source_url=IG_GMP, source_name="InvestorGain",
                                               as_of=date(2026, 10, 10), label="GMP (unofficial)")
    # Not open yet: NSE's book is all zeros and Chittorgarh has no subscription table.
    assert report.sentiment.subscription is None


def test_real_subscription_comes_from_nse_consolidated_bids(tiny_rhp, adapters, config):
    adapters.market, web = real_market(orient_routes())
    adapters.llm = offer_only(config)

    report = analyse_rhp(ORIENT, tiny_rhp, adapters, config)

    # "Updated as on 29-Sep-2026 19:00:00"; noOfTotalMeant for srNo 1, 2, 3 and Total.
    sub = report.sentiment.subscription
    assert [(f.label, f.value) for f in (sub.qib, sub.nii, sub.retail, sub.total)] == [
        ("Subscription (QIB)", 182.7624385117417), ("Subscription (NII)", 115.63008872334234),
        ("Subscription (Retail)", 30.547860736619388), ("Subscription (total)", 92.26964133657097)]
    assert {(f.unit, f.source_name, f.source_url, f.as_of) for f in (sub.qib, sub.nii, sub.retail, sub.total)} == {
        ("×", "NSE", NSE_CAT.format("ORIENTCABL"), date(2026, 9, 29))}
    # Row "Orient Cables": GMP ₹110, Updated-On "5-Oct 9:37".
    assert (report.sentiment.gmp.value, report.sentiment.gmp.as_of) == (110.0, date(2026, 10, 5))
    assert CG_SUB_OC not in web.requests  # the fallback is not needed


def test_real_subscription_falls_back_to_chittorgarh_when_nse_is_blocked(tiny_rhp, adapters, config):
    adapters.market, _ = real_market(orient_routes() | {NSE_CAT.format("ORIENTCABL"): 403})
    adapters.llm = offer_only(config)

    report = analyse_rhp(ORIENT, tiny_rhp, adapters, config)

    # The page's table: QIB (Ex Anchor) 192.68, NII 121.90, Retail 32.21, Total 97.28,
    # "subscribed 97.28 times on September 29, 2026 6:55:00 PM".
    sub = report.sentiment.subscription
    assert [f.value for f in (sub.qib, sub.nii, sub.retail, sub.total)] == [192.68, 121.90, 32.21, 97.28]
    assert {(f.source_name, f.source_url, f.as_of) for f in (sub.qib, sub.nii, sub.retail, sub.total)} == {
        ("Chittorgarh", CG_SUB_OC, date(2026, 9, 29))}


def test_real_sources_down_leave_market_data_not_available(tiny_rhp, adapters, config):
    adapters.market, _ = real_market({IG_GMP: 500, NSE_CAT.format("ORIENTCABL"): httpx.ConnectError,
                                      CG_LIST.format(9): 403})
    adapters.llm = offer_only(config)

    report = analyse_rhp(ORIENT, tiny_rhp, adapters, config)

    assert report.status == "complete"
    assert (report.sentiment.gmp, report.sentiment.subscription) == (None, None)


def test_summary_from_real_nse_figures(adapters, config):
    adapters.exchange = FakeExchange([ORIENT])
    adapters.market, _ = real_market(orient_routes())
    adapters.llm = offer_only(config)

    run_daily(date(2026, 9, 25), adapters, config)

    [msg] = adapters.notifier.messages
    lines = msg.splitlines()
    assert "Sentiment: unavailable · GMP ₹110 (unofficial)" in lines
    # 92.2696 / 182.7624 / 115.6301 / 30.5479, one decimal place.
    assert "Subscription: 92.3× total (QIB 182.8×, NII 115.6×, Retail 30.5×)" in lines
