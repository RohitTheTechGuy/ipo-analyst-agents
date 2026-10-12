"""Peer valuation at seam B (plan 5.3; ticket 17; spec stories 31-34), and its Telegram line at seam A.

Every expected number is worked by hand in the comments. The IPO: make_ipo()'s band is ₹100–₹105, so the
upper band is ₹105; 100,000,000 post-issue shares x ₹105 = ₹10,500,000,000 = ₹1,050 crore.
"""
from datetime import date

import pytest

import ipo_agents.analysis as analysis
from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import (BusinessIndustry, ClaimedFact, Fact, Financials, Kpi, MultipleChoice, OfferObjects,
                                  OfferPriceBasis, Peer, PeerFit, PeerFitCheck, PeerKpi, PeerQuote, PeriodFact,
                                  SuggestedPeer, ValuationPlan)
from ipo_agents.runner import run_daily
from tests.conftest import TINY_OFFER_PAGE, make_tiny_rhp
from tests.fakes import (NOT_DISCLOSED, FakeDownloader, FakeExchange, FakePeerData, ScriptedLLM, found_nothing,
                         make_ipo, offer_objects)
from tests.test_financials import FINANCIALS_CITATIONS, _expected, financials, period
from tests.test_offer_objects import golden_facts as offer_golden_facts

pytestmark = pytest.mark.sections("offer_objects", "financials", "offer_price_basis", "business_industry")

OFFER, FIN, BASIS = 2, 3, 9  # tiny RHP pages: the offer page, the financials page, the basis page
AS_OF = date(2026, 10, 9)
PEER_NAMES = ["Alpha Motors Limited", "Beta Motors Limited", "Gamma Motors Limited"]
POST_SHARES = Fact(value=100_000_000, unit="equity shares", page=OFFER,
                   verbatim_quote="Post-Offer equity shares 100,000,000 equity shares")
PRE_SHARES = Fact(value=90_000_000, unit="equity shares", page=OFFER,
                  verbatim_quote="Pre-Offer equity shares 90,000,000 equity shares")
ROWS = {  # FinancialPeriod field -> row label as printed
    "revenue_from_operations": "Revenue from operations",
    "pat": "Restated profit for the year",
    "ebitda": "EBITDA",
    "net_worth": "Net worth",
    "total_borrowings": "Total borrowings",
    "cash_and_equivalents": "Cash and cash equivalents",
}
# The RHP's KPI tables: the company's own KPIs, then the comparison of KPIs with listed peers.
KPI_LINES = [
    "Number of subscribers (million) Fiscal 2024 2.50",
    "Subscribers added in the three months ended June 30, 2024 (million) 0.70",
    "ARPU (Rs.) Fiscal 2024 210.00",
    "Attrition Fiscal 2024 30.00%",
    "Comparison of KPIs with listed peers",
    "Alpha Motors Number of subscribers (million) Fiscal 2024 4.00 ARPU (Rs.) 190.00",
    "Beta Motors Number of subscribers (million) Fiscal 2024 6.00 ARPU (Rs.) 230.00",
]
FY24 = dict(revenue_from_operations=700, pat=35, ebitda=70, net_worth=350, total_borrowings=100, cash_and_equivalents=50)
FY23 = dict(revenue_from_operations=500, pat=25, ebitda=50, net_worth=300, total_borrowings=90, cash_and_equivalents=40)


def _num(v):
    return f"({abs(v):,.2f})" if v < 0 else f"{v:,.2f}"


def make_rhp(tmp_path, fy24=FY24, fy23=FY23):
    rows = [f"{ROWS[k]} {_num(fy24[k])}" + (f" {_num(fy23[k])}" if fy23 else "") for k in ROWS]
    fin_page = "\n".join(["RESTATED FINANCIAL INFORMATION",
                          "(Rs. crore) Fiscal 2024" + (" Fiscal 2023" if fy23 else ""), *rows])
    offer_page = "\n".join([TINY_OFFER_PAGE, PRE_SHARES.verbatim_quote, POST_SHARES.verbatim_quote])
    basis_page = "\n".join(["BASIS FOR OFFER PRICE", "Comparison with listed industry peers", *PEER_NAMES, *KPI_LINES])
    return make_tiny_rhp(tmp_path / "val_rhp.pdf", (offer_page, fin_page), extra_pages=(basis_page,))


def fin_answer(fy24=FY24, fy23=FY23):
    def year(label, end, values, other):
        quote = lambda k: f"{ROWS[k]} {_num(values[k])}" + (f" {_num(other[k])}" if other else "")  # noqa: E731
        return period(label, end, **{k: Fact(value=v, unit="₹ crore", page=FIN, verbatim_quote=quote(k))
                                     for k, v in values.items()})
    periods = [year("Fiscal 2024", "March 31, 2024", fy24, fy23)]
    if fy23:
        # The FY2023 quote is the same row: its number is the second one printed in it.
        periods.append(year("Fiscal 2023", "March 31, 2023", fy23, None).model_copy(update={
            k: Fact(value=v, unit="₹ crore", page=FIN, verbatim_quote=f"{ROWS[k]} {_num(fy24[k])} {_num(v)}")
            for k, v in fy23.items()}))
    return financials(periods)


def peer(name):
    return Peer(name=name, pe=NOT_DISCLOSED, eps=NOT_DISCLOSED, ronw_pct=NOT_DISCLOSED, nav_per_share=NOT_DISCLOSED,
                revenue=NOT_DISCLOSED)


def claim(value, source="NSE", as_of=AS_OF):
    return ClaimedFact(value=value, unit="₹ crore", source_url=f"https://{source.lower()}.test/", source_name=source,
                       as_of=as_of)


