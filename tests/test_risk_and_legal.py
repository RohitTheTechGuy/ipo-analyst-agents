"""Risk Factors + Fine Print & Legal agents and the legal checks at seam B (spec stories 26, 27; plan 5.2).

Check expectations are worked by hand in the comments, never re-derived with legal_calc's formulas.
"""
import pytest
import yaml

import ipo_agents.analysis as analysis
import ipo_agents.ingestion as ingestion
import ipo_agents.section_agent as section_agent
from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import (MAX_LISTED_RISKS, Fact, FinancialPeriod, Financials, FinePrintLegal, LitigationLine,
                                  PendingApproval, Quote, RelatedPartyLine, RiskFactor, RiskFactors)
from ipo_agents.sections import SECTIONS
from tests.conftest import REPO_ROOT, TINY_OFFER_PAGE, make_tiny_rhp
from tests.fakes import NOT_DISCLOSED, ScriptedLLM, make_ipo

pytestmark = pytest.mark.sections("financials", "fine_print_legal")

# The tiny legal RHP: page 2 the offer, 3 restated financials, 4 other financial information (contingent
# liabilities and related party transactions, read by Fine Print too), 5 litigation, 6 approvals.
FIN, OTHER_FIN, LIT, APPROVALS = 3, 4, 5, 6

REVENUE = "Revenue from operations 1,000.00 800.00"
NET_WORTH = "Net worth 400.00 350.00"
CL_HIGH = "Contingent liabilities not provided for 120.00"
CL_LOW = "Contingent liabilities not provided for 80.00"
RPT_SALES = "Sale of goods to Acme Holdings Private Limited 90.00"
RPT_PURCHASES_HIGH = "Purchase of components from Acme Parts Limited 160.00"
RPT_PURCHASES_LOW = "Purchase of components from Acme Parts Limited 140.00"
RPT_LOAN = "Loan given to Acme Holdings Private Limited 25.00"
CRIMINAL = "Criminal proceedings against our Promoters 2 12.50"
CRIMINAL_NIL = "Criminal proceedings against our Promoters Nil"
TAX = "Tax proceedings involving our Company 7 340.00"
EXPIRED = "Consent to operate for the Pune plant expired on March 31, 2024; renewal application pending"


def legal_rhp(tmp_path, other_fin: list[str], litigation: list[str], approvals: list[str]):
    pages = (
        TINY_OFFER_PAGE,
        "\n".join(["RESTATED FINANCIAL INFORMATION", "(Rs. million) Fiscal 2024 Fiscal 2023", REVENUE, NET_WORTH]),
        "\n".join(["OTHER FINANCIAL INFORMATION", "(Rs. million) Fiscal 2024",
                   "Related party transactions", *other_fin]),
        "\n".join(["OUTSTANDING LITIGATION AND MATERIAL DEVELOPMENTS", "(Rs. million) Number Amount", *litigation]),
        "\n".join(["GOVERNMENT AND OTHER APPROVALS", *approvals]),
    )
    return make_tiny_rhp(tmp_path / "legal_rhp.pdf", pages)


def fact(value, quote, page, unit="₹ million"):
    return Fact(value=value, unit=unit, page=page, verbatim_quote=quote)


def fy2024(revenue=True, net_worth=True) -> Financials:
    metrics = {name: NOT_DISCLOSED for name, f in FinancialPeriod.model_fields.items() if f.annotation is Fact}
    if revenue:
        metrics["revenue_from_operations"] = fact(1000.0, REVENUE, FIN)
    if net_worth:
        metrics["net_worth"] = fact(400.0, NET_WORTH, FIN)
    year = FinancialPeriod(label="Fiscal 2024", end_date="March 31, 2024", kind="full_year", **metrics)
    return Financials(periods=[year], auditor_remarks=[], restatement=None)


def rpt(category, counterparty, value, quote, promoter_entity=True):
    return RelatedPartyLine(category=category, counterparty=counterparty, promoter_entity=promoter_entity,
                            period="Fiscal 2024", amount=fact(value, quote, OTHER_FIN))


def lit(party, case_type, count, amount, quote):
    return LitigationLine(party=party, case_type=case_type, direction="against",
                          count=fact(count, quote, LIT, unit="cases"),
                          amount=fact(amount, quote, LIT) if amount is not None else NOT_DISCLOSED)


