"""Financials section agent + its red flags at seam B (spec stories 24-25; plan 5.2, Financials row).

Flag expectations are worked by hand in the comments, never re-derived with fin_calc's formulas.
"""
import time

import pytest
import yaml

import ipo_agents.analysis as analysis
import ipo_agents.section_agent as section_agent
from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded, BudgetMeter
from ipo_agents.config import RunEstimate
from ipo_agents.contracts import (AuditorRemark, Fact, FinancialPeriod, Financials, OfferObjects, Quote,
                                  Restatement)
from tests.conftest import REPO_ROOT, TINY_OFFER_PAGE, make_tiny_rhp
from tests.fakes import NOT_DISCLOSED, ScriptedLLM, make_ipo, offer_objects

pytestmark = pytest.mark.sections("financials")

FIN = 3  # the financials page of the tiny RHP: page 2 is the offer, page 3 starts Restated Financial Information
AUDIT = 4  # the auditor's report, on the next page of the same section


def fin_rhp(tmp_path, rows: list[str], audit: list[str]):
    fin_page = "\n".join(["RESTATED FINANCIAL INFORMATION", "(Rs. million) Fiscal 2024 Fiscal 2023 Fiscal 2022", *rows])
    audit_page = "\n".join(["EXAMINATION REPORT OF THE STATUTORY AUDITOR", *audit])
    return make_tiny_rhp(tmp_path / "fin_rhp.pdf", (TINY_OFFER_PAGE, fin_page, audit_page))


def fact(value, quote, page=FIN, unit="₹ million"):
    return Fact(value=value, unit=unit, page=page, verbatim_quote=quote)


def period(label, end_date, kind="full_year", **facts):
    metrics = {name: NOT_DISCLOSED for name, f in FinancialPeriod.model_fields.items() if f.annotation is Fact}
    return FinancialPeriod(label=label, end_date=end_date, kind=kind, **{**metrics, **facts})


def financials(periods, remarks=(), restatement=None):
    return Financials(periods=list(periods), auditor_remarks=list(remarks), restatement=restatement)


def three_years(rows: dict[str, tuple[str, list[float | None]]]):
    """Fiscal 2024, 2023, 2022 periods (latest first, as RHPs print them) from {field: (row, [fy24, fy23, fy22])}."""
    labels = [("Fiscal 2024", "March 31, 2024"), ("Fiscal 2023", "March 31, 2023"), ("Fiscal 2022", "March 31, 2022")]
    return [period(label, end, **{name: fact(values[i], row) for name, (row, values) in rows.items()
                                  if values[i] is not None})
            for i, (label, end) in enumerate(labels)]


def analyse(rhp, adapters, config, fin):
    adapters.llm = ScriptedLLM({Financials: fin}, models=config.models)
    return analyse_rhp(make_ipo(), rhp, adapters, config)


def flags(report):
    return {f.code: f.status for f in report.financial_flags}


# --- flags fire on window dressing ---------------------------------------------

REVENUE = "Revenue from operations 1,200.00 1,000.00 800.00"
PAT_JUMP = "Profit for the year 180.00 100.00 80.00"
RECV_UP = "Trade receivables 300.00 200.00 150.00"
CFO_NEG = "Net cash used in operating activities (A) (45.50) 60.00 50.00"
STUB_LABEL = "Three months ended June 30, 2024"
STUB_PAT = "Profit for the three months ended June 30, 2024 150.00"
EOM = "Emphasis of Matter: We draw attention to Note 41 on the going concern assessment."
NO_RESTATEMENT = "Total restatement adjustments Nil"


@pytest.fixture
def dressed_rhp(tmp_path):
    return fin_rhp(tmp_path, [REVENUE, PAT_JUMP, RECV_UP, CFO_NEG, STUB_PAT], [EOM, NO_RESTATEMENT])


def dressed(**overrides):
    years = three_years({
        "revenue_from_operations": (REVENUE, [1200.0, 1000.0, 800.0]),
        "pat": (PAT_JUMP, [180.0, 100.0, 80.0]),
        "trade_receivables": (RECV_UP, [300.0, 200.0, 150.0]),
        "cfo": (CFO_NEG, [-45.5, 60.0, 50.0]),
    })
    stub = period(STUB_LABEL, "June 30, 2024", kind="stub", pat=fact(150.0, STUB_PAT))
    fields = dict(periods=[*years, stub],
                  remarks=[AuditorRemark(kind="emphasis_of_matter", summary="going concern",
                                         source=Quote(page=AUDIT, verbatim_quote=EOM))],
                  restatement=Restatement(adjusted=False, source=Quote(page=AUDIT, verbatim_quote=NO_RESTATEMENT)))
    return financials(**{**fields, **overrides})


