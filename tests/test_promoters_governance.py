"""Promoters & Governance section agent at seam B (plan 5.2; ticket 14)."""
import pytest

from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import (CitedText, Dividend, Fact, FinancialPeriod, Financials, GroupCompany, Promoter,
                                  PromotersGovernance, Quote, Remuneration)
from tests.conftest import make_tiny_rhp
from tests.fakes import NOT_DISCLOSED, ScriptedLLM, found_nothing, make_ipo

pytestmark = pytest.mark.sections("promoters_governance")

# The tiny RHP: pages 1-7 are the stock sections; these extra pages are 8 (management), 9 (promoters),
# 10 (group companies) and 11 (MD&A, a financials page).
MANAGEMENT_PAGE = "\n".join([
    "OUR MANAGEMENT",
    "Our Board comprises 8 Directors, of which 4 are Independent Directors.",
    "Remuneration paid to Ravi Kumar in Fiscal 2024: Rs. 125.00 lakh",
    "Remuneration paid to Asha Kumar in Fiscal 2024: Rs. 7.50 million",
    "Remuneration paid to our Chief Financial Officer: Rs. 30.00 million",
    "M/s ABC & Co. resigned as statutory auditors on June 1, 2022.",
    "M/s XYZ LLP resigned as statutory auditors on May 5, 2023.",
])
GROUP_PAGE = "\n".join([
    "GROUP COMPANIES",
    "Kumar Motors Private Limited is engaged in a similar line of business.",
    "Kumar Realty Private Limited is engaged in real estate.",
])


def promoters_page(pledged):
    return "\n".join([
        "OUR PROMOTERS AND PROMOTER GROUP",
        "Ravi Kumar holds an engineering degree and founded our Company.",
        f"Shares pledged by our Promoters: {pledged}",
        "Final dividend of Rs. 2.00 per equity share for Fiscal 2024.",
    ])


def mda_page(pat):
    return "\n".join([
        "MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION",
        f"Restated profit after tax for Fiscal 2024 was Rs. {pat} crore.",
        "Restated profit after tax for Fiscal 2023 was Rs. 400.00 crore.",
    ])


def governance_rhp(tmp_path, pledged="2,500,000 equity shares", pat="25.00"):
    return make_tiny_rhp(tmp_path / "governance_rhp.pdf", extra_pages=(
        MANAGEMENT_PAGE, promoters_page(pledged), GROUP_PAGE, mda_page(pat)))


@pytest.fixture
def rhp(tmp_path):
    return governance_rhp(tmp_path)


def fact(value, quote, unit, page=8):
    return Fact(value=value, unit=unit, page=page, verbatim_quote=quote)


def quote(text, page):
    return Quote(page=page, verbatim_quote=text)


RAVI_PAY = Remuneration(name="Ravi Kumar", role="Managing Director", is_promoter=True,
                        amount=fact(125.00, "paid to Ravi Kumar in Fiscal 2024: Rs. 125.00 lakh", "₹ lakh"))
ASHA_PAY = Remuneration(name="Asha Kumar", role="Whole-time Director", is_promoter=True,
                        amount=fact(7.50, "paid to Asha Kumar in Fiscal 2024: Rs. 7.50 million", "₹ million"))
CFO_PAY = Remuneration(name="A. Rao", role="Chief Financial Officer", is_promoter=False,
                       amount=fact(30.00, "paid to our Chief Financial Officer: Rs. 30.00 million", "₹ million"))
PLEDGED = fact(2500000, "Shares pledged by our Promoters: 2,500,000 equity shares", "equity shares", page=9)
SAME_LINE = GroupCompany(name="Kumar Motors Private Limited", same_line_of_business=True,
                         source=quote("Kumar Motors Private Limited is engaged in a similar line of business", 10))
OTHER_LINE = GroupCompany(name="Kumar Realty Private Limited", same_line_of_business=False,
                          source=quote("Kumar Realty Private Limited is engaged in real estate", 10))
