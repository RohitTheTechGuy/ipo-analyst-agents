"""Promoters & Governance section agent at seam B (plan 5.2; ticket 14)."""
import pytest
from pydantic import BaseModel

import ipo_agents.analysis as analysis
from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import Cited, Dividend, Fact, GroupCompany, Promoter, PromotersGovernance, Remuneration
from ipo_agents.section_agent import SectionSpec
from ipo_agents.sections import SECTIONS
from tests.conftest import make_tiny_rhp
from tests.fakes import ScriptedLLM, empty_answer, make_ipo

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
    return f"MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION\nRestated profit after tax for Fiscal 2024 was Rs. {pat} crore."


def governance_rhp(tmp_path, pledged="2,500,000 equity shares", pat="25.00"):
    return make_tiny_rhp(tmp_path / "governance_rhp.pdf", extra_pages=(
        MANAGEMENT_PAGE, promoters_page(pledged), GROUP_PAGE, mda_page(pat)))


@pytest.fixture
def rhp(tmp_path):
    return governance_rhp(tmp_path)


def fact(value, quote, unit, page=8):
    return Fact(value=value, unit=unit, page=page, verbatim_quote=quote)


RAVI_PAY = Remuneration(name="Ravi Kumar", role="Managing Director", is_promoter=True,
                        amount=fact(125.00, "paid to Ravi Kumar in Fiscal 2024: Rs. 125.00 lakh", "₹ lakh"))
ASHA_PAY = Remuneration(name="Asha Kumar", role="Whole-time Director", is_promoter=True,
                        amount=fact(7.50, "paid to Asha Kumar in Fiscal 2024: Rs. 7.50 million", "₹ million"))
CFO_PAY = Remuneration(name="A. Rao", role="Chief Financial Officer", is_promoter=False,
                       amount=fact(30.00, "paid to our Chief Financial Officer: Rs. 30.00 million", "₹ million"))
PLEDGED = fact(2500000, "Shares pledged by our Promoters: 2,500,000 equity shares", "equity shares", page=9)
SAME_LINE = GroupCompany(name="Kumar Motors Private Limited", same_line_of_business=True, page=10)
OTHER_LINE = GroupCompany(name="Kumar Realty Private Limited", same_line_of_business=False, page=10)
AUDITOR_CHANGES = [Cited(text="ABC & Co. resigned as statutory auditors, June 2022", page=8),
                   Cited(text="XYZ LLP resigned as statutory auditors, May 2023", page=8)]


def governance(**fields) -> PromotersGovernance:
    return empty_answer(PromotersGovernance).model_copy(update=fields)


class StubFinancials(BaseModel):
    """Stands in for the Financials output (ticket 11): only the PAT this section's check reads."""

    latest_pat: Fact


def with_financials(monkeypatch, pat_value, pat_text="25.00"):
    """Runs a stub Financials agent alongside, whose PAT must verify on the MD&A page (11)."""
    # Not a Report field, so the stub never clashes with the real Report.financials.
    spec = SectionSpec(group="financials", schema=StubFinancials, prompt="p", report_field="stub_financials")
    monkeypatch.setattr(analysis, "SECTIONS", {**SECTIONS, "financials": spec})
    return StubFinancials(latest_pat=fact(pat_value, f"profit after tax for Fiscal 2024 was Rs. {pat_text} crore",
                                          "₹ crore", page=11))


def analyse(rhp, adapters, config, answer, financials=None):
    responses = {PromotersGovernance: answer}
    if financials is not None:
        responses[StubFinancials] = financials
    adapters.llm = ScriptedLLM(responses, models=config.models)
    return analyse_rhp(make_ipo(), rhp, adapters, config)


def flags(report, code):
    return [f for f in report.red_flags if f.code == code]


def pay_vs_pat(report):
    [d] = [d for d in report.derived if d.name == "promoter_pay_pct_of_pat"]
    return d