def test_window_dressed_financials_raise_every_flag(dressed_rhp, adapters, config):
    report = analyse(dressed_rhp, adapters, config, dressed())

    assert report.unverified_fields == []
    assert flags(report) == {
        "pat_positive_cfo_negative": "raised",  # Fiscal 2024: PAT 180 > 0, CFO (45.50) < 0
        "pre_ipo_profit_jump": "raised",  # 100 -> 180 is +80%; prior year 80 -> 100 is +25%; 80% > 2 x 25%
        # receivable days 200/1,000 x 365 = 73.0 -> 300/1,200 x 365 = 91.25: +25% vs revenue +20%
        "receivable_days_rising": "raised",
        "auditor_remarks": "raised",
        "restatement_adjustments": "clear",  # "Nil"
        "stub_period": "raised",
        "free_cash_flow": "not_computable",  # no capex disclosed
    }
    details = {f.code: f.detail for f in report.financial_flags}
    assert "80.0%" in details["pre_ipo_profit_jump"] and "25.0%" in details["pre_ipo_profit_jump"]
    assert "73.0" in details["receivable_days_rising"] and "91.2" in details["receivable_days_rising"]
    assert details["stub_period"] == f"{STUB_LABEL}: not comparable to the full year; not annualised"
    assert "page 4" in details["auditor_remarks"]


def test_a_bracketed_negative_cfo_verifies_as_negative(dressed_rhp, adapters, config):
    sign_dropped = dressed()
    sign_dropped.periods[0].cfo = fact(45.5, CFO_NEG)

    assert analyse(dressed_rhp, adapters, config, dressed()).financials.periods[0].cfo.value == -45.5
    report = analyse(dressed_rhp, adapters, config, sign_dropped)
    assert report.unverified_fields == ["financials.periods[0].cfo"]
    assert flags(report)["pat_positive_cfo_negative"] == "not_computable"


# --- flags stay quiet on healthy financials --------------------------------------

PAT_STEADY = "Profit for the year 110.00 100.00 80.00"
RECV_STEADY = "Trade receivables 230.00 200.00 160.00"
CFO_POS = "Net cash from operating activities 60.00 55.00 50.00"
STUB_CFO = "Net cash used in operating activities for the three months (20.00)"


def test_healthy_financials_stay_quiet_and_the_stub_is_not_annualised(tmp_path, adapters, config):
    rhp = fin_rhp(tmp_path, [REVENUE, PAT_STEADY, RECV_STEADY, CFO_POS, STUB_PAT, STUB_CFO], [NO_RESTATEMENT])
    years = three_years({
        "revenue_from_operations": (REVENUE, [1200.0, 1000.0, 800.0]),
        "pat": (PAT_STEADY, [110.0, 100.0, 80.0]),
        "trade_receivables": (RECV_STEADY, [230.0, 200.0, 160.0]),
        "cfo": (CFO_POS, [60.0, 55.0, 50.0]),
    })
    # The stub's 3-month PAT of 150 would be 600 a year, and its CFO is negative: neither may count.
    stub = period(STUB_LABEL, "June 30, 2024", kind="stub", pat=fact(150.0, STUB_PAT), cfo=fact(-20.0, STUB_CFO))
    fin = financials([stub, *reversed(years)],  # any order: years are ordered by their end date
                     restatement=Restatement(adjusted=False, source=Quote(page=AUDIT, verbatim_quote=NO_RESTATEMENT)))

    report = analyse(rhp, adapters, config, fin)

    assert report.unverified_fields == []
    assert flags(report) == {
        "pat_positive_cfo_negative": "clear",  # Fiscal 2024: PAT 110, CFO 60
        "pre_ipo_profit_jump": "clear",  # 100 -> 110 is +10%; prior +25%; 10% < 50%
        "receivable_days_rising": "clear",  # 200/1,000 x 365 = 73.0 -> 230/1,200 x 365 = 69.96: falling
        "auditor_remarks": "clear",
        "restatement_adjustments": "clear",
        "stub_period": "raised",
        "free_cash_flow": "not_computable",  # no capex disclosed
    }
    [reported_stub] = [p for p in report.financials.periods if p.kind == "stub"]
    assert reported_stub.pat.value == 150.0


def test_a_period_labelled_in_months_is_a_stub_even_if_called_a_full_year(dressed_rhp, adapters, config):
    fin = dressed()
    fin.periods[3].kind = "full_year"  # the three-month column, mislabelled

    assert flags(analyse(dressed_rhp, adapters, config, fin))["pat_positive_cfo_negative"] == "raised"


