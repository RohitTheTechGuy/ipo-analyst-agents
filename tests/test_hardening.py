"""Pre-live hardening (ticket 26): optional parts fail soft, prose is grounded, paths and schemas are safe.

Two rules: no failure in an optional part may lose a verified analysis, and no unverified number may reach
the report. Seam B (analyse_rhp) and seam A (run_daily) where possible.
"""
import logging
from datetime import date

import pytest

import ipo_agents.analysis as analysis
import ipo_agents.runner as runner
from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded
from ipo_agents.calc import compute_offer
from ipo_agents.config import load_config
from ipo_agents.contracts import (Advice, AuditorRemark, BusinessIndustry, CitedText, Derived, Fact, IndustryClaim,
                                  MaterialItem, MultipleChoice, OfferObjects, OtherDisclosures, Promoter, Quote,
                                  RiskFactor, RiskFactors, SectionSummary, SuggestedPeer, Synthesis, ValuationPlan)
from ipo_agents.grounding import ground_section
from ipo_agents.runner import run_daily
from ipo_agents.sentiment import SentimentPoints, StanceTag
from tests.conftest import REPO_ROOT, make_tiny_rhp
from tests.fakes import FakeExchange, FakePeerData, FakeSearch, ScriptedLLM, found_nothing, make_ipo, offer_objects
from tests.test_peer_valuation import analyse as analyse_valuation, fin_answer, make_rhp as make_valuation_rhp, plan
from tests.test_sentiment import hit, sentiment_llm
from tests.test_synthesizer import OFFER_ANSWER, synthesis

TODAY = date(2026, 10, 13)
VALUATION_SECTIONS = ("offer_objects", "financials", "offer_price_basis", "business_industry")
ISSUE = Fact(value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore")


def crore(value, quote, page=2):
    return Fact(value=value, unit="₹ crore", page=page, verbatim_quote=quote)


# --- B3: optional parts fail soft ----------------------------------------------------------------

def one_article(adapters):
    adapters.search = FakeSearch({"Acme Ltd IPO review": [hit("a", date(2026, 10, 8))]})


@pytest.mark.parametrize("error", [RuntimeError("model returned junk"), BudgetExceeded("cap reached")])
def test_a_failing_stance_call_makes_sentiment_unavailable_and_keeps_the_analysis(tiny_rhp, adapters, config,
                                                                                    error, caplog):
    one_article(adapters)
    adapters.llm = sentiment_llm(config, {"a": "subscribe"})
    adapters.llm.responses[StanceTag] = error

    with caplog.at_level(logging.WARNING):
        report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.status == "complete"
    assert report.offer.issue_size.value == 1250.0
    assert report.sentiment.available is False
    assert report.sentiment.note.startswith("sentiment unavailable: ")
    assert type(error).__name__ in report.sentiment.note
    assert any(r.levelno == logging.WARNING and "sentiment" in r.getMessage() for r in caplog.records)


def test_a_failing_aggregate_call_makes_sentiment_unavailable(tiny_rhp, adapters, config):
    one_article(adapters)
    adapters.llm = sentiment_llm(config, {"a": "subscribe"})
    adapters.llm.responses[SentimentPoints] = BudgetExceeded("cap reached")

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.status == "complete"
    assert report.sentiment.available is False and "BudgetExceeded" in report.sentiment.note


@pytest.mark.sections(*VALUATION_SECTIONS)
@pytest.mark.parametrize("error", [RuntimeError("model returned junk"), BudgetExceeded("cap reached")])
def test_a_failing_peer_valuation_leaves_no_valuation_and_a_note(tmp_path, adapters, config, error):
    report = analyse_valuation(make_valuation_rhp(tmp_path), adapters, config, valuation_plan=error)

    assert report.status == "complete"
    assert report.valuation is None
    assert report.financials is not None and report.offer is not None
    assert f"valuation: failed ({type(error).__name__})" in report.unverified_fields


@pytest.mark.synthesizer
def test_a_failing_synthesizer_keeps_the_report_and_says_why_there_is_no_verdict(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: RuntimeError("model returned junk")},
                               models=config.models)

    summary = run_daily(TODAY, adapters, config)

    assert summary.analysed == ["ACME-2026-10-13"]
    [msg] = adapters.notifier.messages
    assert "Analyst view: not reached (synthesizer failed)" in msg
    assert "Issue: ₹1,250 crore (p. 2)" in msg


