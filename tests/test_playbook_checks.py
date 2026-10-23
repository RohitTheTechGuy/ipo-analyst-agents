"""The checks behind the agents' playbook (ipo_agents/playbook.md): numbers the RHP doesn't print, worked
out from the figures it does. Pure functions; every expected number is worked by hand in the comments."""
from datetime import date

from ipo_agents.calc import compute_offer
from ipo_agents.contracts import (BrandLicence, ClaimedFact, Derived, Fact, MultipleStats, OfferPriceBasis, Peer,
                                  PeerQuote, PeerRow, ProceedsObject, Quote, Valuation)
from ipo_agents.fin_calc import NotDerivable, free_cash_flow, full_years, leverage, profit_line
from ipo_agents.flags import offer_flags
from ipo_agents.section_calc import governance_flags
from ipo_agents.synthesizer import PLAYBOOK, SYSTEM as SYNTH_SYSTEM, build_digest
from ipo_agents.advisor import SYSTEM as ADVISOR_SYSTEM
from ipo_agents.valuation import _all_stats, _checks, _Year, ipo_multiple
from tests.fakes import NOT_DISCLOSED, found_nothing, make_ipo, offer_objects
from tests.test_financials import financials, period
from tests.report_fixture import full_report as make_report

PAGE = 3


def cr(value, quote="row"):
    return Fact(value=value, unit="₹ crore", page=PAGE, verbatim_quote=f"{quote} {value}")


def shares(value):
    return Fact(value=value, unit="equity shares", page=2, verbatim_quote=f"{value:,.0f} equity shares")


def pct(value):
    return Fact(value=value, unit="%", page=2, verbatim_quote=f"{value}%")


def year(label, end, **values):
    return period(label, end, **{k: cr(v, k) for k, v in values.items()})


# --- free cash flow -----------------------------------------------------------------------------

def test_free_cash_flow_shows_the_capex_trend_and_flags_a_negative_latest_year():
    fy25 = year("Fiscal 2025", "March 31, 2025", revenue_from_operations=1000, cfo=300, capex=-488)
    fy26 = year("Fiscal 2026", "March 31, 2026", revenue_from_operations=1200, cfo=480, capex=280)
    turned = free_cash_flow(full_years([fy26, fy25]))
    # FY25: 300 − 488 = −188, capex 48.8% of revenue; FY26: 480 − 280 = 200, capex 23.3%
    assert turned.status == "clear"
    assert "Fiscal 2025: FCF -188.00" in turned.detail and "48.8% of revenue" in turned.detail
    assert "Fiscal 2026: FCF 200.00" in turned.detail and "23.3% of revenue" in turned.detail

    negative = free_cash_flow(full_years([fy25]))
    assert negative.status == "raised"
    assert free_cash_flow(full_years([year("Fiscal 2026", "March 31, 2026", cfo=10)])).status == "not_computable"


# --- EBIT / EBITDA from the P&L -----------------------------------------------------------------

def test_ebitda_is_built_from_the_pnl_when_the_rhp_does_not_state_it():
    fy = year("Fiscal 2026", "March 31, 2026", pbt=400, finance_costs=100, depreciation=250)
    fin = financials([fy])
    assert profit_line(fin, fy, "ebit")[0] == 500  # 400 + 100
    value, inputs, basis = profit_line(fin, fy, "ebitda")
    assert value == 750 and len(inputs) == 3 and "computed from the RHP" in basis  # 400 + 100 + 250

    stated = year("Fiscal 2026", "March 31, 2026", ebitda=800, pbt=400)
    value, _, basis = profit_line(financials([stated]), stated, "ebitda")
    assert (value, basis) == (800, "EBITDA as disclosed")
    try:
        profit_line(fin := financials([year("Fiscal 2026", "March 31, 2026", pbt=400)]), fin.periods[0], "ebit")
    except NotDerivable as exc:
        assert "finance_costs" in str(exc)
    else:
        raise AssertionError("a missing input must not be guessed")


# --- leverage after the proceeds ----------------------------------------------------------------