# --- unverified inputs make a flag not computable --------------------------------

def test_a_flag_whose_input_is_unverified_is_not_computable(dressed_rhp, adapters, config):
    fin = dressed(remarks=[AuditorRemark(kind="qualification", summary="made up", source=Quote(
        page=AUDIT, verbatim_quote="We were unable to obtain sufficient audit evidence on inventories."))])
    fin.periods[2].pat = fact(80.0, PAT_JUMP, page=AUDIT)  # Fiscal 2022 PAT cited on the wrong page

    report = analyse(dressed_rhp, adapters, config, fin)

    assert report.unverified_fields == ["financials.auditor_remarks[0].source", "financials.periods[2].pat"]
    assert report.financials.auditor_remarks[0].source == Quote()
    assert flags(report)["pre_ipo_profit_jump"] == "not_computable"
    assert flags(report)["auditor_remarks"] == "not_computable"
    assert flags(report)["pat_positive_cfo_negative"] == "raised"  # its inputs are still verified


def test_flags_are_not_computable_when_nothing_is_disclosed(dressed_rhp, adapters, config):
    report = analyse(dressed_rhp, adapters, config, financials([]))

    assert flags(report) == {
        "pat_positive_cfo_negative": "not_computable",
        "pre_ipo_profit_jump": "not_computable",
        "receivable_days_rising": "not_computable",
        "auditor_remarks": "clear",
        "restatement_adjustments": "not_computable",
        "stub_period": "clear",
        "free_cash_flow": "not_computable",
    }


# --- map -> reduce ---------------------------------------------------------------

def test_a_financials_section_too_big_for_one_call_is_read_part_by_part(dressed_rhp, adapters, config, monkeypatch):
    # Two parts: pages 3-4 (one ingestion chunk) and page 7. Each partial, and the merge, is "too big" too.
    monkeypatch.setattr(section_agent, "MAX_CALL_CHARS", 100)
    whole = dressed()
    by_page = {"[page 3]\n": financials(whole.periods),
               "[page 7]\n": financials([], whole.auditor_remarks, whole.restatement)}

    def answer(user):
        if user.startswith("[pages "):
            return whole  # a merge
        return next((fin for tag, fin in by_page.items() if user.startswith(tag)), financials([]))

    adapters.llm = llm = ScriptedLLM({Financials: answer}, models=config.models)
    report = analyse_rhp(make_ipo(), dressed_rhp, adapters, config)

    reads = sorted(c["user"].split("\n", 1)[0] for c in llm.calls if not c["user"].startswith("[pages "))
    assert reads == ["[page 3]", "[page 7]"]
    assert llm.calls[-1]["task"] == "financials"
    merges = "".join(c["user"] for c in llm.calls if c["user"].startswith("[pages "))
    assert CFO_NEG in merges and EOM in merges  # both parts reached a merge
    assert report.financials == whole
    assert report.unverified_fields == []


# --- parallel branches share one cap ---------------------------------------------

@pytest.mark.sections("offer_objects", "financials")
def test_parallel_sections_cannot_jointly_overshoot_the_cap(dressed_rhp, adapters, config):
    # Both sections route to terra. Each call's worst case is 70,000 x $12/1M = $0.84 plus a prompt of
    # a few thousand tokens at $2/1M (< $0.01): one fits under $1.50, two ($1.68+) don't.
    no_estimate = RunEstimate(tokens_per_page=0, output_tokens_per_page=0, fixed_usd=0)
    config = config.model_copy(update={"default_max_tokens": 70_000, "budget": config.budget.model_copy(
        update={"cap_usd": 1.50, "estimate": no_estimate})})

    def slow(answer):
        def reply(user):
            time.sleep(0.5)  # hold the first call in flight while the other branch asks to start
            return answer
        return reply

    adapters.llm = llm = ScriptedLLM({OfferObjects: slow(offer_objects()), Financials: slow(financials([]))},
                                     models=config.models)

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), dressed_rhp, adapters, config)

    assert len(llm.calls) == 1
    deadline = time.monotonic() + 5
    while (spent := BudgetMeter(make_ipo().ipo_id, config).spent) == 0 and time.monotonic() < deadline:
        time.sleep(0.05)  # the call still in flight settles after the other branch raised
    assert 0 < spent <= 1.50


# --- golden RHPs ------------------------------------------------------------------

