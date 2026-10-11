"""Offer calculations + flags at seam B (and the summary at seam A) (spec stories 21-23; plan 5.1, 5.2).

Every expected number below is worked by hand in the comment next to it, not re-derived with calc.py.
"""
from datetime import date

import pytest

import ipo_agents.analysis as analysis
from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import Fact, OfferObjects, SellingShareholder
from ipo_agents.runner import run_daily
from tests.conftest import make_tiny_rhp
from tests.fakes import NOT_DISCLOSED, FakeDownloader, FakeExchange, ScriptedLLM, make_ipo, offer_objects
from tests.test_offer_objects import golden_facts

# Acme's band is ₹100–₹105 (make_ipo), so the upper band is ₹105.
OFFER_PAGE = "\n".join([
    "THE OFFER",
    "Fresh Issue of up to Rs. 5,000.00 million",
    "Offer for Sale of up to 8,000,000 equity shares",
    "Offer for Sale of up to 60,000,000 equity shares",
    "Offer for Sale aggregating up to Rs. 700.00 crore",
    "Total issue size of up to Rs. 1,200.00 crore",
])
CAPITAL_PAGE = "\n".join([
    "CAPITAL STRUCTURE",
    "Equity Shares outstanding prior to the Offer 100,000,000",
    "Equity Shares outstanding after the Offer 125,000,000",
    "Promoter holding before the Offer 72.50%",
    "Promoter holding after the Offer 45.30%",
    "Promoter holding after the Offer 58.00%",
    "Investor Selling Shareholder holding after the Offer: Nil",
    "Investor Selling Shareholder holding after the Offer 2.10%",
])
OBJECTS_PAGE = "\n".join([
    "OBJECTS OF THE OFFER",
    "General corporate purposes Rs. 115.00 crore",
    "General corporate purposes Rs. 1,400.00 million",
    "General corporate purposes Rs. 500.00 million",
])


def fact(value, page, quote, unit):
    return Fact(value=value, unit=unit, page=page, verbatim_quote=quote)


FRESH = fact(5000.0, 2, "Fresh Issue of up to Rs. 5,000.00 million", "₹ million")  # = ₹500 crore
OFS_SMALL = fact(8_000_000, 2, "Offer for Sale of up to 8,000,000 equity shares", "equity shares")
OFS_BIG = fact(60_000_000, 2, "Offer for Sale of up to 60,000,000 equity shares", "equity shares")
OFS_RUPEES = fact(700.0, 2, "Offer for Sale aggregating up to Rs. 700.00 crore", "₹ crore")
ISSUE_RUPEES = fact(1200.0, 2, "Total issue size of up to Rs. 1,200.00 crore", "₹ crore")
PRE_SHARES = fact(100_000_000, 3, "Equity Shares outstanding prior to the Offer 100,000,000", "equity shares")
POST_SHARES = fact(125_000_000, 3, "Equity Shares outstanding after the Offer 125,000,000", "equity shares")
PROMOTER_PRE = fact(72.5, 3, "Promoter holding before the Offer 72.50%", "%")
PROMOTER_POST_LOW = fact(45.3, 3, "Promoter holding after the Offer 45.30%", "%")
PROMOTER_POST_HIGH = fact(58.0, 3, "Promoter holding after the Offer 58.00%", "%")
EXIT_NIL = fact(0, 3, "Investor Selling Shareholder holding after the Offer: Nil", "%")
STAYS_IN = fact(2.1, 3, "Investor Selling Shareholder holding after the Offer 2.10%", "%")
GCP_NEAR = fact(115.0, 4, "General corporate purposes Rs. 115.00 crore", "₹ crore")
GCP_OVER = fact(1400.0, 4, "General corporate purposes Rs. 1,400.00 million", "₹ million")
GCP_LOW = fact(500.0, 4, "General corporate purposes Rs. 500.00 million", "₹ million")


