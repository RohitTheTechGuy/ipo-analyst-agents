"""The real peer-data adapter against recorded NSE and Yahoo Finance responses (spec story 34).

Fixtures in tests/fixtures/peers/ are real responses recorded on 11 Oct 2026 for three of Hyundai's RHP
peers: Maruti Suzuki, Mahindra & Mahindra and Tata Motors (now TMPV). NSE goes through httpx
MockTransport; yfinance sits behind the adapter's `yf_fetch` seam, replaced here by the recorded payloads.
Expected values are the raw recorded numbers converted by hand (₹1 crore = ₹10,000,000).
"""
import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from ipo_agents.peers import HttpPeerData

FIXTURES = Path(__file__).parent / "fixtures" / "peers"
SEARCHES = {"maruti suzuki india limited": "nse_search_maruti.json",
            "tata motors limited": "nse_search_tata_motors.json",
            "mahindra & mahindra limited": "nse_search_mahindra.json"}
FILE_SYMBOL = {"MARUTI": "MARUTI", "M&M": "MandM", "TMPV": "TMPV"}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def nse(requests, quote_status=200):
    def handler(request: httpx.Request):
        requests.append(request)
        query = parse_qs(urlsplit(str(request.url)).query)
        if "globalSearch" in request.url.path:
            name = SEARCHES.get(query["symbol"][0])
            return httpx.Response(200, json=load(name) if name else {"data": []})
        if "GetQuoteApi" in request.url.path:
            symbol = query["symbol"][0]
            if quote_status != 200 or symbol not in FILE_SYMBOL:
                return httpx.Response(quote_status if quote_status != 200 else 404, text="Access Denied")
            return httpx.Response(200, json=load(f"nse_quote_{FILE_SYMBOL[symbol]}.json"))
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def recorded_yahoo(tickers):
    def fetch(ticker):
        tickers.append(ticker)
        return load(f"yfinance_{FILE_SYMBOL[ticker.removesuffix('.NS')]}.json")
    return fetch


def adapter(requests, tickers, quote_status=200, yf_fetch=None, overrides=None):
    return HttpPeerData(nse(requests, quote_status), overrides=overrides,
                        yf_fetch=yf_fetch or recorded_yahoo(tickers))


def test_a_peer_named_in_the_rhp_is_found_on_nse_and_priced_with_its_latest_annual_figures():
    requests, tickers = [], []

    q = adapter(requests, tickers).quote("Maruti Suzuki India Limited")

    assert q.symbol == "MARUTI" and tickers == ["MARUTI.NS"]
    # NSE: last price ₹11,420; total market cap ₹3,590,477,395,080 = ₹359,047.74 crore; as of 09-Oct-2026.
    assert (q.price.value, q.price.source_name, q.price.as_of) == (11420, "NSE", date(2026, 10, 9))
    assert q.market_cap.value == pytest.approx(359047.74, abs=0.01)
    assert q.market_cap.source_url.startswith("https://www.nseindia.com/api/")
    # Yahoo Finance, FY ending 31 Mar 2026 (rupees / 10^7): revenue 1,770,941,000,000 -> 177,094.1 crore;
    # EBITDA 260,989,000,000 -> 26,098.9; PAT 146,795,000,000 -> 14,679.5; equity 1,071,563,000,000 ->
    # 107,156.3; debt 1,025,000,000 -> 102.5; cash 669,000,000 -> 66.9. FY2025 revenue -> 147,468.6.
    got = {f: getattr(q, f).value for f in ("revenue", "ebitda", "pat", "net_worth", "debt", "cash")}
    assert got == pytest.approx({"revenue": 177094.1, "ebitda": 26098.9, "pat": 14679.5,
                                 "net_worth": 107156.3, "debt": 102.5, "cash": 66.9})
    assert (q.pat.source_name, q.pat.as_of, q.pat.unit) == ("Yahoo Finance", date(2026, 3, 31), "₹ crore")
    assert (q.revenue_prior_year.value, q.revenue_prior_year.as_of) == (pytest.approx(147468.6), date(2025, 3, 31))


