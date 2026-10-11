"""Cross-section checks and the synthesizer verdict at seam B, and the verdict lines of the summary at seam A
(plan 5.6, 10; ticket 20; spec stories 30, 37, 40, 41, 46).

Check expectations are worked by hand in the comments. The tiny RHP: page 2 the offer and its objects,
3 restated financials, 4 litigation (with contingent liabilities), 5 basis for offer price (KPIs), 6 risk
factors. Every amount is printed in ₹ crore.
"""
import json
from datetime import date

import pytest

from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded
from ipo_agents.config import RunEstimate
from ipo_agents.contracts import (ClaimedFact, EvidencedPoint, Fact, Financials, FinePrintLegal, Kpi, LitigationLine,
                                  OfferObjects, OfferPriceBasis, PeriodFact, ProceedsObject, Quote, RiskFactor,
                                  RiskFactors, SectionSummary, Synthesis)
from ipo_agents.runner import format_summary, run_daily
from tests.conftest import TINY_OFFER_PAGE, make_tiny_rhp
from tests.fakes import (NOT_DISCLOSED, FakeExchange, FakeMarketData, FakePeerData, ScriptedLLM, found_nothing,
                         make_ipo, offer_objects)
from tests.test_financials import financials, period

ALL = ("offer_objects", "financials", "fine_print_legal", "offer_price_basis", "risk_factors")
OFFER, FIN, LIT, BASIS, RISK = 2, 3, 4, 5, 6
TODAY = date(2026, 10, 13)

ISSUE = "Total issue size of up to Rs. 1,250.00 crore"
REVENUE = "Revenue from operations 1,000.00"
PAT = "Restated profit for the year 120.00"
TAX = "Tax proceedings against our Company 7 340.00"
RISK_TEXT = "There are outstanding tax proceedings involving Rs. 340.00 crore against our Company."


def repay_line(amount): return f"Repayment or prepayment of certain borrowings {amount}"
def borrowings_line(amount): return f"Total borrowings {amount}"
def cl_line(amount): return f"Contingent liabilities not provided for {amount}"
def kpi_pat_line(amount): return f"Profit after tax {amount}"


def make_rhp(tmp_path, repay="300.00", borrowings="250.00", cl="100.00", kpi_pat="150.00"):
    pages = (
        "\n".join([TINY_OFFER_PAGE, "(Rs. crore)", repay_line(repay)]),
        "\n".join(["RESTATED FINANCIAL INFORMATION", "(Rs. crore) Fiscal 2024", REVENUE, PAT,
                   borrowings_line(borrowings)]),
        "\n".join(["OUTSTANDING LITIGATION AND MATERIAL DEVELOPMENTS", "(Rs. crore) Number Amount", TAX,
                   cl_line(cl)]),
        "\n".join(["BASIS FOR OFFER PRICE", "Key performance indicators (Rs. crore) Fiscal 2024", REVENUE,
                   kpi_pat_line(kpi_pat)]),
        "\n".join(["RISK FACTORS", RISK_TEXT]),
    )
    return make_tiny_rhp(tmp_path / "cross_rhp.pdf", pages)


def crore(value, quote, page):
    return Fact(value=value, unit="₹ crore", page=page, verbatim_quote=quote)


def answers(repay=300.0, borrowings=250.0, cl=100.0, kpi_pat=150.0, borrowings_quote=None, cl_quote=None):
    """Section answers matching make_rhp; a *_quote override cites words not on the page (fails verification)."""
    offer = offer_objects(issue_size=crore(1250.0, ISSUE, OFFER), objects=[ProceedsObject(
        purpose="Repayment or prepayment of certain borrowings", amount=crore(repay, repay_line(f"{repay:.2f}"), OFFER))])
    fin = financials([period("Fiscal 2024", "March 31, 2024",
                             revenue_from_operations=crore(1000.0, REVENUE, FIN), pat=crore(120.0, PAT, FIN),
                             total_borrowings=crore(borrowings, borrowings_quote or borrowings_line(f"{borrowings:.2f}"),
                                                    FIN))])
    legal = FinePrintLegal(
        litigation=[LitigationLine(party="company", case_type="tax", direction="against",
                                   count=Fact(value=7, unit="cases", page=LIT, verbatim_quote=TAX),
                                   amount=crore(340.0, TAX, LIT))],
        contingent_liabilities=crore(cl, cl_quote or cl_line(f"{cl:.2f}"), LIT),
        related_party=[], approvals=[], material_contracts=[])
    basis = found_nothing(OfferPriceBasis).model_copy(update={"kpis": [
        Kpi(name="Revenue from operations", values=[PeriodFact(period="Fiscal 2024", value=crore(1000.0, REVENUE, BASIS))]),
        Kpi(name="Profit after tax", values=[PeriodFact(
            period="Fiscal 2024", value=crore(kpi_pat, kpi_pat_line(f"{kpi_pat:.2f}"), BASIS))]),
        Kpi(name="PAT margin", values=[]),  # a ratio: not the PAT metric
    ]})
    risks = RiskFactors(risks=[RiskFactor(topic="tax proceedings", title=Quote(page=RISK, verbatim_quote=RISK_TEXT),
                                          kind="company_specific", quantified=True, figure=crore(340.0, RISK_TEXT, RISK),
                                          severity_rank=1)])
    return {OfferObjects: offer, Financials: fin, FinePrintLegal: legal, OfferPriceBasis: basis, RiskFactors: risks}