@pytest.mark.synthesizer
def test_a_report_with_nothing_verified_still_says_nothing_verified(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = ScriptedLLM({}, models=config.models)

    run_daily(TODAY, adapters, config)

    [msg] = adapters.notifier.messages
    assert "Analyst view: not reached (nothing verified)" in msg


@pytest.mark.sections("offer_objects", "fine_print_legal")
@pytest.mark.parametrize("block", ["compute_offer", "legal_checks", "compute_sections", "cross_checks"])
def test_a_failing_derived_block_is_dropped_and_named_not_fatal(tiny_rhp, adapters, config, monkeypatch, block,
                                                                 caplog):
    def boom(*args, **kwargs):
        raise ZeroDivisionError("float division by zero")
    monkeypatch.setattr(analysis, block, boom)
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER}, models=config.models)

    with caplog.at_level(logging.WARNING):
        report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.status == "complete"
    assert report.offer.issue_size.value == 1250.0
    name = {"compute_offer": "offer", "legal_checks": "legal", "compute_sections": "sections",
            "cross_checks": "cross_checks"}[block]
    assert f"calculations.{name}: failed" in report.unverified_fields
    if block != "compute_offer":
        assert report.calculations["issue_size_cr"].value == 1250.0
    assert any("calculations" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_a_nil_issue_size_is_not_a_divisor():
    # "Nil" verifies as 0: OFS ₹700 / issue ₹0 is not computable (not a ZeroDivisionError), so the split
    # falls back to fresh + OFS: 700 / (0 + 700) = 100% OFS.
    offer = offer_objects(issue_size=crore(0.0, "Total issue size Nil"), ofs_amount=crore(700.0, "OFS Rs. 700 crore"),
                          fresh_issue=crore(0.0, "Fresh issue Nil"))
    calcs = compute_offer(offer, make_ipo())

    assert (calcs["ofs_pct"].value, calcs["fresh_pct"].value) == (100.0, 0.0)
    # With the OFS also Nil there is nothing to split: not computable.
    nil = compute_offer(offer.model_copy(update={"ofs_amount": crore(0.0, "Offer for sale Nil")}), make_ipo())
    assert nil["ofs_pct"].value is None and nil["ofs_pct"].not_computable_reason


def test_a_zero_upper_band_is_not_a_divisor():
    offer = offer_objects(fresh_issue=crore(500.0, "Fresh issue Rs. 500 crore"),
                          pre_issue_shares=Fact(value=1e8, unit="equity shares", page=2, verbatim_quote="100,000,000"))
    calcs = compute_offer(offer, make_ipo().model_copy(update={"price_band": "₹0"}))

    assert calcs["dilution_pct"].value is None
    assert "price_band" in calcs["dilution_pct"].not_computable_reason


def test_price_to_book_with_a_zero_post_issue_book_is_not_computable():
    from ipo_agents.valuation import _Year, ipo_multiple

    fin = fin_answer()
    latest = _Year(fin, fin.periods[0])  # FY2024 net worth ₹350 crore
    mcap = Derived(value=1050.0, unit="₹ crore")
    fresh = Derived(value=-350.0, unit="₹ crore", basis="computed from RHP figures")

    pb = ipo_multiple("pb", mcap, latest, fresh)

    assert pb.value is None and pb.not_computable_reason


def test_a_summary_that_cannot_be_formatted_does_not_stop_the_other_ipos(adapters, config, monkeypatch):
    second = make_ipo().model_copy(update={"ipo_id": "BETA-2026-10-13", "name": "Beta Ltd", "symbol": "BETA"})
    adapters.exchange = FakeExchange([make_ipo(), second])
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER}, models=config.models)
    original = runner.format_summary

    def flaky(report):
        if report.ipo.name == "Acme Ltd":
            raise KeyError("verdict label")
        return original(report)
    monkeypatch.setattr(runner, "format_summary", flaky)

    summary = run_daily(TODAY, adapters, config)

    assert summary.analysed == ["ACME-2026-10-13", "BETA-2026-10-13"]
    first, other = adapters.notifier.messages
    assert "Acme Ltd" in first and "summary could not be formatted; see report" in first
    assert other.startswith("📄 Beta Ltd IPO (Mainboard)")


