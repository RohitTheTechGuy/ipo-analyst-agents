"""A fully populated Report for renderer tests and docs/sample_report.pdf (ticket 22).

Numbers are chosen so their display form is known by hand: ₹5,000 million is "₹500 cr"; 1,25,000
OFS shares print with lakh grouping. Derived numbers come from calc.py, as analyse_rhp builds them.
"""
from __future__ import annotations

from datetime import date

from ipo_agents.calc import compute_offer
from ipo_agents.contracts import (AuditorRemark, BusinessIndustry, CalcInput, CapacityLine, CheckedAdvice, CheckedSynthesis,
                                  CitedText, ClaimedFact, Dependence, Derived, Dividend, Eps, EvidencedPoint, Fact,
                                  FinancialCheck, FinancialPeriod, Financials, FinePrintLegal, Flag, GroupCompany,
                                  IndustryClaim, IndustryPe, Kpi, LitigationLine, MaterialItem, MultipleStats,
                                  OfferObjects, OfferPriceBasis, OtherDisclosures, Peer, PeerQuote, PeerRow,
                                  PendingApproval, PeriodFact, ProceedsObject, Promoter, PromotersGovernance, Quote,
                                  RelatedPartyLine, Remuneration, Report, Restatement, RevenueShare, RiskFactor,
                                  RiskFactors, SectionSummary, SellingShareholder, Sentiment, SentimentSource,
                                  SubscriptionStatus, Valuation, Waca)
from ipo_agents.section_calc import compute_sections
from ipo_agents.verifier import COULD_NOT_VERIFY
from tests.fakes import make_ipo

INJECTION = "<script>alert('pwned')</script> & <b>not bold</b>"
AS_OF = date(2026, 10, 9)


def f(value, unit, page, quote="as printed"):
    return Fact(value=value, unit=unit, page=page, verbatim_quote=quote)


def mn(value, page):
    return f(value, "₹ million", page)


def pct(value, page):
    return f(value, "%", page)


UNVERIFIED = Fact(value=None, unit="%", page=None, verbatim_quote=None, not_disclosed_reason=COULD_NOT_VERIFY)
NOT_DISCLOSED = Fact(value=None, unit="₹ million", page=None, verbatim_quote=None,
                     not_disclosed_reason="not disclosed in provided pages")


def claimed(value, unit, source="NSE", label=None, url="https://www.nseindia.com/quote"):
    return ClaimedFact(value=value, unit=unit, source_url=url, source_name=source, as_of=AS_OF, label=label)


def offer() -> OfferObjects:
    return OfferObjects(
        issue_size=mn(5000, 19), fresh_issue=mn(2000, 19), ofs_amount=mn(3000, 19),
        ofs_shares=f(125000, "equity shares", 20), pre_issue_shares=f(40_000_000, "equity shares", 21),
        post_issue_shares=f(59_047_619, "equity shares", 21),
        promoter_holding_pre_pct=pct(78.5, 88), promoter_holding_post_pct=pct(61.2, 88),
        objects=[ProceedsObject(purpose="Repayment of borrowings", amount=mn(1200, 95)),
                 ProceedsObject(purpose="New plant at Pune", amount=mn(450, 95))],
        gcp_amount=mn(350, 95), anchor_pct=UNVERIFIED, qib_pct=pct(50, 22), nii_pct=pct(15, 22),
        retail_pct=pct(35, 22),
        selling_shareholders=[
            SellingShareholder(name="Rakesh Sharma", category="promoter", shares_offered=f(75000, "equity shares", 20),
                               holding_after=pct(40.1, 20)),
            SellingShareholder(name="Bluewater PE Fund I", category="investor",
                               shares_offered=f(50000, "equity shares", 20), holding_after=pct(0, 20)),
        ])


def period(label, end, rev, ebitda, pat, cfo, kind="full_year"):
    return FinancialPeriod(
        label=label, end_date=end, kind=kind, revenue_from_operations=mn(rev, 287), total_income=mn(rev * 1.02, 287),
        ebitda=mn(ebitda, 289), pat=mn(pat, 287), cfo=mn(cfo, 291), capex=mn(-310, 291),
        total_borrowings=mn(1800, 288), cash_and_equivalents=mn(240, 288), net_worth=mn(5200, 288),
        trade_receivables=mn(900, 288), inventories=mn(700, 288), trade_payables=mn(650, 288),
        ronw_pct=pct(14.2, 292), roce_pct=NOT_DISCLOSED.model_copy(update={"unit": "%"}))