def checks(report):
    return {c.code: c.status for c in report.cross_checks}


def details(report):
    return {c.code: c.detail for c in report.cross_checks}


# --- cross-section checks -------------------------------------------------------------------

@pytest.mark.sections(*ALL)
def test_inconsistent_sections_raise_each_cross_check(tmp_path, adapters, config):
    adapters.llm = ScriptedLLM(answers(), models=config.models)

    report = analyse_rhp(make_ipo(), make_rhp(tmp_path), adapters, config)

    assert report.unverified_fields == []
    assert checks(report) == {
        "debt_repayment_vs_borrowings": "raised",  # repay ₹300 crore > borrowings ₹250 crore
        "litigation_vs_contingent_liabilities": "raised",  # ₹340 crore > 2 x ₹100 crore = ₹200 crore
        "kpis_vs_financials": "raised",  # KPI PAT 150 vs restated PAT 120: 25% apart; revenue 1,000 = 1,000
        "risk_figures_corroborated": "clear",  # informational: the ₹340 crore tax risk is the litigation amount
    }
    assert "Profit after tax Fiscal 2024" in details(report)["kpis_vs_financials"]
    assert "Revenue" not in details(report)["kpis_vs_financials"]
    assert "corroborated in Financials or Litigation: tax proceedings;" in details(report)["risk_figures_corroborated"]
    flags = {f.code: f for f in report.flags}
    assert flags["DEBT_REPAYMENT_VS_BORROWINGS"].severity == "warning"
    assert flags["DEBT_REPAYMENT_VS_BORROWINGS"].evidence_pages == [OFFER, FIN]
    assert flags["LITIGATION_VS_CONTINGENT_LIABILITIES"].evidence_pages == [LIT]
    assert flags["KPIS_VS_FINANCIALS"].evidence_pages == [FIN, BASIS]
    assert "RISK_FIGURES_CORROBORATED" not in flags


@pytest.mark.sections(*ALL)
def test_consistent_sections_keep_the_cross_checks_quiet(tmp_path, adapters, config):
    adapters.llm = ScriptedLLM(answers(repay=250.0, cl=170.0, kpi_pat=121.0), models=config.models)

    report = analyse_rhp(make_ipo(), make_rhp(tmp_path, repay="250.00", cl="170.00", kpi_pat="121.00"), adapters, config)

    assert checks(report) == {
        "debt_repayment_vs_borrowings": "clear",  # ₹250 crore = borrowings: not more
        "litigation_vs_contingent_liabilities": "clear",  # ₹340 crore = 2 x ₹170 crore: not more than 2x
        "kpis_vs_financials": "clear",  # 121 vs 120 is 0.83% apart, within 1%
        "risk_figures_corroborated": "clear",
    }
    assert not [f for f in report.flags if f.section == "cross_checks"]


@pytest.mark.sections(*ALL)
def test_an_unverified_input_makes_its_cross_check_not_computable(tmp_path, adapters, config):
    # Borrowings and contingent liabilities cite words that aren't on their pages: "could not verify".
    adapters.llm = ScriptedLLM(answers(borrowings_quote="Total borrowings outstanding 250.00",
                                       cl_quote="Contingent liabilities of 100.00"), models=config.models)

    report = analyse_rhp(make_ipo(), make_rhp(tmp_path), adapters, config)

    assert "financials.periods[0].total_borrowings" in report.unverified_fields
    assert "fine_print_legal.contingent_liabilities" in report.unverified_fields
    assert checks(report)["debt_repayment_vs_borrowings"] == "not_computable"
    assert checks(report)["litigation_vs_contingent_liabilities"] == "not_computable"
    assert {"DEBT_REPAYMENT_VS_BORROWINGS", "LITIGATION_VS_CONTINGENT_LIABILITIES"}.isdisjoint(
        f.code for f in report.flags)


# --- the synthesizer --------------------------------------------------------------------------

OFFER_ANSWER = offer_objects(issue_size=crore(1250.0, ISSUE, OFFER))
ISSUE_REASON = EvidencedPoint(text="The issue is ₹1,250 crore", evidence=["F:offer_objects.issue_size"])
BAND_REASON = EvidencedPoint(text="Priced at a ₹100–₹105 band", evidence=["E:ipo"])