# --- S3: prose grounding --------------------------------------------------------------------------

BUSINESS_LINE = "We make electric scooters for city commuters across India."


@pytest.mark.sections("business_industry")
def test_prose_with_a_number_not_in_its_quote_is_replaced_by_the_quote(tmp_path, adapters, config):
    rhp = make_tiny_rhp(tmp_path / "rhp.pdf", business_pages=(f"OUR BUSINESS\n{BUSINESS_LINE}",))
    source = Quote(page=4, verbatim_quote=BUSINESS_LINE)
    answer = found_nothing(BusinessIndustry).model_copy(update={
        "what_it_sells": "Sells 40% of India's electric scooters",
        "moats": [CitedText(text="Revenue fell 40% last year", source=source),
                  CitedText(text="Electric scooter maker", source=source)],
    })
    adapters.llm = ScriptedLLM({BusinessIndustry: answer}, models=config.models)

    report = analyse_rhp(make_ipo(), rhp, adapters, config)

    b = report.business
    assert [m.text for m in b.moats] == [BUSINESS_LINE, "Electric scooter maker"]
    assert b.what_it_sells == ""  # no sibling quote, and 40 is no verified fact of this section


@pytest.mark.sections("business_industry")
def test_number_free_prose_stays(tmp_path, adapters, config):
    rhp = make_tiny_rhp(tmp_path / "rhp.pdf", business_pages=(f"OUR BUSINESS\n{BUSINESS_LINE}",))
    answer = found_nothing(BusinessIndustry).model_copy(update={"what_it_sells": "Electric scooters for commuters"})
    adapters.llm = ScriptedLLM({BusinessIndustry: answer}, models=config.models)

    report = analyse_rhp(make_ipo(), rhp, adapters, config)

    assert report.business.what_it_sells == "Electric scooters for commuters"


def test_every_prose_field_beside_a_quote_is_checked_against_that_quote():
    q = Quote(page=3, verbatim_quote="Revenue from our single plant at Pune was 72.5% of revenue")
    unverified = Quote(page=None, verbatim_quote=None)
    risks = RiskFactors(risks=[
        RiskFactor(topic="40% from one plant", title=q, kind="company_specific", quantified=False),
        RiskFactor(topic="72.5% from one plant", title=q, kind="company_specific", quantified=False),
        RiskFactor(topic="40% from one plant", title=unverified, kind="company_specific", quantified=False),
    ])
    grounded, notes = ground_section(risks, "risk_factors")
    assert [r.topic for r in grounded.risks] == [q.verbatim_quote, "72.5% from one plant", ""]
    assert len(notes) == 2

    other = OtherDisclosures(items=[MaterialItem(summary="A ₹30 crore penalty", why_material="It is 9% of profit",
                                                 source=q, figures=[crore(30.0, "penalty of Rs. 30 crore", 3)])])
    [item] = ground_section(other, "other_disclosures")[0].items
    assert item.summary == "A ₹30 crore penalty"  # 30 is a verified figure beside it
    assert item.why_material == q.verbatim_quote

    claim = IndustryClaim(claim="The market grows 11% a year to 2030", source=Quote(page=5, verbatim_quote="CAGR of 11%"),
                          figure=None)
    remark = AuditorRemark(kind="emphasis_of_matter", summary="Going concern doubt", source=q)
    promoter = Promoter(name="A. Kumar", background="25 years in auto parts", source=unverified)
    assert ground_section(claim, "business_industry")[0].claim == "CAGR of 11%"
    assert ground_section(remark, "financials")[0].summary == "Going concern doubt"
    assert ground_section(promoter, "promoters_governance")[0].background == ""