def quote(name, symbol, *, mcap, pat, revenue, prior, ebitda, net_worth, debt=0.0, cash=0.0):
    yahoo = lambda v: claim(v, "Yahoo Finance", date(2026, 3, 31)) if v is not None else None  # noqa: E731
    return PeerQuote(name=name, symbol=symbol, market_cap=claim(mcap), pat=yahoo(pat), revenue=yahoo(revenue),
                     revenue_prior_year=yahoo(prior), ebitda=yahoo(ebitda), net_worth=yahoo(net_worth),
                     debt=yahoo(debt), cash=yahoo(cash))


# P/E: 2000/100 = 20, 3000/120 = 25, 1500/50 = 30 -> median 25, min 20, max 30.
# P/S: 2000/1000 = 2.0, 3000/2000 = 1.5, 1500/600 = 2.5 -> median 2.0.
# EV/EBITDA: (2000+200-100)/210 = 10, (3000+0-0)/200 = 15, (1500+100-100)/75 = 20 -> median 15.
# P/B: 2000/800 = 2.5, 3000/1000 = 3.0, 1500/300 = 5.0 -> median 3.0. RoE: 12.5%, 12%, 16.67% -> median 12.5%.
# Revenue growth: 1000/900-1 = 11.1%, 2000/1600-1 = 25%, 600/500-1 = 20% -> median 20%.
# PAT margin: 100/1000 = 10%, 120/2000 = 6%, 50/600 = 8.33% -> median 8.33%.
PEERS = {
    "Alpha Motors Limited": quote("Alpha Motors Limited", "ALPHA", mcap=2000, pat=100, revenue=1000, prior=900,
                                  ebitda=210, net_worth=800, debt=200, cash=100),
    "Beta Motors Limited": quote("Beta Motors Limited", "BETA", mcap=3000, pat=120, revenue=2000, prior=1600,
                                 ebitda=200, net_worth=1000),
    "Gamma Motors Limited": quote("Gamma Motors Limited", "GAMMA", mcap=1500, pat=50, revenue=600, prior=500,
                                  ebitda=75, net_worth=300, debt=100, cash=100),
}


def plan(*multiples, suggested=()):
    return ValuationPlan(multiples_to_use=[MultipleChoice(multiple=m, reason=f"{m} fits") for m in multiples],
                         additional_peers=[SuggestedPeer(name=n, reason=f"{n} also makes scooters") for n in suggested])


def analyse(rhp, adapters, config, *, valuation_plan, peers=None, fin=None, peer_names=PEER_NAMES, ipo=None,
            offer=None, fit=None):
    adapters.peers = FakePeerData(dict(PEERS) if peers is None else peers)
    adapters.llm = ScriptedLLM({
        OfferObjects: offer or offer_objects(post_issue_shares=POST_SHARES),
        Financials: fin or fin_answer(),
        OfferPriceBasis: found_nothing(OfferPriceBasis).model_copy(update={"peers": [peer(n) for n in peer_names]}),
        BusinessIndustry: found_nothing(BusinessIndustry).model_copy(update={"what_it_sells": "Petrol scooters for commuters"}),
        ValuationPlan: valuation_plan,
        **({PeerFitCheck: fit} if fit is not None else {}),
    }, models=config.models)
    return analyse_rhp(ipo or make_ipo(), rhp, adapters, config)


def stats(report, multiple):
    [m] = [m for m in report.valuation.multiples if m.multiple == multiple]
    return m


def rich_flags(report):
    return [f for f in report.flags if f.code == "VALUATION_RICH"]


@pytest.fixture
def rhp(tmp_path):
    return make_rhp(tmp_path)


def test_ipo_multiples_at_the_upper_band_are_compared_with_the_rhp_peers_median_min_and_max(rhp, adapters, config):
    report = analyse(rhp, adapters, config, valuation_plan=plan("pe", "ev_ebitda"))

    assert report.unverified_fields == []
    v = report.valuation
    # 100,000,000 shares x ₹105 = ₹1,050 crore.
    assert v.ipo_market_cap.value == pytest.approx(1050.0)
    assert [(i.field, i.page) for i in v.ipo_market_cap.inputs] == [
        ("offer_objects.post_issue_shares", OFFER), ("ipo.price_band", None)]
    pe = stats(report, "pe")
    # P/E: ₹1,050 crore / FY2024 PAT ₹35 crore = 30.0x vs peer median 25x -> 30/25 - 1 = +20%.
    assert (pe.ipo.value, pe.median.value, pe.min.value, pe.max.value) == pytest.approx((30.0, 25.0, 20.0, 30.0))
    assert pe.premium_pct.value == pytest.approx(20.0)
    assert ("financials.periods[0].pat", FIN) in [(i.field, i.page) for i in pe.ipo.inputs]
    # EV/EBITDA: (1,050 + 100 - 50) / 70 = 15.714x vs median 15x -> +4.76%.
    ev = stats(report, "ev_ebitda")
    assert ev.ipo.value == pytest.approx(1100 / 70)
    assert ev.median.value == pytest.approx(15.0)
    assert ev.premium_pct.value == pytest.approx(100 * (1100 / 70 / 15 - 1))
    # Every peer input cites its source and as-of date.
    sources = {(i.field, i.source) for i in pe.median.inputs}
    assert ("peers.ALPHA.market_cap", "NSE, as of 2026-10-09") in sources
    assert ("peers.ALPHA.pat", "Yahoo Finance, as of 2026-03-31") in sources
    assert all(i.source for i in pe.median.inputs)
    assert "Priced at 30.0× P/E vs RHP peer median 25.0× (range 20.0×–30.0×) → 20% premium." in v.read
    assert rich_flags(report) == []