def financials() -> Financials:
    return Financials(
        periods=[period("Fiscal 2026", "March 31, 2026", 4126, 780, 412.6, 120),
                 period("Fiscal 2025", "March 31, 2025", 3480, 610, 330, 290),
                 period("Fiscal 2024", "March 31, 2024", 2950, 500, 260, 310)],
        auditor_remarks=[AuditorRemark(kind="emphasis_of_matter", summary="Pending GST demand on the Pune unit",
                                       source=Quote(page=301, verbatim_quote="we draw attention to Note 42"))],
        restatement=Restatement(adjusted=False, source=Quote(page=300, verbatim_quote="no restatement adjustments")))


def risks() -> RiskFactors:
    return RiskFactors(risks=[
        RiskFactor(topic="single plant", title=Quote(page=31, verbatim_quote="All our manufacturing is at one plant in Pune."),
                   kind="company_specific", quantified=False, severity_rank=1),
        RiskFactor(topic="customer concentration", title=Quote(page=32, verbatim_quote=INJECTION),
                   kind="company_specific", quantified=True, figure=pct(38.4, 32), severity_rank=2),
        RiskFactor(topic="economy", title=Quote(page=60, verbatim_quote="A slowdown in India may hurt us."),
                   kind="generic", quantified=False),
    ])


def legal() -> FinePrintLegal:
    return FinePrintLegal(
        litigation=[LitigationLine(party="company", case_type="tax", direction="against", count=f(7, "cases", 410),
                                   amount=mn(340, 410))],
        contingent_liabilities=mn(820, 330),
        related_party=[RelatedPartyLine(category="purchases", counterparty="Sharma Castings Pvt Ltd", promoter_entity=True,
                                        period="Fiscal 2026", amount=mn(210, 335))],
        approvals=[PendingApproval(name="Consent to operate, Pune plant", status="pending", critical=True,
                                   source=Quote(page=420, verbatim_quote="application for renewal is pending"))],
        material_contracts=[])


def business() -> BusinessIndustry:
    return BusinessIndustry(
        what_it_sells="Aluminium die-cast parts for two-wheeler makers in India.",
        revenue_mix=[RevenueShare(name="Two-wheeler OEMs", basis="segment", share=pct(72, 150)),
                     RevenueShare(name="Exports", basis="geography", share=pct(11, 151))],
        top1_customer_pct=pct(38.4, 152), top10_customers_pct=pct(81, 152), top_supplier_pct=pct(22, 153),
        capacity=[CapacityLine(facility="Pune plant", installed_capacity=f(24000, "tonnes per annum", 160),
                               utilisation_pct=pct(78, 160))],
        order_book=mn(6200, 155),
        competitors=[CitedText(text="Sundaram Clayton", source=Quote(page=140, verbatim_quote="Sundaram Clayton"))],
        moats=[CitedText(text="Long relationships with OEMs", source=Quote(page=148, verbatim_quote="over 20 years"))],
        industry_claims=[IndustryClaim(claim="Indian die-casting market grows 11% a year to FY30",
                                       source=Quote(page=120, verbatim_quote="CAGR of 11%"), figure=pct(11, 120))],
        single_plant=Dependence(dependent=True, evidence=Quote(page=31, verbatim_quote="one plant in Pune")),
        single_geography=Dependence(dependent=False, evidence=None))


def governance() -> PromotersGovernance:
    return PromotersGovernance(
        promoters=[Promoter(name="Rakesh Sharma", background="Founder; 30 years in auto components.",
                            source=Quote(page=200, verbatim_quote="Rakesh Sharma is the founder"))],
        group_companies=[GroupCompany(name="Sharma Castings Pvt Ltd", same_line_of_business=True,
                                      source=Quote(page=230, verbatim_quote="engaged in a similar line of business"))],
        subsidiaries=[],
        remuneration=[Remuneration(name="Rakesh Sharma", role="Managing Director", is_promoter=True,
                                   amount=f(4.55, "₹ million", 210))],
        board_size=f(6, "directors", 205), independent_directors=f(3, "directors", 205),
        promoter_shares_pledged=f(0, "equity shares", 89),
        dividends=[Dividend(period="Fiscal 2026", per_share=f(2.5, "₹", 250))],
        auditor_changes=[], kmp_changes=[])