def test_bull_and_bear_points_keep_only_numbers_from_the_tagged_reasons(tiny_rhp, adapters, config):
    one_article(adapters)
    adapters.llm = sentiment_llm(config, {"a": "subscribe"}, reasons={"a": ["GMP at ₹40 points to listing gains"]})
    adapters.llm.responses[SentimentPoints] = SentimentPoints(
        bull_points=["GMP of ₹40 signals demand", "Listing gain of 25% likely"], bear_points=["Rich valuation"])

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.sentiment.bull_points == ["GMP of ₹40 signals demand"]
    assert report.sentiment.bear_points == ["Rich valuation"]


@pytest.mark.sections(*VALUATION_SECTIONS)
def test_valuation_reasons_with_numbers_are_dropped(tmp_path, adapters, config):
    plan_ = ValuationPlan(multiples_to_use=[MultipleChoice(multiple="pe", reason="Peers trade at 25x earnings")],
                          additional_peers=[SuggestedPeer(name="Delta Motors Limited", reason="Also 2-wheelers"),
                                            SuggestedPeer(name="Omega Motors Limited", reason="Also makes scooters")])

    report = analyse_valuation(make_valuation_rhp(tmp_path), adapters, config, valuation_plan=plan_)

    [pe] = report.valuation.multiples
    assert pe.reason == ""
    assert [r.reason for r in report.valuation.suggested_peers] == ["", "Also makes scooters"]