def wrong_page(f: Fact) -> Fact:
    """The same fact citing a page it isn't on, so the verifier marks it "could not verify"."""
    return f.model_copy(update={"page": 5 if f.page != 5 else 2})


@pytest.fixture
def rhp(tmp_path):
    return make_tiny_rhp(tmp_path / "calc_rhp.pdf", (OFFER_PAGE, CAPITAL_PAGE, OBJECTS_PAGE))


def analyse(rhp, adapters, config, **fields):
    adapters.llm = ScriptedLLM({OfferObjects: offer_objects(**fields)}, models=config.models)
    return analyse_rhp(make_ipo(), rhp, adapters, config)


def codes(report):
    return [f.code for f in report.flags]


def seller(category, holding_after, name="Growth Fund I"):
    return SellingShareholder(name=name, category=category, shares_offered=NOT_DISCLOSED,
                              holding_after=holding_after)


# --- derived numbers ------------------------------------------------------------


def test_ofs_and_issue_size_are_computed_at_the_upper_band_when_rupees_are_blank(rhp, adapters, config):
    report = analyse(rhp, adapters, config, fresh_issue=FRESH, ofs_shares=OFS_BIG)
    calc = report.calculations

    # 60,000,000 shares x ₹105 = ₹6,300,000,000 = ₹630 crore (1 crore = 10,000,000).
    assert calc["ofs_amount_cr"].value == pytest.approx(630.0)
    assert "upper band" in calc["ofs_amount_cr"].basis
    # ₹500 crore fresh + ₹630 crore OFS = ₹1,130 crore.
    assert calc["issue_size_cr"].value == pytest.approx(1130.0)
    # 630 / 1,130 = 55.75%; 500 / 1,130 = 44.25%.
    assert calc["ofs_pct"].value == pytest.approx(55.75, abs=0.01)
    assert calc["fresh_pct"].value == pytest.approx(44.25, abs=0.01)
    assert {(i.field, i.page) for i in calc["ofs_pct"].inputs} >= {
        ("offer_objects.fresh_issue", 2), ("offer_objects.ofs_shares", 2), ("ipo.price_band", None)}


def test_ofs_pct_uses_the_rupee_amounts_when_both_are_verified(rhp, adapters, config):
    report = analyse(rhp, adapters, config, ofs_amount=OFS_RUPEES, issue_size=ISSUE_RUPEES, ofs_shares=OFS_SMALL)

    # 700 / 1,200 = 58.33%, from the RHP's own rupee amounts, not shares x band.
    ofs = report.calculations["ofs_pct"]
    assert ofs.value == pytest.approx(58.33, abs=0.01)
    assert "upper band" not in ofs.basis
    assert [i.field for i in ofs.inputs] == ["offer_objects.ofs_amount", "offer_objects.issue_size"]


def test_million_is_converted_to_crore(rhp, adapters, config):
    report = analyse(rhp, adapters, config, fresh_issue=FRESH, gcp_amount=GCP_NEAR)

    # ₹5,000 million = ₹500 crore (10 million = 1 crore).
    assert report.calculations["fresh_issue_cr"].value == pytest.approx(500.0)
    # ₹115 crore GCP / ₹500 crore fresh = 23%; without the conversion it would read 2.3%.
    assert report.calculations["gcp_pct_of_fresh"].value == pytest.approx(23.0)
    assert report.calculations["gcp_pct_of_fresh"].pages == [2, 4]


def test_dilution_and_promoter_change(rhp, adapters, config):
    report = analyse(rhp, adapters, config, pre_issue_shares=PRE_SHARES, post_issue_shares=POST_SHARES,
                     promoter_holding_pre_pct=PROMOTER_PRE, promoter_holding_post_pct=PROMOTER_POST_LOW)

    # Fresh shares 125,000,000 - 100,000,000 = 25,000,000; 25,000,000 / 125,000,000 = 20%.
    assert report.calculations["dilution_pct"].value == pytest.approx(20.0)
    # 45.30% - 72.50% = -27.20 percentage points.
    assert report.calculations["promoter_change_pp"].value == pytest.approx(-27.2)
    assert report.calculations["promoter_change_pp"].pages == [3]