GOLDEN_SLUGS = ["bajaj-housing-finance", "hyundai-motor-india", "ola-electric"]
# Golden expected.yaml keys -> FinancialPeriod fields (all Fiscal 2024).
GOLDEN_FIELDS = {
    "revenue_from_operations_fy2024": "revenue_from_operations",
    "pat_fy2024": "pat",
    "net_worth_fy2024": "net_worth",
    "cfo_fy2024": "cfo",
}
# expected.yaml cites most of these on the offer-document summary pages, which the Financials agent
# never sees. These are the same numbers where the financials section prints them (restated statements
# or MD&A), so the agent can cite them; value and unit still come from expected.yaml. Ola's CFO is
# re-cited from the cash flow statement because the MD&A prose "used in ... ₹ 6,330.87" drops the sign.
FINANCIALS_CITATIONS = {
    ("bajaj-housing-finance", "pat_fy2024"): (321, "Profit after tax 4,826.1 4,618.0 17,312.2 12,578.0 7,096.2"),
    ("bajaj-housing-finance", "net_worth_fy2024"): (
        432, "Our net worth (as restated) was ₹ 67,413.6 million, ₹ 105,031.9 million, ₹ 109,649.9 million, "
             "₹ 122,335.0 million"),
    ("hyundai-motor-india", "revenue_from_operations_fy2024"): (
        272, "Revenue from operations 29 173,442.34 166,235.11 698,290.57 603,075.80 473,784.32"),
    ("hyundai-motor-india", "pat_fy2024"): (
        272, "Profit for the period / year 14,896.52 13,291.86 60,600.44 47,092.50 29,015.91"),
    ("hyundai-motor-india", "net_worth_fy2024"): (
        352, "Net Worth 121,487.10 213,808.29 106,656.57 200,548.18 168,562.55"),
    ("ola-electric", "revenue_from_operations_fy2024"): (274, "Revenue from operations 21 50,098.31 26,309.27 3,734.23"),
    ("ola-electric", "pat_fy2024"): (274, "Loss for the year (VII-VIII) (15,844.00) (14,720.79) (7,841.50)"),
    ("ola-electric", "net_worth_fy2024"): (331, "Net Worth (D) 20,193.39 23,564.44 36,614.52"),
    ("ola-electric", "cfo_fy2024"): (
        278, "Net cash used in operating activities (A) (6,330.87) (15,072.71) (8,849.54)"),
}


def _expected(slug):
    return yaml.safe_load((REPO_ROOT / "tests" / "golden" / slug / "expected.yaml").read_text(encoding="utf-8"))


GOLDEN_CASES = [(slug, GOLDEN_FIELDS[f["key"]]) for slug in GOLDEN_SLUGS
                for f in _expected(slug)["facts"] if f["key"] in GOLDEN_FIELDS]


def golden_facts(slug) -> dict[str, Fact]:
    """The golden Fiscal 2024 facts, cited on financials pages (needs_human_review: checks verifier agreement)."""
    facts = {}
    for f in _expected(slug)["facts"]:
        if f["key"] in GOLDEN_FIELDS:
            page, quote = FINANCIALS_CITATIONS.get((slug, f["key"]), (f["page"], f["verbatim_quote"]))
            facts[GOLDEN_FIELDS[f["key"]]] = Fact(value=f["value"], unit=f["unit"], page=page, verbatim_quote=quote)
    return facts


def analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, facts):
    monkeypatch.setattr(analysis, "ingest", lambda path: golden_doc(slug))  # ingested once per session
    fin = financials([period("Fiscal 2024", "March 31, 2024", **facts)])
    return analyse(golden_rhp(slug), adapters, config, fin)


@pytest.mark.parametrize("slug", GOLDEN_SLUGS)
def test_golden_financial_facts_verify_on_the_real_financials_pages(slug, golden_rhp, golden_doc, adapters, config,
                                                                    monkeypatch):
    facts = golden_facts(slug)
    assert set(facts) == set(GOLDEN_FIELDS.values())

    report = analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, facts)

    assert report.unverified_fields == []
    assert {name: getattr(report.financials.periods[0], name).value for name in facts} == {
        name: f.value for name, f in facts.items()}


@pytest.mark.parametrize("slug, field", GOLDEN_CASES)
def test_a_golden_financial_fact_altered_by_one_is_the_only_one_rejected(slug, field, golden_rhp, golden_doc, adapters,
                                                                        config, monkeypatch):
    facts = golden_facts(slug)
    facts[field] = facts[field].model_copy(update={"value": facts[field].value + 1})

    report = analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, facts)

    assert report.unverified_fields == [f"financials.periods[0].{field}"]