def test_the_model_picks_multiples_from_the_business_and_peers_but_gives_no_numbers(rhp, adapters, config):
    analyse(rhp, adapters, config, valuation_plan=plan("pe"))

    [call] = [c for c in adapters.llm.calls if c["task"] == "peer_valuation"]
    assert call["model"] == "sol"
    assert "Petrol scooters for commuters" in call["user"]
    assert all(name in call["user"] for name in PEER_NAMES)
    assert "Fiscal 2024): profit" in call["user"]
    numeric = {float, int}
    assert not any(f.annotation in numeric for f in ValuationPlan.model_fields.values())
    assert adapters.peers.asked == PEER_NAMES


def test_a_peer_with_no_quote_is_left_out_and_reported_as_data_unavailable(rhp, adapters, config):
    peers = {**PEERS, "Gamma Motors Limited": None}  # the source has nothing for Gamma

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe"), peers=peers)

    [gamma] = [r for r in report.valuation.rhp_peers if r.name == "Gamma Motors Limited"]
    assert gamma.quote is None and gamma.unavailable.startswith("data unavailable")
    # Median of the two left, 20x and 25x = 22.5x; max 25x. 30 / 22.5 - 1 = +33.3%.
    pe = stats(report, "pe")
    assert (pe.median.value, pe.max.value) == pytest.approx((22.5, 25.0))
    assert "GAMMA" not in pe.median.basis
    assert "Data unavailable for: Gamma Motors Limited." in report.valuation.read


def test_a_peer_source_that_fails_means_that_peer_has_no_data_not_a_failed_run(rhp, adapters, config):
    peers = {**PEERS, "Beta Motors Limited": ConnectionError("NSE down")}

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe"), peers=peers)

    [beta] = [r for r in report.valuation.rhp_peers if r.name == "Beta Motors Limited"]
    assert beta.unavailable == "data unavailable: ConnectionError"
    assert stats(report, "pe").median.value == pytest.approx(25.0)  # median of 20x and 30x


def test_a_peer_missing_one_number_has_that_multiple_not_computable(rhp, adapters, config):
    beta = PEERS["Beta Motors Limited"].model_copy(update={"pat": None})

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe", "ps"),
                     peers={**PEERS, "Beta Motors Limited": beta})

    [row] = [r for r in report.valuation.rhp_peers if r.name == "Beta Motors Limited"]
    assert row.multiples["pe"].not_computable_reason == "peers.BETA.pat: not available from the source"
    assert row.multiples["ps"].value == pytest.approx(1.5)
    assert stats(report, "pe").median.value == pytest.approx(25.0)  # median of 20x and 30x


def test_a_loss_maker_has_no_pe_and_is_valued_on_price_to_sales(tmp_path, adapters, config):
    fy24 = {**FY24, "pat": -20}
    rhp = make_rhp(tmp_path, fy24=fy24)

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe", "ps"), fin=fin_answer(fy24=fy24))

    assert report.unverified_fields == []
    pe = stats(report, "pe")
    assert pe.ipo.value is None and "a loss" in pe.ipo.not_computable_reason
    assert pe.premium_pct.value is None
    # P/S: ₹1,050 crore / revenue ₹700 crore = 1.5x vs median 2.0x -> 1.5/2 - 1 = -25% (a discount).
    ps = stats(report, "ps")
    assert ps.ipo.value == pytest.approx(1.5)
    assert ps.premium_pct.value == pytest.approx(-25.0)
    assert report.valuation.headline.multiple == "ps"
    assert "→ 25% discount" in report.valuation.read
    [call] = [c for c in adapters.llm.calls if c["task"] == "peer_valuation"]
    assert "Fiscal 2024): loss" in call["user"]


def test_a_lender_is_valued_on_price_to_book_on_post_issue_net_worth_with_roe(rhp, adapters, config):
    offer = offer_objects(pre_issue_shares=PRE_SHARES, post_issue_shares=POST_SHARES)  # fresh issue is [●]

    report = analyse(rhp, adapters, config, valuation_plan=plan("pb"), offer=offer)

    assert report.unverified_fields == []
    pb = stats(report, "pb")
    # Fresh issue = (100,000,000 - 90,000,000) shares x ₹105 = ₹1,050,000,000 = ₹105 crore (at upper band).
    # Post-issue net worth = FY2024 ₹350 crore + ₹105 crore = ₹455 crore.
    # P/B = ₹1,050 crore / ₹455 crore = 2.3077x vs median 3.0x -> 2.3077/3 - 1 = -23.08%.
    assert pb.ipo.value == pytest.approx(1050 / 455)
    assert pb.median.value == pytest.approx(3.0)
    assert pb.premium_pct.value == pytest.approx(100 * (1050 / 455 / 3 - 1))
    assert pb.ipo.basis == "post-issue net worth: FY net worth + fresh issue at upper band"
    fields = [i.field for i in pb.ipo.inputs]
    assert "financials.periods[0].net_worth" in fields and "offer_objects.pre_issue_shares" in fields
    # RoE: 35 / 350 = 10% vs peer median 12.5%.
    assert report.valuation.checks["ipo_roe_pct"].value == pytest.approx(10.0)
    assert report.valuation.checks["peer_median_roe_pct"].value == pytest.approx(12.5)
    [alpha] = [r for r in report.valuation.rhp_peers if r.name == "Alpha Motors Limited"]
    assert alpha.multiples["roe_pct"].value == pytest.approx(12.5)