def legal(**fields) -> FinePrintLegal:
    defaults = dict(litigation=[], contingent_liabilities=NOT_DISCLOSED, related_party=[], approvals=[],
                    material_contracts=[])
    return FinePrintLegal(**{**defaults, **fields})


RED_LEGAL = dict(
    litigation=[lit("promoters", "criminal", 2, 12.5, CRIMINAL), lit("company", "tax", 7, 340.0, TAX)],
    contingent_liabilities=fact(120.0, CL_HIGH, OTHER_FIN),
    related_party=[rpt("sales", "Acme Holdings Private Limited", 90.0, RPT_SALES),
                   rpt("purchases", "Acme Parts Limited", 160.0, RPT_PURCHASES_HIGH),
                   rpt("loans", "Acme Holdings Private Limited", 25.0, RPT_LOAN)],
    approvals=[PendingApproval(name="Consent to operate, Pune plant", status="expired", critical=True,
                               source=Quote(page=APPROVALS, verbatim_quote=EXPIRED))],
)


def analyse(rhp, adapters, config, fine_print, fin=None):
    responses = {FinePrintLegal: fine_print, **({Financials: fin} if fin is not None else {})}
    adapters.llm = ScriptedLLM(responses, models=config.models)
    return analyse_rhp(make_ipo(), rhp, adapters, config)


def checks(report):
    return {c.code: c.status for c in report.legal_checks}


@pytest.fixture
def red_rhp(tmp_path):
    return legal_rhp(tmp_path, [CL_HIGH, RPT_SALES, RPT_PURCHASES_HIGH, RPT_LOAN], [CRIMINAL, TAX], [EXPIRED])


# --- checks fire --------------------------------------------------------------------

def test_hidden_liabilities_raise_every_legal_check(red_rhp, adapters, config):
    report = analyse(red_rhp, adapters, config, legal(**RED_LEGAL), fy2024())

    assert report.unverified_fields == []
    assert checks(report) == {
        "contingent_liabilities_vs_net_worth": "raised",  # 120 / 400 = 30.0% > 25%
        "rpt_vs_revenue": "raised",  # purchases 160 / 1,000 = 16.0% > 15%; sales 90 / 1,000 = 9.0%
        "loans_to_promoter_entities": "raised",  # 25.00
        "criminal_cases_against_promoters": "raised",  # 2 cases
        "critical_approvals_pending": "raised",  # the Pune consent to operate expired
    }
    details = {c.code: c.detail for c in report.legal_checks}
    assert "30.0%" in details["contingent_liabilities_vs_net_worth"]
    assert "16.0%" in details["rpt_vs_revenue"] and "9.0%" in details["rpt_vs_revenue"]
    # Raised checks are warnings in the summary, each with the pages it rests on.
    flags = {f.code: f for f in report.flags}
    assert {code: flags[code].severity for code in map(str.upper, checks(report))} == {
        "CONTINGENT_LIABILITIES_VS_NET_WORTH": "warning", "RPT_VS_REVENUE": "warning",
        "LOANS_TO_PROMOTER_ENTITIES": "warning", "CRIMINAL_CASES_AGAINST_PROMOTERS": "warning",
        "CRITICAL_APPROVALS_PENDING": "warning"}
    assert flags["CONTINGENT_LIABILITIES_VS_NET_WORTH"].evidence_pages == [FIN, OTHER_FIN]
    assert flags["CRIMINAL_CASES_AGAINST_PROMOTERS"].evidence_pages == [LIT]


def test_litigation_counts_and_amounts_by_party_and_type_match_the_fixture(red_rhp, adapters, config):
    report = analyse(red_rhp, adapters, config, legal(**RED_LEGAL), fy2024())

    by_kind = {(l.party, l.case_type): (l.count.value, l.amount.value) for l in report.fine_print_legal.litigation}
    assert by_kind == {("promoters", "criminal"): (2.0, 12.5), ("company", "tax"): (7.0, 340.0)}


# --- checks stay quiet -------------------------------------------------------------

