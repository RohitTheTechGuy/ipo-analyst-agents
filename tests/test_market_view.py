"""The "What the market is saying" section: a plain read built by Python from the sentiment data, no model."""
from __future__ import annotations

from datetime import date

from ipo_agents.contracts import ClaimedFact, Sentiment, SubscriptionStatus
from ipo_agents.report.labels import market_read
from ipo_agents.runner import format_summary
from tests.report_fixture import full_report

BAND = "₹258–₹271"


def gmp(value: float) -> ClaimedFact:
    return ClaimedFact(value=value, unit="₹", source_url="https://ig.example/gmp", source_name="InvestorGain",
                       as_of=date(2026, 10, 11), label="GMP (unofficial)")


def tally(**counts: int) -> dict:
    return {"subscribe": 0, "listing_gains_only": 0, "avoid": 0, "neutral": 0, **counts}


def test_a_majority_stance_and_the_gmp_as_a_listing_gain_over_the_upper_band():
    s = Sentiment(available=True, tally=tally(listing_gains_only=1, neutral=7), gmp=gmp(77))
    assert market_read(s, BAND) == ("Most coverage is neutral (7 of 8 articles); the grey market prices a "
                                    "28% listing gain over the ₹271 upper band (unofficial).")


def test_split_coverage_a_negative_gmp_and_the_subscription_so_far():
    sub = SubscriptionStatus(total=ClaimedFact(value=3.4, unit="×", source_url="https://nse.example", source_name="NSE",
                                               as_of=date(2026, 10, 14), label="Subscription (total)"))
    s = Sentiment(available=True, tally=tally(subscribe=2, avoid=2), gmp=gmp(-10), subscription=sub)
    assert market_read(s, BAND) == ("Coverage is split (2 subscribe / 2 avoid); the grey market prices a "
                                    "4% listing loss below the ₹271 upper band (unofficial); bids so far are 3.4× "
                                    "the shares on offer.")


def test_nothing_to_say():
    assert market_read(Sentiment(available=False), BAND) == "No press coverage could be read, and no GMP or subscription data yet."
    assert market_read(Sentiment(available=True, tally=tally()), BAND) == "No recent press coverage, and no GMP or subscription data yet."


def test_telegram_summary_has_its_own_market_section_with_bulls_and_bears():
    lines = format_summary(full_report()).splitlines()
    start = lines.index("📣 What the market is saying")
    assert lines[start - 1] == ""
    assert lines[start + 1].startswith("Coverage is split (1 subscribe / 1 listing gains only)")
    section = lines[start:lines.index("", start)]
    assert "Bulls say:" in section and "• Valuation well below listed peers" in section
    assert "Bears say:" in section and "• One plant makes all output" in section