def test_price_to_book_falls_back_to_pre_issue_net_worth_when_the_fresh_issue_is_not_computable(rhp, adapters, config):
    # Only the post-issue count is known: no fresh issue amount or share count, so no fresh issue.
    report = analyse(rhp, adapters, config, valuation_plan=plan("pb"))

    pb = stats(report, "pb")
    # ₹1,050 crore / FY2024 net worth ₹350 crore = 3.0x.
    assert pb.ipo.value == pytest.approx(3.0)
    assert pb.ipo.basis.startswith("pre-issue net worth (fresh issue not computable:")


def test_agent_suggested_peers_are_listed_separately_with_reasons_and_not_in_the_median(rhp, adapters, config):
    peers = {**PEERS, "Epsilon Scooters Limited": quote("Epsilon Scooters Limited", "EPSILON", mcap=10000, pat=100,
                                                        revenue=500, prior=400, ebitda=150, net_worth=900)}
    suggested = ["Epsilon Scooters Limited", "Alpha Motors Ltd.", "Zeta Ltd", "Eta Ltd", "Theta Ltd"]

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe", suggested=suggested), peers=peers)

    v = report.valuation
    assert [r.name for r in v.rhp_peers] == PEER_NAMES
    # "Alpha Motors Ltd." is an RHP peer already; at most 3 suggestions are kept.
    assert [(r.name, r.origin, r.reason) for r in v.suggested_peers] == [
        ("Epsilon Scooters Limited", "agent_suggested", "Epsilon Scooters Limited also makes scooters"),
        ("Zeta Ltd", "agent_suggested", "Zeta Ltd also makes scooters"),
        ("Eta Ltd", "agent_suggested", "Eta Ltd also makes scooters")]
    [eps] = v.suggested_peers[:1]
    assert eps.multiples["pe"].value == pytest.approx(100.0)  # 10,000 / 100, shown but not in the median
    assert v.peer_set == "rhp"
    assert stats(report, "pe").median.value == pytest.approx(25.0)
    assert [r.unavailable for r in v.suggested_peers[1:]] == ["data unavailable: not found on NSE or Yahoo Finance"] * 2


def test_with_no_rhp_peers_the_agent_suggested_peers_set_the_median(rhp, adapters, config):
    peers = {"Alpha Motors Limited": PEERS["Alpha Motors Limited"], "Beta Motors Limited": PEERS["Beta Motors Limited"]}

    report = analyse(rhp, adapters, config, peer_names=[], peers=peers,
                     valuation_plan=plan("pe", suggested=["Alpha Motors Limited", "Beta Motors Limited"]))

    assert report.valuation.peer_set == "agent_suggested"
    assert stats(report, "pe").median.value == pytest.approx(22.5)  # median of 20x and 25x
    assert "agent-suggested peer" in report.valuation.read


def test_each_peer_has_its_own_growth_margins_and_roe_with_sources(rhp, adapters, config):
    report = analyse(rhp, adapters, config, valuation_plan=plan("pe"))

    [alpha] = [r for r in report.valuation.rhp_peers if r.name == "Alpha Motors Limited"]
    # Alpha: revenue 1,000 vs 900 -> +11.11%; EBITDA 210 / 1,000 = 21%; PAT 100 / 1,000 = 10%; RoE 100 / 800 = 12.5%.
    assert {k: d.value for k, d in alpha.ratios.items()} == pytest.approx(
        {"revenue_growth_pct": 100 * (1000 / 900 - 1), "ebitda_margin_pct": 21.0, "pat_margin_pct": 10.0,
         "roe_pct": 12.5})
    assert alpha.ratios["ebitda_margin_pct"].basis == "ALPHA: EBITDA / revenue"
    assert all(i.source == "Yahoo Finance, as of 2026-03-31" for d in alpha.ratios.values() for i in d.inputs)
    # The IPO beside them, FY2024: revenue 700 vs 500 -> +40%; EBITDA 70 / 700 = 10%; PAT 35 / 700 = 5%; RoE 35 / 350 = 10%.
    v = report.valuation
    assert v.ipo_year == "Fiscal 2024"
    assert {k: d.value for k, d in v.ipo_figures.items()} == pytest.approx(
        {"revenue_from_operations": 700.0, "ebitda": 70.0, "pat": 35.0, "net_worth": 350.0})
    assert {k: d.value for k, d in v.ipo_ratios.items()} == pytest.approx(
        {"revenue_growth_pct": 40.0, "ebitda_margin_pct": 10.0, "pat_margin_pct": 5.0, "roe_pct": 10.0})
    assert v.ipo_figures["pat"].pages == [FIN]


def test_a_peer_ratio_missing_a_figure_is_not_computable(rhp, adapters, config):
    beta = PEERS["Beta Motors Limited"].model_copy(update={"ebitda": None})

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe"), peers={**PEERS, "Beta Motors Limited": beta})

    [row] = [r for r in report.valuation.rhp_peers if r.name == "Beta Motors Limited"]
    assert row.ratios["ebitda_margin_pct"].value is None
    assert row.ratios["ebitda_margin_pct"].not_computable_reason == "peers.BETA.ebitda: not available from the source"
    assert row.ratios["pat_margin_pct"].value == pytest.approx(6.0)


def fit(name, verdict, common=("scooters",), differs=()):
    return PeerFit(name=name, verdict=verdict, commonalities=list(common), differences=list(differs))