def test_an_unverified_input_is_not_computable_and_raises_no_flag(rhp, adapters, config):
    report = analyse(rhp, adapters, config, fresh_issue=FRESH, ofs_shares=wrong_page(OFS_BIG))

    assert report.unverified_fields == ["offer_objects.ofs_shares"]
    ofs = report.calculations["ofs_pct"]
    assert ofs.value is None
    assert ofs.not_computable_reason == "offer_objects.ofs_shares: could not verify"
    assert "OFS_HEAVY" not in codes(report)


def test_a_not_disclosed_input_is_not_computable(rhp, adapters, config):
    report = analyse(rhp, adapters, config, fresh_issue=FRESH)

    assert report.calculations["gcp_pct_of_fresh"].value is None
    assert report.calculations["gcp_pct_of_fresh"].not_computable_reason == "offer_objects.gcp_amount: not disclosed"
    assert report.flags == []


# --- flags ----------------------------------------------------------------------


def test_ofs_over_half_the_issue_is_flagged(rhp, adapters, config):
    report = analyse(rhp, adapters, config, fresh_issue=FRESH, ofs_shares=OFS_BIG)

    [flag] = report.flags
    assert (flag.code, flag.severity) == ("OFS_HEAVY", "warning")
    assert flag.evidence_pages == [2]


def test_ofs_under_half_the_issue_is_not_flagged(rhp, adapters, config):
    report = analyse(rhp, adapters, config, fresh_issue=FRESH, ofs_shares=OFS_SMALL)

    # 8,000,000 x ₹105 = ₹84 crore; 84 / (500 + 84) = 14.38%.
    assert report.calculations["ofs_pct"].value == pytest.approx(14.38, abs=0.01)
    assert report.flags == []


def test_an_investor_selling_out_fully_is_flagged(rhp, adapters, config):
    report = analyse(rhp, adapters, config, selling_shareholders=[seller("investor", EXIT_NIL)])

    [flag] = report.flags
    assert (flag.code, flag.severity, flag.evidence_pages) == ("PE_VC_FULL_EXIT", "warning", [3])
    assert "Growth Fund I" in flag.message


@pytest.mark.parametrize("category, holding", [("investor", STAYS_IN), ("promoter", EXIT_NIL),
                                               ("investor", wrong_page(EXIT_NIL))])
def test_a_partial_exit_a_non_investor_or_an_unverified_holding_is_not_flagged(rhp, adapters, config,
                                                                               category, holding):
    report = analyse(rhp, adapters, config, selling_shareholders=[seller(category, holding)])

    assert "PE_VC_FULL_EXIT" not in codes(report)


def test_gcp_close_to_the_cap_is_a_warning(rhp, adapters, config):
    report = analyse(rhp, adapters, config, fresh_issue=FRESH, gcp_amount=GCP_NEAR)

    [flag] = report.flags
    assert (flag.code, flag.severity, flag.evidence_pages) == ("GCP_NEAR_CAP", "warning", [2, 4])


def test_gcp_over_the_cap_is_an_error(rhp, adapters, config):
    report = analyse(rhp, adapters, config, fresh_issue=FRESH, gcp_amount=GCP_OVER)

    # ₹1,400 million = ₹140 crore; 140 / 500 = 28% > 25%.
    assert report.calculations["gcp_pct_of_fresh"].value == pytest.approx(28.0)
    [flag] = report.flags
    assert (flag.code, flag.severity) == ("GCP_OVER_CAP", "error")