AUDITOR_CHANGES = [CitedText(text="ABC & Co. resigned as statutory auditors, June 2022",
                             source=quote("M/s ABC & Co. resigned as statutory auditors on June 1, 2022", 8)),
                   CitedText(text="XYZ LLP resigned as statutory auditors, May 2023",
                             source=quote("M/s XYZ LLP resigned as statutory auditors on May 5, 2023", 8))]


def governance(**fields) -> PromotersGovernance:
    return found_nothing(PromotersGovernance).model_copy(update=fields)


def period(label, end_date, pat, kind="full_year"):
    metrics = {name: NOT_DISCLOSED for name, f in FinancialPeriod.model_fields.items() if f.annotation is Fact}
    return FinancialPeriod(label=label, end_date=end_date, kind=kind, **{**metrics, "pat": pat})


def financials(fy24_pat="25.00"):
    """Financials (ticket 11) as RHPs print them, latest year first; PAT verifies on the MD&A page (11)."""
    return Financials(periods=[
        period("Fiscal 2024", "March 31, 2024", fact(float(fy24_pat), f"profit after tax for Fiscal 2024 was Rs. "
                                                                    f"{fy24_pat} crore", "₹ crore", page=11)),
        period("Fiscal 2023", "March 31, 2023", fact(400.00, "profit after tax for Fiscal 2023 was Rs. 400.00 crore",
                                                     "₹ crore", page=11)),
    ], auditor_remarks=[], restatement=None)


def analyse(rhp, adapters, config, answer, fin=None):
    responses = {PromotersGovernance: answer}
    if fin is not None:
        responses[Financials] = fin
    adapters.llm = ScriptedLLM(responses, models=config.models)
    return analyse_rhp(make_ipo(), rhp, adapters, config)


def flags(report, code):
    return [f for f in report.flags if f.code == code]


def test_governance_fields_are_extracted_as_page_cited_facts_that_verify(rhp, adapters, config):
    answer = governance(
        promoters=[Promoter(name="Ravi Kumar", background="Engineer; founded the company",
                            source=quote("Ravi Kumar holds an engineering degree and founded our Company", 9))],
        group_companies=[SAME_LINE, OTHER_LINE],
        remuneration=[RAVI_PAY, ASHA_PAY, CFO_PAY],
        board_size=fact(8, "Our Board comprises 8 Directors", "directors"),
        independent_directors=fact(4, "of which 4 are Independent Directors", "directors"),
        promoter_shares_pledged=PLEDGED,
        dividends=[Dividend(period="Fiscal 2024", per_share=fact(
            2.00, "Final dividend of Rs. 2.00 per equity share", "₹ per share", page=9))],
        auditor_changes=AUDITOR_CHANGES,
    )

    report = analyse(rhp, adapters, config, answer)

    assert report.unverified_fields == []
    g = report.governance
    assert (g.board_size.value, g.independent_directors.value) == (8, 4)
    assert [r.amount.value for r in g.remuneration] == [125.00, 7.50, 30.00]
    assert g.promoter_shares_pledged.value == 2500000
    assert g.dividends[0].per_share.value == 2.00
    assert g.promoters[0].source.page == 9


def test_the_agent_is_routed_to_groq(rhp, adapters, config):
    # Its call is capped at 1,500 output tokens, so prompt + schema + pages fit Groq's 6,000-token limit.
    analyse(rhp, adapters, config, governance())

    [call] = [c for c in adapters.llm.calls if c["task"] == "promoters_governance"]
    assert (call["model"], call["max_tokens"]) == ("groq", 1500)
    assert "Our Board comprises 8 Directors" in call["user"]
    assert "Restated profit after tax" not in call["user"]  # the MD&A page belongs to Financials