def test_net_debt_to_ebitda_before_and_after_the_issue_repays_debt():
    fy = year("Fiscal 2026", "March 31, 2026", total_borrowings=5000, cash_and_equivalents=1000, ebitda=2000)
    offer = offer_objects(objects=[ProceedsObject(purpose="Repayment or prepayment of certain borrowings", amount=cr(1500)),
                                   ProceedsObject(purpose="New plant", amount=cr(700))])
    out = leverage(financials([fy]), offer)
    assert out["net_debt_to_ebitda"].value == 2.0  # (5000 − 1000) / 2000
    assert out["net_debt_to_ebitda_post"].value == 1.25  # (5000 − 1500 − 1000) / 2000; the plant isn't debt

    no_repay = leverage(financials([fy]), offer_objects(objects=[]))
    assert no_repay["net_debt_to_ebitda_post"].value is None
    loss = year("Fiscal 2026", "March 31, 2026", total_borrowings=5000, cash_and_equivalents=1000, ebitda=-10)
    assert "negative" in leverage(financials([loss]), offer)["net_debt_to_ebitda"].not_computable_reason


# --- free float and the lock-in overhang --------------------------------------------------------

def jio_like(**more):
    # 100 crore shares after the issue; 2.9 crore sold (fresh 2.0 + OFS 0.9); promoters keep 65%.
    return offer_objects(pre_issue_shares=shares(980_000_000), post_issue_shares=shares(1_000_000_000),
                         ofs_shares=shares(9_000_000), promoter_holding_post_pct=pct(65.0), **more)


def test_a_thin_float_with_a_big_unlockable_stake_is_flagged():
    calcs = compute_offer(jio_like(), make_ipo())
    assert round(calcs["free_float_pct"].value, 6) == 2.9  # (20,000,000 + 9,000,000) / 1,000,000,000
    assert round(calcs["unlockable_pct"].value, 6) == 32.1  # 100 − 65 − 2.9
    assert round(calcs["overhang_x_float"].value, 1) == 11.1  # 32.1 / 2.9

    [flag] = [f for f in offer_flags(jio_like(), calcs) if f.code == "UNLOCK_OVERHANG"]
    assert "2.9%" in flag.message and "6 months under SEBI rules" in flag.message

    stated = jio_like(non_promoter_lock_in_months=Fact(value=6, unit="months", page=4, verbatim_quote="six months"))
    [flag] = [f for f in offer_flags(stated, compute_offer(stated, make_ipo())) if f.code == "UNLOCK_OVERHANG"]
    assert "6-month lock-in (page 4)" in flag.message


def test_a_wide_float_raises_no_overhang():
    offer = offer_objects(pre_issue_shares=shares(800_000_000), post_issue_shares=shares(1_000_000_000),
                          ofs_shares=shares(0), promoter_holding_post_pct=pct(60.0))
    calcs = compute_offer(offer, make_ipo())
    assert round(calcs["free_float_pct"].value, 6) == 20.0
    assert not [f for f in offer_flags(offer, calcs) if f.code == "UNLOCK_OVERHANG"]


# --- the brand licence --------------------------------------------------------------------------

def test_a_brand_the_promoter_can_withdraw_is_flagged_only_when_its_quote_verified():
    quote = Quote(page=150, verbatim_quote="The Jio trademark is owned by RIL and licensed to us")
    brand = BrandLicence(owner="promoter_group", terminable=True, royalty=NOT_DISCLOSED, source=quote)
    g = found_nothing(type(make_report().governance)).model_copy(update={"brand": brand})
    pay = Derived(value=None, unit="%", not_computable_reason="n/a")
    [flag] = [f for f in governance_flags(g, pay) if f.code == "BRAND_LICENSED"]
    assert "owner can end the licence" in flag.message and flag.evidence_pages == [150]

    unverified = brand.model_copy(update={"source": Quote(page=None, verbatim_quote=quote.verbatim_quote)})
    assert not [f for f in governance_flags(g.model_copy(update={"brand": unverified}), pay) if f.code == "BRAND_LICENSED"]
    own = brand.model_copy(update={"owner": "company"})
    assert not [f for f in governance_flags(g.model_copy(update={"brand": own}), pay) if f.code == "BRAND_LICENSED"]


# --- multiples worked out from the financials ---------------------------------------------------

AS_OF = date(2026, 10, 20)


def claim(value):
    return ClaimedFact(value=value, unit="₹ crore", source_url="https://x.test", source_name="Yahoo Finance", as_of=AS_OF)