def test_gcp_well_under_the_cap_is_not_flagged(rhp, adapters, config):
    report = analyse(rhp, adapters, config, fresh_issue=FRESH, gcp_amount=GCP_LOW)

    # ₹500 million = ₹50 crore; 50 / 500 = 10%.
    assert report.calculations["gcp_pct_of_fresh"].value == pytest.approx(10.0)
    assert report.flags == []


def test_promoter_holding_under_half_after_the_issue_is_info(rhp, adapters, config):
    report = analyse(rhp, adapters, config, promoter_holding_post_pct=PROMOTER_POST_LOW)

    [flag] = report.flags
    assert (flag.code, flag.severity, flag.evidence_pages) == ("PROMOTER_BELOW_50", "info", [3])


def test_promoter_holding_over_half_is_not_flagged(rhp, adapters, config):
    report = analyse(rhp, adapters, config, promoter_holding_post_pct=PROMOTER_POST_HIGH)

    assert report.flags == []


# --- golden: Hyundai, a 100% offer for sale ---------------------------------------


def test_hyundai_is_flagged_ofs_heavy(golden_rhp, golden_doc, adapters, config, monkeypatch):
    slug = "hyundai-motor-india"
    monkeypatch.setattr(analysis, "ingest", lambda path: golden_doc(slug))
    facts = golden_facts(golden_rhp, slug)
    # Pure OFS: the p. 22 shareholding table is the pre-Offer count, the same as post-Offer.
    facts["pre_issue_shares"] = facts["post_issue_shares"]
    ipo = make_ipo().model_copy(update={"name": "Hyundai Motor India Ltd", "price_band": "₹1,865–₹1,960"})
    adapters.llm = ScriptedLLM({OfferObjects: offer_objects(**facts)}, models=config.models)

    report = analyse_rhp(ipo, golden_rhp(slug), adapters, config)

    assert report.unverified_fields == []
    # 142,194,700 shares x ₹1,960 = ₹278,701,612,000 = ₹27,870.16 crore (the reported issue size).
    assert report.calculations["ofs_amount_cr"].value == pytest.approx(27870.16, abs=0.01)
    assert report.calculations["issue_size_cr"].value == pytest.approx(27870.16, abs=0.01)
    assert report.calculations["ofs_pct"].value == pytest.approx(100.0)
    assert report.calculations["dilution_pct"].value == pytest.approx(0.0)
    assert "OFS_HEAVY" in codes(report)


# --- seam A: the summary line ------------------------------------------------------


def test_summary_shows_ofs_pct_and_red_flag_codes(rhp, adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.downloader = FakeDownloader(rhp)
    adapters.llm = ScriptedLLM({OfferObjects: offer_objects(fresh_issue=FRESH, ofs_shares=OFS_BIG,
                                                            promoter_holding_post_pct=PROMOTER_POST_LOW)},
                               models=config.models)

    run_daily(date(2026, 10, 13), adapters, config)

    [msg] = adapters.notifier.messages
    # 630 / 1,130 = 55.75% -> "55.8%"; info flags (PROMOTER_BELOW_50) stay out of the summary.
    assert "OFS 55.8% (at upper band) · ⚠️ OFS_HEAVY" in msg
    assert "PROMOTER_BELOW_50" not in msg


@pytest.mark.parametrize("unit", ["Nil", "₹ million", "equity shares"])
def test_a_zero_is_zero_crore_whatever_its_unit(unit):
    # HD Fire's FY2026 "Total Borrowings (B) Nil" is read as 0 with the unit "Nil": no debt, not unreadable.
    from ipo_agents.calc import to_crore

    assert to_crore(Fact(value=0.0, unit=unit, page=329, verbatim_quote="Total Borrowings (B) Nil")) == 0.0


def test_a_nonzero_amount_in_a_unit_that_is_not_rupees_is_still_refused():
    from ipo_agents.calc import to_crore

    with pytest.raises(ValueError, match="not a rupee unit"):
        to_crore(Fact(value=5.0, unit="Nil", page=329, verbatim_quote="Nil"))