@pytest.mark.sections("promoters_governance", "financials")
def test_promoter_pay_over_5_percent_of_pat_is_flagged(rhp, adapters, config):
    # Promoter pay: 125.00 lakh = ₹1.25 crore, plus ₹7.50 million = ₹0.75 crore: ₹2.00 crore (the CFO is
    # not a promoter). Latest full-year PAT (Fiscal 2024) ₹25.00 crore. 2 / 25 = 8%.
    report = analyse(rhp, adapters, config, governance(remuneration=[RAVI_PAY, ASHA_PAY, CFO_PAY]), financials())

    assert report.unverified_fields == []
    pay = report.calculations["promoter_pay_pct_of_pat"]
    assert pay.value == pytest.approx(8.0)
    assert [(i.field, i.page) for i in pay.inputs] == [
        ("promoters_governance.remuneration[0].amount", 8), ("promoters_governance.remuneration[1].amount", 8),
        ("financials.periods[0].pat", 11)]
    [flag] = flags(report, "PROMOTER_PAY_VS_PAT")
    assert (flag.section, flag.evidence_pages) == ("promoters_governance", [8, 11])


@pytest.mark.sections("promoters_governance", "financials")
def test_promoter_pay_under_5_percent_of_pat_is_quiet(tmp_path, adapters, config):
    # ₹2.00 crore / ₹150.00 crore = 1.33%.
    rhp = governance_rhp(tmp_path, pat="150.00")

    report = analyse(rhp, adapters, config, governance(remuneration=[RAVI_PAY, ASHA_PAY]), financials("150.00"))

    assert report.calculations["promoter_pay_pct_of_pat"].value == pytest.approx(4 / 3)
    assert flags(report, "PROMOTER_PAY_VS_PAT") == []


def test_promoter_pay_vs_pat_is_not_computable_without_financials(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance(remuneration=[RAVI_PAY, ASHA_PAY]))

    pay = report.calculations["promoter_pay_pct_of_pat"]
    assert pay.value is None
    assert pay.not_computable_reason == "financials.periods: no verified full-year PAT"
    assert flags(report, "PROMOTER_PAY_VS_PAT") == []


def test_a_group_company_in_the_same_line_of_business_is_a_conflict(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance(group_companies=[SAME_LINE, OTHER_LINE]))

    [flag] = flags(report, "GROUP_COMPANY_CONFLICT")
    assert "Kumar Motors" in flag.message and "Kumar Realty" not in flag.message
    assert flag.evidence_pages == [10]


def test_pledged_promoter_shares_are_flagged(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance(promoter_shares_pledged=PLEDGED))

    [flag] = flags(report, "PROMOTER_PLEDGE")
    assert flag.evidence_pages == [9]


def test_no_pledge_is_quiet(tmp_path, adapters, config):
    rhp = governance_rhp(tmp_path, pledged="Nil")
    nil = fact(0, "Shares pledged by our Promoters: Nil", "equity shares", page=9)

    report = analyse(rhp, adapters, config, governance(promoter_shares_pledged=nil))

    assert report.unverified_fields == []
    assert flags(report, "PROMOTER_PLEDGE") == []


def test_two_auditor_changes_in_three_years_are_flagged(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance(auditor_changes=AUDITOR_CHANGES))

    [flag] = flags(report, "FREQUENT_AUDITOR_CHANGES")
    assert flag.evidence_pages == [8]


def test_one_auditor_change_is_quiet(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance(auditor_changes=AUDITOR_CHANGES[:1]))

    assert flags(report, "FREQUENT_AUDITOR_CHANGES") == []


def test_a_made_up_auditor_change_does_not_count(rhp, adapters, config):
    # (A near-copy of a real line, e.g. "M/s PQR & Co. resigned as statutory auditors", would still pass:
    # the quote check is fuzzy, so it can't tell one firm name from another of the same shape.)
    made_up = CitedText(text="PQR LLP appointed", source=quote("PQR LLP was appointed statutory auditor in 2024", 8))

    report = analyse(rhp, adapters, config, governance(auditor_changes=[AUDITOR_CHANGES[0], made_up]))

    assert report.unverified_fields == ["promoters_governance.auditor_changes[1].source"]
    assert flags(report, "FREQUENT_AUDITOR_CHANGES") == []


def test_an_empty_section_raises_no_governance_flag(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance())

    assert [f for f in report.flags if f.section == "promoters_governance"] == []