def price_basis() -> OfferPriceBasis:
    return OfferPriceBasis(
        eps=[Eps(period="Fiscal 2026", basic=f(10.32, "₹", 110), diluted=f(10.30, "₹", 110))],
        nav_per_share=[PeriodFact(period="Fiscal 2026", value=f(130, "₹", 110))],
        ronw_pct=[PeriodFact(period="Fiscal 2026", value=pct(14.2, 110))],
        pe_at_floor=f(9.7, "times", 111), pe_at_cap=f(10.2, "times", 111),
        industry_pe=IndustryPe(highest=f(48, "times", 111), lowest=f(22, "times", 111), average=f(33, "times", 111)),
        peers=[Peer(name="Endurance Technologies", pe=f(42, "times", 112), eps=f(48.1, "₹", 112),
                    ronw_pct=pct(15, 112), nav_per_share=f(320, "₹", 112), revenue=mn(102000, 112))],
        kpis=[Kpi(name="EBITDA margin", values=[PeriodFact(period="Fiscal 2026", value=pct(18.9, 113))])],
        waca_primary=Waca(waca=f(35, "₹", 115), floor_price_times=NOT_DISCLOSED, cap_price_times=NOT_DISCLOSED),
        waca_secondary=Waca(waca=f(80, "₹", 115), floor_price_times=NOT_DISCLOSED, cap_price_times=NOT_DISCLOSED))


def d(value, unit, *inputs: CalcInput, basis=""):
    return Derived(value=value, unit=unit, inputs=list(inputs), basis=basis)


def valuation() -> Valuation:
    mcap = d(620, "₹ crore", CalcInput(field="offer_objects.post_issue_shares", page=21), CalcInput(field="ipo.price_band"))
    ipo_pe = d(15.03, "times", CalcInput(field="offer_objects.post_issue_shares", page=21),
               CalcInput(field="ipo.price_band"), CalcInput(field="financials.pat", page=287))
    nse = "NSE, as of 2026-10-09"

    def peer(name, symbol, pe):
        quote = PeerQuote(name=name, symbol=symbol, price=claimed(1850, "₹"), market_cap=claimed(26000, "₹ crore"))
        return PeerRow(name=name, origin="rhp", quote=quote,
                       multiples={"pe": d(pe, "times", CalcInput(field=f"{symbol}.market_cap", source=nse),
                                          CalcInput(field=f"{symbol}.pat", source=nse))})

    peers = [peer("Endurance Technologies", "ENDURANCE", 42.0), peer("Sundram Fasteners", "SUNDRMFAST", 38.0),
             peer("Rico Auto", "RICOAUTO", 21.0)]
    return Valuation(
        ipo_market_cap=mcap,
        multiples=[MultipleStats(multiple="pe", reason="steady earner", ipo=ipo_pe,
                                 median=d(38.0, "times", CalcInput(field="peers.pe", source=nse)),
                                 min=d(21.0, "times", CalcInput(field="peers.pe", source=nse)),
                                 max=d(42.0, "times", CalcInput(field="peers.pe", source=nse)),
                                 premium_pct=d(-60.4, "%", CalcInput(field="peers.pe", source=nse)))],
        rhp_peers=peers,
        suggested_peers=[PeerRow(name="Craftsman Automation", origin="agent_suggested", reason="also die-casts",
                                 unavailable="data unavailable: no quote")],
        read="At the upper band the IPO trades at 15× earnings, a 60% discount to the peer median of 38×.")


def sentiment() -> Sentiment:
    blog = "https://example.test/acme-ipo-review"
    return Sentiment(
        available=True,
        sources=[SentimentSource(url=blog, title="Acme IPO review: cheap, but one plant", source_name="Moneyblog",
                                 published=date(2026, 10, 10), stance="subscribe", key_reasons=["cheap vs peers"],
                                 claimed_numbers=[claimed(18, "times", "Moneyblog", "P/E", blog)]),
                 SentimentSource(url="javascript:alert(1)", title="Broker note", source_name="Broker X",
                                 published=date(2026, 10, 11), stance="listing_gains_only")],
        tally={"subscribe": 1, "listing_gains_only": 1, "avoid": 0, "neutral": 0},
        bull_points=["Valuation well below listed peers"], bear_points=["One plant makes all output"],
        gmp=claimed(42, "₹", "IPO Watch", "GMP", "https://ipowatch.example/gmp"),
        subscription=SubscriptionStatus(total=claimed(3.4, "times", "NSE", "subscription"),
                                        retail=claimed(5.1, "times", "NSE", "subscription retail")))