@pytest.mark.synthesizer
def test_section_summaries_are_checked_against_their_own_section_without_page_numbers(tiny_rhp, adapters, config):
    answer = synthesis().model_copy(update={"section_summaries": [
        SectionSummary(section="offer_objects", text="A ₹1,250 crore issue."),
        SectionSummary(section="offer_objects", text="A ₹1,250 crore issue priced up to ₹105."),  # 105: E:ipo
        SectionSummary(section="offer_objects", text="A ₹1,250 crore issue, see page 2."),  # 2: a page number
        SectionSummary(section="financials", text="Raising ₹1,250 crore."),  # another section's number
    ]})
    adapters.llm = ScriptedLLM({OfferObjects: OFFER_ANSWER, Synthesis: answer}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [s.text for s in report.synthesis.section_summaries] == ["A ₹1,250 crore issue."]
    assert sum("summary" in n for n in report.synthesis.notes) == 3


# --- S4: bullets ----------------------------------------------------------------------------------

def test_a_reason_is_shown_in_full_in_the_summary():
    # Reasons and risks were cut at 120 characters ("…"); they are shown whole, up to a safety ceiling.
    text = "The full-year cash conversion cycle lengthened from 83.0 days to 102.0 days, while full-year " \
           "operating cash flow and PAT diverged, so profits are not turning into cash at the same pace."
    assert runner._bullet(text) == "• " + text


def test_a_runaway_bullet_is_cut_at_a_word_boundary_never_inside_a_figure():
    text = "x" * (runner.BULLET_CHARS - 10) + " rev ₹1,234,567 crore in Fiscal 2024"  # a raw cut would split the figure
    bullet = runner._bullet(text)

    assert bullet == "• " + "x" * (runner.BULLET_CHARS - 10) + " rev…"
    assert len(bullet) <= runner.BULLET_CHARS + 2


# --- S5: paths ------------------------------------------------------------------------------------

def test_relative_paths_resolve_against_the_repo_root_wherever_the_run_starts(tmp_path, monkeypatch):
    raw = (REPO_ROOT / "config.yaml").read_text()
    assert "db_path: data/ipo.db" in raw and "work_dir: data/work" in raw
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    cfg = load_config(REPO_ROOT / "config.yaml")

    assert cfg.db_path == str(REPO_ROOT / "data" / "ipo.db")
    assert cfg.work_dir == str(REPO_ROOT / "data" / "work")
    path = tmp_path / "config.yaml"
    path.write_text(raw.replace("db_path: data/ipo.db", f"db_path: {tmp_path / 'x.db'}"))
    assert load_config(path).db_path == str(tmp_path / "x.db")  # an absolute path is kept


def test_data_dir_is_gitignored():
    assert "data/" in (REPO_ROOT / ".gitignore").read_text().splitlines()


# --- S6: strict-mode schemas ----------------------------------------------------------------------

def _llm_schemas():
    from ipo_agents.sections import SECTIONS

    return [*(s.schema for s in SECTIONS.values()), StanceTag, SentimentPoints, ValuationPlan, Synthesis, Advice]


@pytest.mark.parametrize("schema", _llm_schemas(), ids=lambda s: s.__name__)
def test_llm_facing_schemas_pass_openai_strict_mode(schema):
    from openai.lib._pydantic import to_strict_json_schema

    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if (k == "default" and v is not None) or k in ("minimum", "maximum", "exclusiveMinimum",
                                                                 "exclusiveMaximum"):
                    found.append(f"{path}/{k}={v!r}")
                walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(to_strict_json_schema(schema), "")
    assert found == []


def test_a_rank_below_one_is_normalised_to_no_rank():
    r = RiskFactor(topic="x", title=Quote(), kind="company_specific", quantified=False, severity_rank=0)
    assert r.severity_rank is None


# --- Live smoke finding: the model sends `value: null` with no reason, or a value with no citation ---

@pytest.mark.sections("offer_objects")
def test_a_null_value_without_a_reason_is_read_as_not_disclosed_not_a_failed_reply(tiny_rhp, adapters, config):
    # Live gpt-5.6-luna returned {"value": null, "not_disclosed_reason": null} for absent fields; one such
    # field must not make the whole section unreadable.
    bare = Fact(value=None, unit="₹ crore", page=None, verbatim_quote=None)
    adapters.llm = ScriptedLLM({OfferObjects: offer_objects(issue_size=bare)}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.offer.issue_size.value is None
    assert report.offer.issue_size.not_disclosed_reason == "not disclosed in provided pages"
    assert "offer_objects.issue_size" not in report.unverified_fields


@pytest.mark.sections("offer_objects")
def test_a_value_without_a_citation_could_not_be_verified_instead_of_failing_the_reply(tiny_rhp, adapters, config):
    uncited = Fact(value=1250.0, unit="₹ crore", page=None, verbatim_quote=None)
    adapters.llm = ScriptedLLM({OfferObjects: offer_objects(issue_size=uncited)}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.offer.issue_size.value is None
    assert "offer_objects.issue_size" in report.unverified_fields


@pytest.mark.sections("offer_objects", "business_industry")
def test_a_section_whose_own_reply_is_cut_off_is_skipped_not_the_whole_ipo(tiny_rhp, adapters, config):
    # HD Fire live run: Promoters & Governance's final call hit its output limit and the LLMParseError
    # failed the whole IPO. One unreadable section must only mark that section, like an unreadable part.
    from ipo_agents.contracts import BusinessIndustry
    from ipo_agents.llm import LLMParseError
    issue = Fact(value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore")
    adapters.llm = ScriptedLLM({OfferObjects: offer_objects(issue_size=issue),
                                BusinessIndustry: LLMParseError("length limit", input_tokens=10, cached_tokens=0,
                                                                output_tokens=1500)}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.offer.issue_size == issue
    assert report.business is None
    assert "business_industry: could not read (reply unreadable)" in report.unverified_fields


@pytest.mark.sections("offer_objects", "business_industry")
def test_a_budget_stop_in_a_section_still_stops_the_run(tiny_rhp, adapters, config):
    from ipo_agents.contracts import BusinessIndustry
    adapters.llm = ScriptedLLM({OfferObjects: offer_objects(), BusinessIndustry: BudgetExceeded("cap")},
                               models=config.models)

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)