def test_ev_multiples_use_post_issue_ev_and_a_derived_ebit():
    fy = year("Fiscal 2026", "March 31, 2026", pat=300, revenue_from_operations=1500, net_worth=2000,
              total_borrowings=1000, cash_and_equivalents=200, pbt=420, finance_costs=80, depreciation=300)
    latest = _Year(financials([fy]), fy)
    mcap = Derived(value=10000.0, unit="₹ crore")
    fresh = Derived(value=500.0, unit="₹ crore", basis="computed from RHP figures")

    ev_ebit = ipo_multiple("ev_ebit", mcap, latest, fresh)
    assert ev_ebit.value == (10000 + 1000 - 200 - 500) / 500  # post-issue EV 10,300 / EBIT (420 + 80)
    assert "post-issue EV" in ev_ebit.basis and "EBIT = PBT + finance costs" in ev_ebit.basis
    ev_ebitda = ipo_multiple("ev_ebitda", mcap, latest, fresh)
    assert ev_ebitda.value == 10300 / 800  # EBITDA not stated: 420 + 80 + 300

    rows = [PeerRow(name=n, origin="rhp", quote=PeerQuote(name=n, symbol=n, market_cap=claim(m), ebit=claim(e),
                                                          debt=claim(0), cash=claim(0)))
            for n, m, e in (("A", 4000, 200), ("B", 9000, 300))]  # EV/EBIT 20× and 30×: median 25×
    stats = _all_stats("ev_ebit", mcap, latest, fresh, rows)
    assert stats.median.value == 25.0 and round(stats.premium_pct.value, 1) == round(100 * (20.6 / 25 - 1), 1)


def test_roe_vs_peers_is_always_compared_and_the_rhp_peer_table_comes_first():
    fy = year("Fiscal 2026", "March 31, 2026", pat=300, net_worth=2000, revenue_from_operations=1500)
    latest = _Year(financials([fy]), fy)
    basis = found_nothing(OfferPriceBasis).model_copy(update={"peers": [
        Peer(name="Bharti Airtel", pe=NOT_DISCLOSED, eps=NOT_DISCLOSED, nav_per_share=NOT_DISCLOSED,
             revenue=NOT_DISCLOSED, ronw_pct=pct(14.6))]})
    checks = _checks(latest, None, [], basis)
    assert checks["ipo_roe_pct"].value == 15.0  # 300 / 2000
    assert checks["peer_median_roe_pct"].value == 14.6 and "RHP's peer table" in checks["peer_median_roe_pct"].basis


def test_the_multiples_table_reaches_the_digest():
    stats = MultipleStats(multiple="ev_ebit", reason="", ipo=Derived(value=23.71, unit="×", basis="post-issue EV"),
                          median=Derived(value=24.4, unit="×"), min=Derived(value=24.4, unit="×"),
                          max=Derived(value=24.4, unit="×"), premium_pct=Derived(value=-2.8, unit="%"))
    report = make_report()
    v = (report.valuation or Valuation(ipo_market_cap=Derived(value=1.0, unit="₹ crore"))).model_copy(
        update={"all_multiples": [stats], "read": report.valuation.read if report.valuation else "x"})
    item = build_digest(report.model_copy(update={"valuation": v}))["V:multiples"]
    assert item == {"EV/EBIT": {"ipo": 23.7, "unit": "×", "basis": "post-issue EV", "peer_median": 24.4, "premium_pct": -3}}


def test_the_playbook_is_in_both_the_synthesizer_and_advisor_prompts():
    assert "UNLOCK_OVERHANG" in PLAYBOOK and "V:multiples" in PLAYBOOK
    assert PLAYBOOK in SYNTH_SYSTEM and PLAYBOOK in ADVISOR_SYSTEM


def test_a_fresh_issue_printed_only_as_up_to_n_shares_still_gives_market_cap_float_and_holding():
    # Jio Platforms' RHP: "fresh issue of up to 270,000,000 Equity Shares ... aggregating up to ₹[●] million",
    # no selling shareholder, promoters 66.43% of 8,939,030,830 pre-issue shares; band ₹1,065–₹1,119.
    offer = offer_objects(fresh_issue_shares=shares(270_000_000), pre_issue_shares=shares(8_939_030_830),
                          promoter_holding_pre_pct=pct(66.43))
    calcs = compute_offer(offer, make_ipo().model_copy(update={"price_band": "₹1065–₹1119"}))
    assert round(calcs["market_cap_cr"].value, 2) == 1_030_490.55  # 9,209,030,830 x ₹1,119 / 1 crore
    assert round(calcs["fresh_issue_cr"].value) == 30_213  # 270,000,000 x ₹1,119
    assert round(calcs["free_float_pct"].value, 2) == 2.93  # no OFS: 270,000,000 / 9,209,030,830
    # promoters sell none: 66.43% x 8,939,030,830 / 9,209,030,830 = 64.48%; the rest is the pre-IPO stake
    assert round(calcs["unlockable_pct"].value, 2) == round(100 - 66.43 * 8_939_030_830 / 9_209_030_830 - 2.932, 2)