def test_each_peer_is_checked_against_the_business_with_its_commonalities(rhp, adapters, config):
    peers = {**PEERS, "Alpha Motors Limited": PEERS["Alpha Motors Limited"].model_copy(update={
        "sector": "Consumer Cyclical", "industry": "Auto Manufacturers",
        "business_summary": "Alpha makes two-wheelers sold through dealers across India."})}
    check = PeerFitCheck(peers=[
        fit("Alpha Motors Ltd.", "strong", ["petrol scooters", "Indian commuters", "has 3 plants"], ["larger"]),
        fit("Beta Motors Limited", "partial", ["two-wheelers"], ["mostly motorcycles"]),
        fit("Gamma Motors Limited", "weak", ["vehicles"], ["trucks", "fleet buyers"]),
    ])

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe"), peers=peers, fit=check)

    [call] = [c for c in adapters.llm.calls if c["task"] == "peer_fit"]
    assert call["model"] == "terra"
    assert "Petrol scooters for commuters" in call["user"]
    assert "Alpha makes two-wheelers sold through dealers across India." in call["user"]
    assert "industry Auto Manufacturers" in call["user"]
    rows = {r.name: r.fit for r in report.valuation.rhp_peers}
    # Matched by name as sources spell it; a phrase with a number in it is dropped.
    assert rows["Alpha Motors Limited"].verdict == "strong"
    assert rows["Alpha Motors Limited"].commonalities == ["petrol scooters", "Indian commuters"]
    assert rows["Gamma Motors Limited"].differences == ["trucks", "fleet buyers"]
    assert "Peer fit: 1 strong, 1 partial, 1 weak (weak: Gamma Motors Limited)." in report.valuation.read
    # A weak peer stays in the median: 20x, 25x, 30x -> 25x.
    assert stats(report, "pe").median.value == pytest.approx(25.0)
    assert [f for f in report.flags if f.code == "PEER_SET_WEAK"] == []


def test_a_mostly_weak_peer_set_is_flagged_but_kept_in_the_median(rhp, adapters, config):
    check = PeerFitCheck(peers=[fit("Alpha Motors Limited", "weak"), fit("Beta Motors Limited", "weak"),
                                fit("Gamma Motors Limited", "strong")])

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe"), fit=check)

    [flag] = [f for f in report.flags if f.code == "PEER_SET_WEAK"]
    assert (flag.severity, flag.section) == ("info", "peer_valuation")
    assert "2 of the 3 peers" in flag.message
    assert stats(report, "pe").median.value == pytest.approx(25.0)


def test_a_failed_peer_fit_check_leaves_the_valuation_without_verdicts(rhp, adapters, config):
    report = analyse(rhp, adapters, config, valuation_plan=plan("pe"), fit=RuntimeError("model down"))

    assert all(r.fit is None for r in report.valuation.rhp_peers)
    assert stats(report, "pe").median.value == pytest.approx(25.0)
    assert "Peer fit" not in report.valuation.read


# --- VALUATION_RICH -----------------------------------------------------------------------

def test_a_rich_premium_without_growth_or_margin_advantage_is_a_warning(tmp_path, adapters, config):
    # PAT ₹30 crore: P/E 1,050/30 = 35x vs median 25x -> +40% (> 30%). Revenue growth 700/680 - 1 = 2.9% vs
    # peer median 20%; PAT margin 30/700 = 4.3% vs 8.33%: no advantage.
    fy24, fy23 = {**FY24, "pat": 30}, {**FY23, "revenue_from_operations": 680}
    rhp = make_rhp(tmp_path, fy24=fy24, fy23=fy23)

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe"), fin=fin_answer(fy24, fy23))

    assert report.unverified_fields == []
    assert stats(report, "pe").premium_pct.value == pytest.approx(40.0)
    [flag] = rich_flags(report)
    assert (flag.severity, flag.section) == ("warning", "peer_valuation")
    assert "40% premium" in flag.message
    assert "revenue growth 2.9% vs peer median 20.0%" in flag.message
    assert "PAT margin 4.3% vs 8.3%" in flag.message
    assert FIN in flag.evidence_pages


def test_a_rich_premium_backed_by_faster_growth_is_quiet(tmp_path, adapters, config):
    # P/E 35x (+40%), but revenue growth 700/500 - 1 = 40% beats the peer median 20%.
    fy24 = {**FY24, "pat": 30}
    rhp = make_rhp(tmp_path, fy24=fy24)

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe"), fin=fin_answer(fy24))

    assert report.valuation.checks["ipo_revenue_growth_pct"].value == pytest.approx(40.0)
    assert report.valuation.checks["peer_median_revenue_growth_pct"].value == pytest.approx(20.0)
    assert rich_flags(report) == []


def test_a_rich_premium_whose_growth_cannot_be_computed_is_info(tmp_path, adapters, config):
    # One fiscal year only: growth not computable; margin 4.3% < 8.33% shows no advantage either.
    fy24 = {**FY24, "pat": 30}
    rhp = make_rhp(tmp_path, fy24=fy24, fy23=None)

    report = analyse(rhp, adapters, config, valuation_plan=plan("pe"), fin=fin_answer(fy24, None))

    [flag] = rich_flags(report)
    assert flag.severity == "info"
    assert "needs 2 full fiscal years" in flag.message


def test_without_a_peer_data_source_no_valuation_is_run(rhp, adapters, config):
    adapters.llm = ScriptedLLM({}, models=config.models)

    report = analyse_rhp(make_ipo(), rhp, adapters, config)

    assert report.valuation is None
    assert not any(c["task"] == "peer_valuation" for c in adapters.llm.calls)


# --- seam A: the Telegram summary line ----------------------------------------------------