def test_a_manual_override_maps_a_renamed_peer_without_searching():
    # Since the 2025 demerger NSE's search gives "Tata Motors Limited" to the CV company (TMCV); the RHPs'
    # Tata Motors trades as TMPV. config.yaml carries that override.
    requests, tickers = [], []
    peers = adapter(requests, tickers, overrides={"Tata Motors Limited": "TMPV"})

    q = peers.quote("TATA MOTORS LTD.")

    assert q.symbol == "TMPV"
    assert not any("globalSearch" in r.url.path for r in requests)
    assert q.price.value == 279.9  # NSE lastPrice


def test_without_an_override_the_search_result_is_taken_as_nse_names_it():
    assert adapter([], []).resolve("Tata Motors Limited") == "TMCV"


def test_a_symbol_with_an_ampersand_is_quoted_in_the_url():
    requests, tickers = [], []

    q = adapter(requests, tickers).quote("Mahindra & Mahindra Limited")

    assert q.symbol == "M&M" and tickers == ["M&M.NS"]
    assert q.price.value == 2796.2
    # totalMarketCap ₹3,477,155,317,242.2 -> ₹347,715.53 crore.
    assert q.market_cap.value == pytest.approx(347715.53, abs=0.01)


def test_when_nse_refuses_the_quote_yahoo_gives_the_price_and_market_cap():
    q = adapter([], [], quote_status=403).quote("MARUTI")

    # Yahoo fast_info: last price ₹11,420.0; market cap ₹3,590,477,395,080 -> ₹359,047.74 crore; 9 Oct 2026.
    assert (q.price.value, q.price.source_name, q.price.as_of) == (11420.0, "Yahoo Finance", date(2026, 10, 9))
    assert q.market_cap.source_name == "Yahoo Finance"
    assert q.market_cap.value == pytest.approx(359047.74, abs=0.01)


def test_when_yahoo_fails_the_financials_are_missing_not_filled_in():
    def broken(ticker):
        raise ConnectionError("yahoo down")

    q = adapter([], [], yf_fetch=broken).quote("MARUTI")

    assert q.price.value == 11420
    assert (q.revenue, q.ebitda, q.pat, q.net_worth, q.debt, q.cash) == (None,) * 6


def test_a_number_the_latest_year_lacks_is_missing_never_taken_from_an_older_year():
    payload = load("yfinance_MARUTI.json")
    payload["income_stmt"]["2026-03-31"]["EBITDA"] = None

    q = adapter([], [], yf_fetch=lambda t: payload).quote("MARUTI")

    assert q.ebitda is None
    assert q.pat.value == pytest.approx(14679.5)


def test_a_peer_nse_cannot_find_has_no_quote():
    requests, tickers = [], []

    assert adapter(requests, tickers).quote("Imaginary Motors Limited") is None
    assert tickers == []


def test_a_listing_not_in_rupees_gives_no_numbers():
    payload = {**load("yfinance_MARUTI.json"), "currency": "USD"}

    q = adapter([], [], quote_status=403, yf_fetch=lambda t: payload).quote("MARUTI")

    assert q is None


def test_the_peer_carries_its_yahoo_profile_for_the_peer_fit_check():
    summary = "Maruti Suzuki India Limited manufactures and sells passenger vehicles in India. " * 20
    payload = {**load("yfinance_MARUTI.json"),
               "profile": {"sector": "Consumer Cyclical", "industry": "Auto Manufacturers", "summary": summary}}

    q = adapter([], [], yf_fetch=lambda t: payload).quote("MARUTI")

    assert (q.sector, q.industry) == ("Consumer Cyclical", "Auto Manufacturers")
    assert q.business_summary == summary[:600]


def test_without_a_profile_the_peer_still_has_its_numbers():
    q = adapter([], []).quote("MARUTI")  # the recorded payloads predate the profile

    assert (q.sector, q.industry, q.business_summary) == (None, None, None)
    assert q.pat.value == pytest.approx(14679.5)


@pytest.mark.parametrize("name, symbol", [("Elgi Equipment Limited", "ELGIEQUIP"),
                                          ("Ingersoll-Rand (India) Limited", "INGERRAND")])
def test_the_configured_overrides_map_peers_nse_search_cannot_find(name, symbol):
    from ipo_agents.config import load_config

    overrides = load_config(Path(__file__).resolve().parent.parent / "config.yaml").peers.symbol_overrides
    requests = []

    assert adapter(requests, [], overrides=overrides).resolve(name) == symbol
    assert requests == []  # no search