def test_clean_fine_print_stays_quiet(tmp_path, adapters, config):
    rhp = legal_rhp(tmp_path, [CL_LOW, RPT_SALES, RPT_PURCHASES_LOW], [CRIMINAL_NIL, TAX], ["No approval is pending."])
    clean = legal(
        litigation=[lit("promoters", "criminal", 0, None, CRIMINAL_NIL), lit("company", "tax", 7, 340.0, TAX)],
        contingent_liabilities=fact(80.0, CL_LOW, OTHER_FIN),
        related_party=[rpt("sales", "Acme Holdings Private Limited", 90.0, RPT_SALES),
                       rpt("purchases", "Acme Parts Limited", 140.0, RPT_PURCHASES_LOW)],
    )

    report = analyse(rhp, adapters, config, clean, fy2024())

    assert report.unverified_fields == []
    assert checks(report) == {
        "contingent_liabilities_vs_net_worth": "clear",  # 80 / 400 = 20.0%
        "rpt_vs_revenue": "clear",  # purchases 140 / 1,000 = 14.0%; sales 9.0%
        "loans_to_promoter_entities": "clear",  # no loan to a related party
        "criminal_cases_against_promoters": "clear",  # Nil
        "critical_approvals_pending": "clear",
    }
    assert not [f for f in report.flags if f.code in {c.upper() for c in checks(report)}]


def test_a_loan_to_a_party_that_is_not_a_promoter_entity_does_not_raise(red_rhp, adapters, config):
    fine_print = legal(**{**RED_LEGAL, "related_party": [
        rpt("loans", "Acme Holdings Private Limited", 25.0, RPT_LOAN, promoter_entity=False)]})

    assert checks(analyse(red_rhp, adapters, config, fine_print, fy2024()))["loans_to_promoter_entities"] == "clear"


# --- unverified inputs are not computable ---------------------------------------------

def test_an_unverified_input_makes_its_check_not_computable(red_rhp, adapters, config):
    fine_print = legal(**{
        **RED_LEGAL,
        "contingent_liabilities": fact(120.0, CL_HIGH, LIT),  # cited on the wrong page
        "litigation": [lit("promoters", "criminal", 3, 12.5, CRIMINAL)],  # 3 is not printed
        "approvals": [PendingApproval(name="Factory licence", status="pending", critical=True, source=Quote(
            page=APPROVALS, verbatim_quote="The factory licence for the Chennai plant is pending renewal"))],
    })

    report = analyse(red_rhp, adapters, config, fine_print, fy2024())

    assert report.unverified_fields == ["fine_print_legal.approvals[0].source",
                                        "fine_print_legal.contingent_liabilities",
                                        "fine_print_legal.litigation[0].count"]
    assert checks(report)["contingent_liabilities_vs_net_worth"] == "not_computable"
    assert checks(report)["criminal_cases_against_promoters"] == "not_computable"
    assert checks(report)["critical_approvals_pending"] == "not_computable"
    assert checks(report)["rpt_vs_revenue"] == "raised"  # its inputs are still verified


@pytest.mark.sections("fine_print_legal")
def test_ratio_checks_need_verified_financials(red_rhp, adapters, config):
    report = analyse(red_rhp, adapters, config, legal(**RED_LEGAL))  # no Financials agent ran

    assert report.financials is None
    assert checks(report)["contingent_liabilities_vs_net_worth"] == "not_computable"
    assert checks(report)["rpt_vs_revenue"] == "not_computable"
    assert checks(report)["loans_to_promoter_entities"] == "raised"


def test_unverified_net_worth_or_revenue_is_not_computable(red_rhp, adapters, config):
    report = analyse(red_rhp, adapters, config, legal(**RED_LEGAL), fy2024(revenue=False, net_worth=False))

    assert checks(report)["contingent_liabilities_vs_net_worth"] == "not_computable"
    assert checks(report)["rpt_vs_revenue"] == "not_computable"


# --- Fine Print reads the contingent-liability and RPT pages of other sections ----------