def test_the_summary_shows_the_ipo_multiple_against_the_peer_median(rhp, adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.downloader = FakeDownloader(rhp)
    adapters.peers = FakePeerData(dict(PEERS))
    adapters.llm = ScriptedLLM({
        OfferObjects: offer_objects(post_issue_shares=POST_SHARES), Financials: fin_answer(),
        OfferPriceBasis: found_nothing(OfferPriceBasis).model_copy(update={"peers": [peer(n) for n in PEER_NAMES]}),
        ValuationPlan: plan("pe"),
    }, models=config.models)

    run_daily(make_ipo().open_date, adapters, config)

    [message] = adapters.notifier.messages
    assert "Valuation: P/E 30.0× vs peer median 25.0× (20% premium)" in message.splitlines()


# --- golden RHPs: the IPO side, worked by hand ------------------------------------------------

def golden_fin(slug, *fields):
    """FY2024 facts as expected.yaml has them, cited on the financials pages (test_financials' citations)."""
    keys = {f"{name}_fy2024": name for name in fields}
    facts = {}
    for f in _expected(slug)["facts"]:
        if f["key"] in keys:
            page, quote_ = FINANCIALS_CITATIONS[(slug, f["key"])]
            facts[keys[f["key"]]] = Fact(value=f["value"], unit=f["unit"], page=page, verbatim_quote=quote_)
    return financials([period("Fiscal 2024", "March 31, 2024", **facts)])


def analyse_golden(slug, band, offer, fin, chosen, golden_rhp, golden_doc, adapters, config, monkeypatch):
    """No peer data (the golden checks are the IPO side); the scripted plan picks `chosen` multiples."""
    monkeypatch.setattr(analysis, "ingest", lambda path: golden_doc(slug))  # ingested once per session
    adapters.peers = FakePeerData({})
    adapters.llm = ScriptedLLM({OfferObjects: offer, Financials: fin, ValuationPlan: plan(*chosen)},
                               models=config.models)
    return analyse_rhp(make_ipo().model_copy(update={"price_band": band}), golden_rhp(slug), adapters, config)


@pytest.mark.sections("offer_objects", "financials")
def test_hyundai_market_cap_and_pe_at_the_upper_band(golden_rhp, golden_doc, adapters, config, monkeypatch):
    slug = "hyundai-motor-india"
    facts = offer_golden_facts(golden_rhp, slug)
    report = analyse_golden(slug, "₹1,865–₹1,960", offer_objects(post_issue_shares=facts["post_issue_shares"]),
                            golden_fin(slug, "pat"), ("pe",), golden_rhp, golden_doc, adapters, config, monkeypatch)

    assert report.unverified_fields == []
    # 812,541,100 post-issue shares x ₹1,960 = ₹1,592,580,556,000 = ₹159,258.06 crore.
    assert report.valuation.ipo_market_cap.value == pytest.approx(159258.06, abs=0.01)
    # FY2024 PAT ₹60,600.44 million = ₹6,060.04 crore; 159,258.06 / 6,060.04 = 26.28x
    # (the same as ₹1,960 / FY2024 basic EPS ₹74.58 = 26.28x).
    assert stats(report, "pe").ipo.value == pytest.approx(26.28, abs=0.005)


@pytest.mark.sections("offer_objects", "financials")
def test_bajaj_housing_finance_price_to_book_at_the_upper_band(golden_rhp, golden_doc, adapters, config, monkeypatch):
    slug = "bajaj-housing-finance"
    facts = offer_golden_facts(golden_rhp, slug)
    offer = offer_objects(pre_issue_shares=facts["pre_issue_shares"], fresh_issue=facts["fresh_issue"])
    report = analyse_golden(slug, "₹66–₹70", offer, golden_fin(slug, "net_worth"), ("pb",),
                            golden_rhp, golden_doc, adapters, config, monkeypatch)

    assert report.unverified_fields == []
    # Post-issue shares = 7,819,575,273 + ₹35,600 million / ₹70 = 7,819,575,273 + 508,571,428.6 shares;
    # x ₹70 = ₹547,370,269,110 + ₹35,600,000,000 = ₹582,970,269,110 = ₹58,297.03 crore.
    assert report.valuation.ipo_market_cap.value == pytest.approx(58297.03, abs=0.01)
    # Post-issue net worth = FY2024 net worth ₹122,335.0 million (₹12,233.5 crore) + fresh issue ₹35,600
    # million (₹3,560.0 crore, stated in the RHP) = ₹15,793.5 crore.
    # P/B = 58,297.03 / 15,793.5 = 3.6912x (on pre-issue net worth it would be 58,297.03 / 12,233.5 = 4.765x).
    pb = stats(report, "pb").ipo
    assert pb.value == pytest.approx(3.6912, abs=0.0005)
    assert pb.basis == "post-issue net worth: FY net worth + fresh issue as stated in the RHP"


@pytest.mark.sections("offer_objects", "financials")
def test_ola_electric_a_loss_maker_has_no_pe_and_a_price_to_sales(golden_rhp, golden_doc, adapters, config,
                                                                   monkeypatch):
    slug = "ola-electric"
    facts = offer_golden_facts(golden_rhp, slug)
    offer = offer_objects(pre_issue_shares=facts["pre_issue_shares"], fresh_issue=facts["fresh_issue"])
    report = analyse_golden(slug, "₹72–₹76", offer, golden_fin(slug, "pat", "revenue_from_operations"), ("pe", "ps"),
                            golden_rhp, golden_doc, adapters, config, monkeypatch)

    assert report.unverified_fields == []
    # 3,687,072,258 + ₹55,000 million / ₹76 shares, x ₹76 = ₹280,217,491,608 + ₹55,000,000,000
    # = ₹335,217,491,608 = ₹33,521.75 crore.
    assert report.valuation.ipo_market_cap.value == pytest.approx(33521.75, abs=0.01)
    assert "a loss" in stats(report, "pe").ipo.not_computable_reason  # FY2024 loss ₹15,844.00 million
    # FY2024 revenue ₹50,098.31 million = ₹5,009.83 crore; 33,521.75 / 5,009.83 = 6.691x.
    assert stats(report, "ps").ipo.value == pytest.approx(6.691, abs=0.0005)


def test_the_summary_lists_each_peer_with_its_numbers_verdict_and_what_it_shares(rhp, adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.downloader = FakeDownloader(rhp)
    adapters.peers = FakePeerData({**PEERS, "Gamma Motors Limited": None})
    adapters.llm = ScriptedLLM({
        OfferObjects: offer_objects(post_issue_shares=POST_SHARES), Financials: fin_answer(),
        OfferPriceBasis: found_nothing(OfferPriceBasis).model_copy(update={"peers": [peer(n) for n in PEER_NAMES]}),
        ValuationPlan: plan("pe"),
        PeerFitCheck: PeerFitCheck(peers=[fit("Alpha Motors Limited", "strong", ["petrol scooters", "commuters"]),
                                          fit("Beta Motors Limited", "weak", ["vehicles"])]),
    }, models=config.models)

    run_daily(make_ipo().open_date, adapters, config)

    [message] = adapters.notifier.messages
    lines = message.splitlines()
    peers = lines[lines.index("Peers:") + 1:]
    # Alpha: P/E 2,000 / 100 = 20x; revenue ₹1,000 crore, +11% on ₹900 crore; PAT margin 100 / 1,000 = 10%.
    assert peers[0] == ("• Alpha Motors Limited (ALPHA) · ✅ strong peer · P/E 20× · Rev ₹1,000 cr (+11%) · "
                        "PAT margin 10.0% · common: petrol scooters, commuters")
    assert peers[1].startswith("• Beta Motors Limited (BETA) · ⚠️ weak peer · P/E 25×")
    assert peers[2] == "• No market data: Gamma Motors Limited"



# --- business-specific multiples from the RHP's KPI tables -----------------------------------

def kpi_fact(value, quote, unit):
    return Fact(value=value, unit=unit, page=BASIS, verbatim_quote=quote)


IPO_KPIS = [
    Kpi(name="Number of subscribers (1)", values=[
        PeriodFact(period="Three months ended June 30, 2024", value=kpi_fact(0.70, "three months ended June 30, 2024 (million) 0.70", "million subscribers")),
        PeriodFact(period="Fiscal 2024", value=kpi_fact(2.50, "Number of subscribers (million) Fiscal 2024 2.50",
                                                        "million subscribers"))]),
    Kpi(name="ARPU", values=[PeriodFact(period="Fiscal 2024", value=kpi_fact(210.00, "ARPU (Rs.) Fiscal 2024 210.00", "₹"))]),
    Kpi(name="Attrition", values=[PeriodFact(period="Fiscal 2024", value=kpi_fact(30.00, "Attrition Fiscal 2024 30.00%", "%"))]),
]


def peer_kpi(company, kpi, value, quote, unit):
    return PeerKpi(company=company, kpi=kpi, values=[PeriodFact(period="Fiscal 2024", value=kpi_fact(value, quote, unit))])


PEER_KPIS = [  # the KPI table names the peers without "Limited"; Gamma is not in it
    peer_kpi("Alpha Motors", "Number of subscribers", 4.00, "Alpha Motors Number of subscribers (million) Fiscal 2024 4.00",
             "million subscribers"),
    peer_kpi("Beta Motors", "Number of subscribers", 6.00, "Beta Motors Number of subscribers (million) Fiscal 2024 6.00",
             "million subscribers"),
    peer_kpi("Alpha Motors", "ARPU", 190.00, "Fiscal 2024 4.00 ARPU (Rs.) 190.00", "₹"),
    peer_kpi("Beta Motors", "ARPU", 230.00, "Fiscal 2024 6.00 ARPU (Rs.) 230.00", "₹"),
]
EV_PER_SUBSCRIBER = MultipleChoice(multiple="ev_per_subscriber", label="EV per subscriber", numerator="enterprise_value",
                                   kpi="Number of subscribers", reason="subscriber businesses are valued per subscriber")


def kpi_basis(kpis=IPO_KPIS, peer_kpis=PEER_KPIS):
    return found_nothing(OfferPriceBasis).model_copy(update={
        "peers": [peer(n) for n in PEER_NAMES], "kpis": kpis, "peer_kpis": peer_kpis})


def analyse_kpis(rhp, adapters, config, *choices, operating=(), basis=None):
    adapters.peers = FakePeerData(dict(PEERS))
    adapters.llm = ScriptedLLM({
        OfferObjects: offer_objects(post_issue_shares=POST_SHARES), Financials: fin_answer(),
        OfferPriceBasis: basis or kpi_basis(),
        ValuationPlan: ValuationPlan(multiples_to_use=list(choices), additional_peers=[],
                                     operating_metrics=list(operating)),
    }, models=config.models)
    return analyse_rhp(make_ipo(), rhp, adapters, config)


def test_the_model_can_value_on_a_business_specific_multiple_from_the_rhp_kpis(rhp, adapters, config):
    report = analyse_kpis(rhp, adapters, config, EV_PER_SUBSCRIBER, MultipleChoice(multiple="pe", reason="earner"))

    assert report.unverified_fields == []
    m = stats(report, "kpi_ev_per_subscriber")
    assert (m.title, m.unit, m.kpi) == ("EV per subscriber", "₹", "Number of subscribers")
    # IPO EV = 1,050 + 100 - 50 = ₹1,100 crore over Fiscal 2024's 2.5 million subscribers (the June quarter
    # is a stub, never used): ₹11,000,000,000 / 2,500,000 = ₹4,400 per subscriber.
    assert m.ipo.value == pytest.approx(4400.0)
    assert ("price_basis.kpis[0].Fiscal 2024", BASIS) in [(i.field, i.page) for i in m.ipo.inputs]
    # Alpha (2,000 + 200 - 100) / 4.0 million = ₹5,250; Beta 3,000 / 6.0 million = ₹5,000; Gamma has no KPI.
    # Median ₹5,125, so 4,400 / 5,125 - 1 = -14.15% (a discount).
    assert (m.median.value, m.min.value, m.max.value) == pytest.approx((5125.0, 5000.0, 5250.0))
    assert m.premium_pct.value == pytest.approx(100 * (4400 / 5125 - 1))
    [gamma] = [r for r in report.valuation.rhp_peers if r.name == "Gamma Motors Limited"]
    assert "not disclosed for Gamma Motors Limited" in gamma.multiples["kpi_ev_per_subscriber"].not_computable_reason
    assert stats(report, "pe").median.value == pytest.approx(25.0)  # the standard multiple still runs
    assert "Priced at ₹4,400 EV per subscriber vs RHP peer median ₹5,125" in report.valuation.read


def test_the_model_is_shown_the_kpis_the_rhp_discloses_for_the_company_and_its_peers(rhp, adapters, config):
    analyse_kpis(rhp, adapters, config, EV_PER_SUBSCRIBER)

    [call] = [c for c in adapters.llm.calls if c["task"] == "peer_valuation"]
    assert "KPIs the RHP discloses for the company: Number of subscribers (1); ARPU; Attrition" in call["user"]
    assert "KPIs the RHP discloses for its listed peers (number of peers): Number of subscribers (2); ARPU (2)" in call["user"]
    assert "EV per subscriber" in call["system"]  # examples, not a whitelist


def test_a_kpi_the_rhp_does_not_have_leaves_that_multiple_not_computable(rhp, adapters, config):
    beds = MultipleChoice(multiple="ev_per_bed", label="EV per bed", numerator="enterprise_value", kpi="Beds",
                          reason="hospitals")
    report = analyse_kpis(rhp, adapters, config, beds, MultipleChoice(multiple="pe", reason="earner"))

    m = stats(report, "kpi_ev_per_bed")
    assert m.ipo.not_computable_reason == "KPI 'Beds' is not among the company's KPIs in the RHP"
    assert m.premium_pct.value is None
    assert report.valuation.headline.multiple == "pe"


def test_a_rate_is_never_divided_by(rhp, adapters, config):
    per_attrition = MultipleChoice(multiple="per_attrition", numerator="market_cap", kpi="Attrition", reason="x")
    report = analyse_kpis(rhp, adapters, config, per_attrition)

    assert "'%' is a rate, not an amount" in stats(report, "kpi_per_attrition").ipo.not_computable_reason


def test_a_made_up_multiple_with_no_kpi_is_dropped(rhp, adapters, config):
    vague = MultipleChoice(multiple="ev_per_vibes", label="EV per vibe", reason="x")
    report = analyse_kpis(rhp, adapters, config, vague, MultipleChoice(multiple="ps", reason="sales"))

    assert [m.multiple for m in report.valuation.multiples] == ["ps"]


def test_operating_kpis_are_compared_with_the_peer_median(rhp, adapters, config):
    report = analyse_kpis(rhp, adapters, config, EV_PER_SUBSCRIBER, operating=["ARPU", "Number of subscribers", "Beds"])

    arpu, subs = report.valuation.operating  # "Beds" is in no KPI table: left out
    assert arpu.ipo.value == 210.00 and arpu.ipo_period == "Fiscal 2024"
    assert {p: f.value for p, f in arpu.peers.items()} == {"Alpha Motors Limited": 190.00, "Beta Motors Limited": 230.00}
    # Median of ₹190 and ₹230 = ₹210, held in ₹ crore (₹210 / 10,000,000).
    assert (arpu.median.value, arpu.median.unit) == (pytest.approx(210 / 10_000_000), "₹ crore")
    assert subs.median.value == pytest.approx(5.0) and subs.median.unit == "million subscribers"


def test_the_summary_has_an_operating_line(rhp, adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.downloader = FakeDownloader(rhp)
    adapters.peers = FakePeerData(dict(PEERS))
    adapters.llm = ScriptedLLM({
        OfferObjects: offer_objects(post_issue_shares=POST_SHARES), Financials: fin_answer(),
        OfferPriceBasis: kpi_basis(),
        ValuationPlan: ValuationPlan(multiples_to_use=[EV_PER_SUBSCRIBER], additional_peers=[], operating_metrics=["ARPU"]),
    }, models=config.models)

    run_daily(make_ipo().open_date, adapters, config)

    [message] = adapters.notifier.messages
    assert "Operating: ARPU ₹210 vs peer median ₹210" in message.splitlines()
    assert "Valuation: EV per subscriber ₹4,400 vs peer median ₹5,125 (14% discount)" in message.splitlines()


def test_an_operating_kpi_no_peer_discloses_is_left_out_and_footnote_marks_are_dropped(rhp, adapters, config):
    report = analyse_kpis(rhp, adapters, config, EV_PER_SUBSCRIBER, operating=["Attrition", "Number of subscribers (1)"])

    assert [m.name for m in report.valuation.operating] == ["Number of subscribers"]