def synthesis(verdict="avoid", confidence="medium", reasons=(ISSUE_REASON, BAND_REASON), red_flags=(), positives=()):
    return Synthesis(verdict=verdict, confidence=confidence, reasons=list(reasons), top_red_flags=list(red_flags),
                     top_positives=list(positives),
                     section_summaries=[SectionSummary(section="offer_objects", text="A ₹1,250 crore issue.")])


def synth_prompt(llm):
    [call] = [c for c in llm.calls if c["task"] == "synthesizer"]
    return call["user"]


def digest_ids(llm):
    return [item["id"] for item in json.loads(synth_prompt(llm).split("\n", 1)[1])["items"]]


@pytest.mark.synthesizer
def test_the_synthesizer_sees_only_verified_facts(tiny_rhp, adapters, config):
    # The fresh issue cites a ₹999 crore quote that isn't on page 2: it fails verification.
    wrong = "Fresh issue of up to Rs. 999.00 crore"
    answer = offer_objects(issue_size=crore(1250.0, ISSUE, OFFER), fresh_issue=crore(999.0, wrong, OFFER))
    adapters.llm = llm = ScriptedLLM({OfferObjects: answer, Synthesis: synthesis()}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.unverified_fields == ["offer_objects.fresh_issue"]
    prompt = synth_prompt(llm)
    assert "F:offer_objects.issue_size" in digest_ids(llm)
    assert '{"id":"F:offer_objects.issue_size","value":1250.0,"unit":"₹ crore","page":2}' in prompt
    assert "999" not in prompt and wrong not in prompt
    assert "F:offer_objects.fresh_issue" not in digest_ids(llm)
    # Only its name is listed, as a field that could not be verified: opacity is a fact too.
    assert '"could_not_verify":["fresh_issue"]' in prompt
    assert report.verdict == "avoid"
    assert report.synthesis.reasons == [ISSUE_REASON, BAND_REASON]


@pytest.mark.synthesizer
def test_points_citing_an_unknown_id_or_a_number_not_in_their_evidence_are_dropped(tiny_rhp, adapters, config):
    invented_id = EvidencedPoint(text="The company is debt-free", evidence=["F:financials.periods[0].total_borrowings"])
    invented_number = EvidencedPoint(text="The issue is ₹1,500 crore", evidence=["F:offer_objects.issue_size"])
    no_evidence = EvidencedPoint(text="Strong brand", evidence=[])
    flag = EvidencedPoint(text="OFS is 80% of the issue", evidence=["E:ipo"])
    answer = synthesis(reasons=[ISSUE_REASON, invented_id, invented_number, no_evidence, BAND_REASON], red_flags=[flag])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: answer}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    s = report.synthesis
    assert s.reasons == [ISSUE_REASON, BAND_REASON]
    assert s.top_red_flags == []
    assert s.confidence == "medium"  # two reasons survived
    assert len(s.notes) == 4
    assert any("F:financials.periods[0].total_borrowings" in n for n in s.notes)
    assert any("1,500" in n for n in s.notes)
    assert any("80" in n for n in s.notes)


@pytest.mark.synthesizer
def test_fewer_than_two_surviving_reasons_make_confidence_low(tiny_rhp, adapters, config):
    invented = EvidencedPoint(text="Margins of 40%", evidence=["F:offer_objects.issue_size"])
    answer = synthesis(verdict="subscribe_long_term", confidence="high", reasons=[ISSUE_REASON, invented])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: answer}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.verdict == "subscribe_long_term"  # its one surviving reason rests on an RHP fact
    assert report.synthesis.confidence == "low"
    assert report.synthesis.reasons == [ISSUE_REASON]


GMP = ClaimedFact(value=40.0, unit="₹", source_url="https://gmp.example/acme", source_name="GMP Watch",
                  as_of=date(2026, 10, 12))
GMP_REASONS = [EvidencedPoint(text="GMP of ₹40 signals demand", evidence=["S:gmp"]),
               EvidencedPoint(text="The grey market is strong", evidence=["S:gmp"])]


@pytest.mark.synthesizer
def test_a_subscribe_resting_only_on_gmp_is_downgraded_to_neutral(tiny_rhp, adapters, config):
    adapters.market = FakeMarketData(gmp_fact=GMP)
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: synthesis(
        verdict="subscribe_listing_gains", confidence="high", reasons=GMP_REASONS)}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.verdict == "neutral"
    assert report.synthesis.verdict == "neutral"
    assert any("GMP" in n and "sole basis" in n for n in report.synthesis.notes)
    [gmp] = [i for i in json.loads(synth_prompt(llm).split("\n", 1)[1])["items"] if i["id"] == "S:gmp"]
    assert gmp["value"] == 40.0 and "unofficial" in gmp["label"] and "unofficial" in gmp["note"]