def test_fine_print_reads_contingent_and_rpt_pages_from_the_financials(red_rhp, adapters, config):
    adapters.llm = llm = ScriptedLLM({FinePrintLegal: legal(**RED_LEGAL), Financials: fy2024()},
                                     models=config.models)
    analyse_rhp(make_ipo(), red_rhp, adapters, config)

    [call] = [c for c in llm.calls if c["schema"] is FinePrintLegal]
    assert f"[page {OTHER_FIN}]" in call["user"] and CL_HIGH in call["user"]
    assert f"[page {FIN}]" not in call["user"]  # a financials page without either is not read


# --- risk factors ------------------------------------------------------------------------

RISK_PAGE = 2  # the tiny risk RHP puts its risk pages from page 2 on


def risk_rhp(tmp_path, pages: list[list[str]]):
    texts = tuple("\n".join(["RISK FACTORS" if i == 0 else "Risk factors (continued)", *lines])
                  for i, lines in enumerate(pages))
    return make_tiny_rhp(tmp_path / "risk_rhp.pdf", (TINY_OFFER_PAGE, *texts))


def risk(title, page, kind="company_specific", rank=None, figure=None, topic=None):
    return RiskFactor(topic=topic or title[:30], title=Quote(page=page, verbatim_quote=title), kind=kind,
                      quantified=figure is not None, figure=figure, severity_rank=rank)


GENERIC = "Political instability or changes in the Government of India could adversely affect our business"
PLANT = "We depend on a single manufacturing plant at Pune for 100% of our production"
CUSTOMER = "Our top customer accounted for 62.40% of our revenue from operations in Fiscal 2024"


@pytest.mark.sections("risk_factors")
def test_company_specific_risks_are_separated_from_boilerplate_and_the_top_10_ranked(tmp_path, adapters, config):
    titles = [f"Risk number {k}: our plant may fail to meet demand" for k in range(12)]
    rhp = risk_rhp(tmp_path, [[GENERIC, *titles]])
    page = RISK_PAGE + 1
    # Scripted ranks: titles[k] is ranked 12 - k, so titles[11] is the most severe.
    risks = [risk(GENERIC, page, kind="generic"), *(risk(t, page, rank=12 - k) for k, t in enumerate(titles))]
    adapters.llm = ScriptedLLM({RiskFactors: RiskFactors(risks=risks)}, models=config.models)

    report = analyse_rhp(make_ipo(), rhp, adapters, config)

    assert report.unverified_fields == []
    rf = report.risk_factors
    assert [r.kind for r in rf.risks] == ["generic", *["company_specific"] * 12]  # preserved as scripted
    top = rf.top_company_specific
    assert [r.title.verbatim_quote for r in top] == [titles[k] for k in range(11, 1, -1)]  # ranks 1..10
    assert GENERIC not in [r.title.verbatim_quote for r in top]


@pytest.mark.sections("risk_factors")
def test_a_risk_whose_title_is_not_printed_is_left_out_of_the_top_10(tmp_path, adapters, config):
    rhp = risk_rhp(tmp_path, [[PLANT, GENERIC]])
    made_up = "We may lose our licence to sell scooters in every state of India"
    risks = [risk(made_up, RISK_PAGE + 1, rank=1), risk(PLANT, RISK_PAGE + 1, rank=2)]
    adapters.llm = ScriptedLLM({RiskFactors: RiskFactors(risks=risks)}, models=config.models)

    report = analyse_rhp(make_ipo(), rhp, adapters, config)

    assert report.unverified_fields == ["risk_factors.risks[0].title"]
    assert [r.title.verbatim_quote for r in report.risk_factors.top_company_specific] == [PLANT]