def synthesis() -> CheckedSynthesis:
    return CheckedSynthesis(
        verdict="subscribe_listing_gains", confidence="medium",
        reasons=[EvidencedPoint(text="P/E of 15× is a deep discount to the peer median of 38×.", evidence=["D:pe"]),
                 EvidencedPoint(text="Revenue grew from ₹295 cr to ₹412.6 cr in two years.", evidence=["F:rev"]),
                 EvidencedPoint(text="60% of the issue is an offer for sale.", evidence=["D:ofs_pct"])],
        top_red_flags=[EvidencedPoint(text="Cash from operations fell to ₹12 cr against PAT of ₹41.3 cr.", evidence=["G:x"]),
                       EvidencedPoint(text="All manufacturing is at one plant in Pune.", evidence=["G:y"]),
                       EvidencedPoint(text="Largest customer is 38.4% of revenue.", evidence=["G:z"]),
                       EvidencedPoint(text="A fourth flag that page 1 leaves out.", evidence=["G:w"])],
        top_positives=[EvidencedPoint(text="RoNW of 14.2% with low debt.", evidence=["F:ronw"])],
        section_summaries=[SectionSummary(section="offer_objects", text="Mostly an offer for sale by the promoter and a PE fund.")],
        notes=["dropped a reason citing an unknown item"])


def advice() -> CheckedAdvice:
    return CheckedAdvice(
        call="buy", conviction="medium", horizon="listing_gains",
        headline="Apply for listing gains: priced below peers, though most of the issue is an exit.",
        reasons=[EvidencedPoint(text="P/E of 15× is a deep discount to the peer median of 38×.", evidence=["D:pe", "V:read"]),
                 EvidencedPoint(text="A ₹500 cr issue, mostly an offer for sale.", evidence=["F:offer_objects.issue_size"]),
                 EvidencedPoint(text="Retail is 5.1 times subscribed.", evidence=["S:subscription"])],
        key_risks=[EvidencedPoint(text="Cash from operations is below PAT.", evidence=["C:CFO_BELOW_PAT"])],
        what_would_change_my_mind="Subscription stalling before the close.",
        as_of=date(2026, 10, 13), notes=[])


def full_report() -> Report:
    ipo = make_ipo()
    o, fin, g, pb, b = offer(), financials(), governance(), price_basis(), business()
    calcs = compute_offer(o, ipo)
    section_calcs, section_flags = compute_sections(ipo, b, g, pb, fin)
    return Report(
        ipo=ipo, status="complete", verdict="subscribe_listing_gains", offer=o, financials=fin,
        financial_flags=[FinancialCheck(code="CFO_BELOW_PAT", status="raised",
                                        detail="Cash from operations is below PAT in Fiscal 2026"),
                         FinancialCheck(code="DEBT_RISING", status="clear", detail="")],
        risk_factors=risks(), fine_print_legal=legal(), business=b, governance=g, price_basis=pb,
        other=OtherDisclosures(items=[MaterialItem(summary="A SEBI show-cause notice to a promoter entity.",
                                                   why_material="It may delay the listing.",
                                                   source=Quote(page=440, verbatim_quote="show cause notice"),
                                                   figures=[])]),
        sentiment=sentiment(), valuation=valuation(), synthesis=synthesis(), advice=advice(),
        calculations={**calcs, **section_calcs},
        flags=[Flag(code="OFS_HEAVY", severity="warning", message="Offer for sale is 60.0% of the issue",
                    evidence_pages=[19])] + section_flags,
        unverified_fields=["offer.anchor_pct"],
        rhp_sha256="ab" * 32)


def partial_report() -> Report:
    """Stopped at the budget cap: only the offer section and its calculations were verified."""
    full = full_report()
    return Report(ipo=full.ipo, status="partial_budget", verdict=None, offer=full.offer,
                  calculations=compute_offer(full.offer, full.ipo), rhp_sha256=full.rhp_sha256)