@pytest.mark.synthesizer
def test_a_subscribe_with_an_rhp_reason_beside_gmp_stands(tiny_rhp, adapters, config):
    adapters.market = FakeMarketData(gmp_fact=GMP)
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: synthesis(
        verdict="subscribe_listing_gains", reasons=[GMP_REASONS[0], ISSUE_REASON])}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.verdict == "subscribe_listing_gains"
    assert report.synthesis.notes == []


@pytest.mark.synthesizer
@pytest.mark.sections("offer_objects", "financials", "offer_price_basis", "business_industry")
def test_the_synthesizer_runs_last_and_reads_the_peer_valuation(tiny_rhp, adapters, config):
    adapters.peers = FakePeerData()
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: synthesis()}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.valuation is not None
    assert llm.calls[-1]["task"] == "synthesizer"
    assert "V:read" in digest_ids(llm)
    assert report.valuation.read in synth_prompt(llm)


@pytest.mark.synthesizer
def test_a_crash_in_the_synthesizer_keeps_the_verified_report_without_a_verdict(tiny_rhp, adapters, config):
    # Call 0 is the offer read, call 1 the synthesizer: the connection drops on it. The synthesizer is
    # optional (ticket 26): the verified analysis goes out, with no verdict and the reason why.
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: synthesis()}, models=config.models,
                                     errors=[None, ConnectionError("connection dropped mid-call")])

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [c["task"] for c in llm.calls] == ["offer_objects", "synthesizer"]
    assert report.status == "complete"
    assert report.verdict is None and report.synthesis is None
    assert report.synthesis_error == "ConnectionError"
    assert report.offer.issue_size.value == 1250.0


def no_estimate(config):
    return config.model_copy(update={"budget": config.budget.model_copy(
        update={"estimate": RunEstimate(tokens_per_page=0, output_tokens_per_page=0, fixed_usd=0)})})


@pytest.mark.synthesizer
def test_a_budget_stop_before_the_synthesizer_leaves_no_verdict(tiny_rhp, adapters, config):
    # The offer read is billed 124,995 out x $12/1M = $1.49994, leaving $0.00006: past 70% the synthesizer
    # goes to luna, whose worst case alone is 2,000 x $1.20/1M = $0.0024. It is never sent.
    config = no_estimate(config)
    adapters.llm = llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: synthesis()}, models=config.models,
                                     usage=[(0, 0, 124_995)])

    with pytest.raises(BudgetExceeded) as stop:
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    report = stop.value.report
    assert report.status == "partial_budget"
    assert report.verdict is None and report.synthesis is None
    assert report.offer.issue_size.value == 1250.0
    assert [c["task"] for c in llm.calls] == ["offer_objects"]


# --- seam A: the summary ----------------------------------------------------------------------

LONG = ("Promoters and investors sell most of the shares in this issue, so most of the money raised goes to "
        "them and not to the company")


@pytest.mark.synthesizer
def test_summary_shows_the_verdict_why_and_the_top_three_red_flags(adapters, config):
    red = [EvidencedPoint(text=LONG, evidence=["E:ipo"]),
           EvidencedPoint(text="Second flag", evidence=["E:ipo"]),
           EvidencedPoint(text="Third flag", evidence=["E:ipo"]),
           EvidencedPoint(text="Fourth flag", evidence=["E:ipo"])]
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: synthesis(red_flags=red)}, models=config.models)

    run_daily(TODAY, adapters, config)

    [msg] = adapters.notifier.messages
    assert len(LONG) > 120
    truncated = LONG[:119].rstrip() + "…"
    assert "\n".join([
        "Analyst view: AVOID (confidence: medium)",
        "Why:",
        "• The issue is ₹1,250 crore",
        "• Priced at a ₹100–₹105 band",
        "Red flags:",
        f"• {truncated}",
        "• Second flag",
        "• Third flag",
    ]) in msg
    assert "Fourth flag" not in msg
    assert msg.index("Issue: ₹1,250 crore (p. 2)") < msg.index("Analyst view:")
    assert msg.endswith("Not investment advice; generated from RHP + public sources.")


@pytest.mark.synthesizer
def test_summary_of_a_budget_stop_says_the_verdict_was_not_reached(adapters, config):
    config = no_estimate(config)
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: synthesis()}, models=config.models,
                               usage=[(0, 0, 124_995)])

    summary = run_daily(TODAY, adapters, config)

    assert summary.failed == ["ACME-2026-10-13"]
    [msg] = adapters.notifier.messages
    assert msg.startswith("⚠️ PARTIAL: stopped at the $1.50 budget cap\n")
    assert "Analyst view: not reached (budget cap)" in msg
    assert "Why:" not in msg