@pytest.mark.sections("risk_factors")
def test_a_long_risk_section_is_read_part_by_part_and_merged(tmp_path, adapters, config, monkeypatch):
    # Three risk pages, each its own chunk and part; the merge keeps the boilerplate apart and re-ranks.
    monkeypatch.setattr(ingestion, "MAX_CHUNK_CHARS", 100)
    monkeypatch.setattr(section_agent, "MAX_CALL_CHARS", 100)
    rhp = risk_rhp(tmp_path, [[GENERIC], [PLANT], [CUSTOMER]])
    p_generic, p_plant, p_customer = RISK_PAGE + 1, RISK_PAGE + 2, RISK_PAGE + 3
    share = Fact(value=62.4, unit="%", page=p_customer, verbatim_quote=CUSTOMER)
    by_page = {f"[page {p_generic}]\n": [risk(GENERIC, p_generic, kind="generic")],
               f"[page {p_plant}]\n": [risk(PLANT, p_plant, rank=1)],
               f"[page {p_customer}]\n": [risk(CUSTOMER, p_customer, rank=1, figure=share)]}
    merged = RiskFactors(risks=[risk(GENERIC, p_generic, kind="generic"), risk(CUSTOMER, p_customer, rank=1, figure=share),
                                risk(PLANT, p_plant, rank=2)])

    def answer(user):
        if user.startswith("[pages "):
            return merged
        return RiskFactors(risks=next((r for tag, r in by_page.items() if user.startswith(tag)), []))

    adapters.llm = llm = ScriptedLLM({RiskFactors: answer}, models=config.models)
    report = analyse_rhp(make_ipo(), rhp, adapters, config)

    reads = [c for c in llm.calls if not c["user"].startswith("[pages ")]
    assert sorted(c["user"].split("\n", 1)[0] for c in reads) == [f"[page {p}]" for p in (p_generic, p_plant, p_customer, p_customer + 1)]  # + the tiny RHP's own risk page
    assert all(c["task"] == "map_extract" for c in reads)
    assert llm.calls[-1]["task"] == "risk_factors"
    merges = "".join(c["user"] for c in llm.calls if c["user"].startswith("[pages "))
    assert PLANT in merges and CUSTOMER in merges
    assert report.unverified_fields == []
    rf = report.risk_factors
    assert [r.title.verbatim_quote for r in rf.top_company_specific] == [CUSTOMER, PLANT]
    assert [r.kind for r in rf.risks if r.title.verbatim_quote == GENERIC] == ["generic"]
    [quantified] = [r for r in rf.risks if r.quantified]
    assert quantified.figure.value == 62.4  # a risk that states a number carries it as a verified Fact


# --- golden RHPs ------------------------------------------------------------------------------

# expected.yaml keys for the contingent-liability total, per golden RHP.
GOLDEN_CONTINGENT = {"bajaj-housing-finance": "contingent_liabilities",
                     "hyundai-motor-india": "contingent_liabilities_claims_not_acknowledged"}
# Neither figure is printed in the litigation / approvals / material-contracts chapters; Fine Print reads
# them from the pages of other sections that mention contingent liabilities (FINE_PRINT_BORROW). Bajaj's
# golden citation is the offer-document summary (page 32); it is re-cited here where Other Financial
# Information prints the same total. Hyundai's 31,461.02 is printed only in the summary (page 24; the
# financials print its parts), so it keeps its golden citation. Value and unit still come from expected.yaml.
CONTINGENT_CITATIONS = {
    "bajaj-housing-finance": (
        443, "we had contingent liabilities as per Ind AS 37 on “Provisions, Contingent Liabilities and "
             "Contingent Assets” not provided for amounting to ₹ 35.3 million"),
}


def golden_contingent(slug) -> Fact:
    [f] = [f for f in yaml.safe_load((REPO_ROOT / "tests" / "golden" / slug / "expected.yaml").read_text(
        encoding="utf-8"))["facts"] if f["key"] == GOLDEN_CONTINGENT[slug]]
    page, quote = CONTINGENT_CITATIONS.get(slug, (f["page"], f["verbatim_quote"]))
    return Fact(value=f["value"], unit=f["unit"], page=page, verbatim_quote=quote)


def analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, contingent):
    monkeypatch.setattr(analysis, "ingest", lambda path: golden_doc(slug))  # ingested once per session
    return analyse(golden_rhp(slug), adapters, config, legal(contingent_liabilities=contingent))


@pytest.mark.sections("fine_print_legal")
@pytest.mark.parametrize("slug", sorted(GOLDEN_CONTINGENT))
def test_golden_contingent_liabilities_verify_on_the_pages_fine_print_reads(slug, golden_rhp, golden_doc, adapters,
                                                                           config, monkeypatch):
    contingent = golden_contingent(slug)

    report = analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, contingent)

    assert report.unverified_fields == []
    assert report.fine_print_legal.contingent_liabilities.value == contingent.value