def test_governance_fields_are_extracted_as_page_cited_facts_that_verify(rhp, adapters, config):
    answer = governance(
        promoters=[Promoter(name="Ravi Kumar", background="Engineer; founded the company", page=9)],
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
    assert g.promoters[0].page == 9


@pytest.mark.parametrize("max_tokens, model", [
    # Prompt + schema are ~2,100 tokens: with 2,000 output tokens the call fits Groq's 6,000-token
    # call limit; with 4,000 it doesn't, so it falls back to Luna.
    (2000, "groq"),
    (4000, "luna"),
])
def test_the_agent_is_routed_to_groq_with_luna_fallback(rhp, adapters, config, max_tokens, model):
    config = config.model_copy(update={"default_max_tokens": max_tokens})

    analyse(rhp, adapters, config, governance())

    [call] = [c for c in adapters.llm.calls if c["task"] == "promoters_governance"]
    assert call["model"] == model
    assert "Our Board comprises 8 Directors" in call["user"]
    assert "Restated profit after tax" not in call["user"]  # the MD&A page belongs to Financials


def test_promoter_pay_over_5_percent_of_pat_is_flagged(rhp, adapters, config, monkeypatch):
    # Promoter pay: 125.00 lakh = ₹12.5 million, plus ₹7.5 million = ₹20 million (the CFO is not a
    # promoter). PAT ₹25.00 crore = ₹250 million. 20 / 250 = 8%.
    financials = with_financials(monkeypatch, 25.00)

    report = analyse(rhp, adapters, config, governance(remuneration=[RAVI_PAY, ASHA_PAY, CFO_PAY]), financials)

    assert report.unverified_fields == []
    assert pay_vs_pat(report).value == pytest.approx(8.0)
    [flag] = flags(report, "promoter_pay_vs_pat")
    assert flag.pages == [8, 11]


def test_promoter_pay_under_5_percent_of_pat_is_quiet(tmp_path, adapters, config, monkeypatch):
    # PAT ₹150.00 crore = ₹1,500 million; ₹20 million / ₹1,500 million = 1.33%.
    rhp = governance_rhp(tmp_path, pat="150.00")
    financials = with_financials(monkeypatch, 150.00, pat_text="150.00")

    report = analyse(rhp, adapters, config, governance(remuneration=[RAVI_PAY, ASHA_PAY]), financials)

    assert pay_vs_pat(report).value == pytest.approx(4 / 3)
    assert flags(report, "promoter_pay_vs_pat") == []


def test_promoter_pay_vs_pat_is_not_computable_without_pat(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance(remuneration=[RAVI_PAY, ASHA_PAY]))

    assert pay_vs_pat(report).value is None
    assert "not computable" in pay_vs_pat(report).note
    assert flags(report, "promoter_pay_vs_pat") == []


def test_a_group_company_in_the_same_line_of_business_is_a_conflict(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance(group_companies=[SAME_LINE, OTHER_LINE]))

    [flag] = flags(report, "group_company_conflict")
    assert "Kumar Motors" in flag.message and "Kumar Realty" not in flag.message
    assert flag.pages == [10]


def test_pledged_promoter_shares_are_flagged(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance(promoter_shares_pledged=PLEDGED))

    [flag] = flags(report, "promoter_pledge")
    assert flag.pages == [9]


def test_no_pledge_is_quiet(tmp_path, adapters, config):
    rhp = governance_rhp(tmp_path, pledged="Nil")
    nil = fact(0, "Shares pledged by our Promoters: Nil", "equity shares", page=9)

    report = analyse(rhp, adapters, config, governance(promoter_shares_pledged=nil))

    assert report.unverified_fields == []
    assert flags(report, "promoter_pledge") == []


def test_two_auditor_changes_in_three_years_are_flagged(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance(auditor_changes=AUDITOR_CHANGES))

    [flag] = flags(report, "frequent_auditor_changes")
    assert flag.pages == [8]


def test_one_auditor_change_is_quiet(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance(auditor_changes=AUDITOR_CHANGES[:1]))

    assert flags(report, "frequent_auditor_changes") == []


def test_an_empty_section_raises_no_governance_flag(rhp, adapters, config):
    report = analyse(rhp, adapters, config, governance())

    assert [f for f in report.red_flags if f.section == "promoters_governance"] == []