@pytest.mark.sections("fine_print_legal")
@pytest.mark.parametrize("slug", sorted(GOLDEN_CONTINGENT))
def test_golden_contingent_liabilities_altered_by_one_are_rejected(slug, golden_rhp, golden_doc, adapters, config,
                                                                  monkeypatch):
    contingent = golden_contingent(slug)
    contingent = contingent.model_copy(update={"value": contingent.value + 1})

    report = analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, contingent)

    assert report.unverified_fields == ["fine_print_legal.contingent_liabilities"]


# --- output size: risk factors are capped, large merges get more output tokens ------------------

def test_a_risk_list_over_the_cap_keeps_every_quantified_risk_then_the_most_severe():
    # 40 risks: 5 generic, 30 company-specific ranked 30..1, and 5 quantified ones ranked 31-35.
    # Kept (25): the 5 quantified, then ranks 1-20; in printed order; the counts are of all 40.
    share = Fact(value=10.0, unit="%", page=3, verbatim_quote="10%")
    printed = ([risk(f"generic {k}", 3, kind="generic") for k in range(5)]
               + [risk(f"ranked {k}", 3, rank=k) for k in range(30, 0, -1)]
               + [risk(f"quantified {k}", 3, rank=30 + k, figure=share) for k in range(1, 6)])

    rf = RiskFactors(risks=printed)

    assert len(rf.risks) == MAX_LISTED_RISKS == 25
    assert [r.title.verbatim_quote for r in rf.risks] == (
        [f"ranked {k}" for k in range(20, 0, -1)] + [f"quantified {k}" for k in range(1, 6)])
    assert (rf.total_count, rf.generic_count) == (40, 5)


def test_risk_counts_the_model_gives_are_kept_and_a_short_list_is_untouched():
    risks = [risk(GENERIC, 3, kind="generic"), risk(PLANT, 3, rank=1)]

    rf = RiskFactors(risks=risks, total_count=48, generic_count=31)

    assert rf.risks == risks
    assert (rf.n_risks, rf.n_generic) == (48, 31)
    uncounted = RiskFactors(risks=risks)
    assert (uncounted.n_risks, uncounted.n_generic) == (2, 1)  # no counts given: the list's own


@pytest.mark.sections("risk_factors")
def test_a_long_risk_section_merges_with_the_larger_output_allowance(tmp_path, adapters, config, monkeypatch):
    monkeypatch.setattr(ingestion, "MAX_CHUNK_CHARS", 100)
    monkeypatch.setattr(section_agent, "MAX_CALL_CHARS", 100)
    rhp = risk_rhp(tmp_path, [[GENERIC], [PLANT]])
    adapters.llm = llm = ScriptedLLM({}, models=config.models)

    analyse_rhp(make_ipo(), rhp, adapters, config)

    reads = [c for c in llm.calls if not c["user"].startswith("[pages ")]
    merges = [c for c in llm.calls if c["user"].startswith("[pages ")]  # batch rounds, then the final merge
    merge = llm.calls[-1]
    assert merge["task"] == "risk_factors"
    assert reads and {(c["task"], c["max_tokens"]) for c in reads} == {("map_extract", 4000)}
    assert {c["max_tokens"] for c in merges} == {8000}
    assert f"at most {MAX_LISTED_RISKS}" in merge["system"]
    assert SECTIONS["financials"].reduce_max_tokens == 8000


@pytest.mark.sections("risk_factors")
def test_a_failing_map_part_stops_the_parts_still_queued(tmp_path, adapters, config, monkeypatch):
    # Four parts, one worker: the first part's call fails (not a parse error), so the other three are
    # never sent. (With pool.map they all ran, and spent, before the error surfaced.)
    monkeypatch.setattr(ingestion, "MAX_CHUNK_CHARS", 100)
    monkeypatch.setattr(section_agent, "MAX_CALL_CHARS", 100)
    monkeypatch.setattr(section_agent, "MAP_WORKERS", 1)
    rhp = risk_rhp(tmp_path, [[GENERIC], [PLANT], [CUSTOMER]])
    adapters.llm = llm = ScriptedLLM({}, models=config.models, errors=[TimeoutError("read timed out")])

    with pytest.raises(TimeoutError):
        analyse_rhp(make_ipo(), rhp, adapters, config)

    assert len(llm.calls) == 1
