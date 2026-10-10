"""Offer & Objects section agent + grounding verifier at seam B (spec stories 17-22; plan 5.5)."""
import typing

import pytest
import yaml
from pydantic import BaseModel

import ipo_agents.analysis as analysis
import ipo_agents.section_agent as section_agent
from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded
from ipo_agents.contracts import Fact, OfferObjects, ProceedsObject, SellingShareholder
from ipo_agents.llm import LLMParseError
from ipo_agents.section_agent import SectionSpec
from ipo_agents.sections import SECTIONS
from tests.conftest import REPO_ROOT, make_tiny_rhp
from tests.fakes import NOT_DISCLOSED, ScriptedLLM, make_ipo, offer_objects

OFFER_PAGE = "\n".join([
    "THE OFFER",
    "Total issue size of up to Rs. 1,250.00 crore",
    "Offer for Sale of up to",
    "84,941,997 equity shares",
    "Offer expenses adjustment in Rs. million (15,844.00)",
    "Shares held by the Investor Selling",
    "Shareholder after the Offer: Nil",
    "Sr. No. Particulars",
    "1 Fresh Issue Rs. 5,000.00 million",
    "2 Offer for Sale",
    "FY 2026 revenue",
    "Shares pledged by the Promoter -",
    "Change in promoter holding -5.2 %",
    "1,250.00 1,250.00 1,250.00",
])
CAPITAL_PAGE = "\n".join([
    "CAPITAL STRUCTURE",
    "Equity Shares outstanding prior to the Offer 3,687,072,258",
    "Investor Selling Shareholder offers 4,001 shares",
    "Investor Selling Shareholders offer 4,000 shares",
])
OBJECTS_PAGE = "OBJECTS OF THE OFFER\nRepayment of borrowings Rs. 800.00 crore"
COULD_NOT_VERIFY = Fact(value=None, unit="₹ crore", page=None, verbatim_quote=None,
                        not_disclosed_reason="could not verify")


@pytest.fixture
def rhp(tmp_path):
    """Offer section on pages 2 (the offer) and 3 (capital structure); risk factors on page 4."""
    return make_tiny_rhp(tmp_path / "offer_rhp.pdf", (OFFER_PAGE, CAPITAL_PAGE))


def fact(value, page, quote, unit="₹ crore"):
    return Fact(value=value, unit=unit, page=page, verbatim_quote=quote)


def analyse(rhp, adapters, config, offer):
    adapters.llm = ScriptedLLM({OfferObjects: offer}, models=config.models)
    return analyse_rhp(make_ipo(), rhp, adapters, config)


def verdict(rhp, adapters, config, **fields):
    """The unverified field paths when the agent returns these offer fields."""
    return analyse(rhp, adapters, config, offer_objects(**fields)).unverified_fields


def test_a_fact_found_on_its_page_is_kept(rhp, adapters, config):
    issue = fact(1250.0, 2, "Total issue size of up to Rs. 1,250.00 crore")

    report = analyse(rhp, adapters, config, offer_objects(issue_size=issue))

    assert report.offer.issue_size == issue
    assert report.unverified_fields == []


def test_a_number_absent_from_its_cited_page_could_not_be_verified(rhp, adapters, config):
    # The quote and number are real, but on page 2; page 3 has no 1,250.
    issue = fact(1250.0, 3, "Total issue size of up to Rs. 1,250.00 crore")

    report = analyse(rhp, adapters, config, offer_objects(issue_size=issue))

    assert report.offer.issue_size == COULD_NOT_VERIFY
    assert report.unverified_fields == ["offer_objects.issue_size"]


def test_a_genuine_quote_with_a_different_value_is_rejected(rhp, adapters, config):
    issue = fact(1205.0, 2, "Total issue size of up to Rs. 1,250.00 crore")

    report = analyse(rhp, adapters, config, offer_objects(issue_size=issue))

    assert report.offer.issue_size == COULD_NOT_VERIFY
    assert report.unverified_fields == ["offer_objects.issue_size"]


def test_a_number_changed_inside_an_otherwise_genuine_quote_is_rejected(rhp, adapters, config):
    # One digit off still matches the page's line fuzzily (score ~98), but 1,350 is not on the page.
    issue = fact(1350.0, 2, "Total issue size of up to Rs. 1,350.00 crore")

    assert verdict(rhp, adapters, config, issue_size=issue) == ["offer_objects.issue_size"]


def test_a_quote_not_on_the_page_is_rejected_even_when_its_number_is(rhp, adapters, config):
    issue = fact(1250.0, 2, "Anchor investors were allotted shares worth Rs. 1,250.00 crore")

    assert verdict(rhp, adapters, config, issue_size=issue) == ["offer_objects.issue_size"]


def test_a_value_on_the_page_but_not_in_the_quote_is_rejected(rhp, adapters, config):
    # 1,250 is on page 2, but this quote (also on page 2) doesn't contain it.
    issue = fact(1250.0, 2, "Offer for Sale of up to 84,941,997 equity shares")

    assert verdict(rhp, adapters, config, issue_size=issue) == ["offer_objects.issue_size"]


@pytest.mark.parametrize("quote", ["1,250.00 crore", "1,250.00 1,250.00"])
def test_a_quote_too_short_or_without_a_word_is_rejected(rhp, adapters, config, quote):
    # "1,250.00 crore" is 14 characters; the other (a row of page 2) has no word at all.
    issue = fact(1250.0, 2, quote)

    assert verdict(rhp, adapters, config, issue_size=issue) == ["offer_objects.issue_size"]


def test_a_quote_matches_across_the_pages_line_breaks(rhp, adapters, config):
    ofs = fact(84941997, 2, "Offer for Sale of up to 84,941,997 equity shares", unit="equity shares")

    report = analyse(rhp, adapters, config, offer_objects(ofs_shares=ofs))

    assert report.offer.ofs_shares.value == 84941997
    assert report.unverified_fields == []


def test_a_leading_serial_number_is_not_the_value(rhp, adapters, config):
    fresh_serial = fact(1, 2, "1 Fresh Issue Rs. 5,000.00 million", unit="₹ million")
    ofs_serial = fact(2, 2, "2 Offer for Sale", unit="equity shares")
    gcp_amount = fact(5000.0, 2, "1 Fresh Issue Rs. 5,000.00 million", unit="₹ million")

    assert verdict(rhp, adapters, config, fresh_issue=fresh_serial, ofs_shares=ofs_serial,
                   gcp_amount=gcp_amount) == ["offer_objects.fresh_issue", "offer_objects.ofs_shares"]


def test_a_year_is_not_a_value_unless_the_unit_is_a_year(rhp, adapters, config):
    year = fact(2026, 2, "FY 2026 revenue", unit="₹ million")

    assert verdict(rhp, adapters, config, issue_size=year) == ["offer_objects.issue_size"]


def test_a_value_in_another_unit_than_its_quote_is_rejected(rhp, adapters, config):
    # The page says ₹ 5,000 million; calling it ₹ 5,000 crore is 10x off.
    crore = fact(5000.0, 2, "1 Fresh Issue Rs. 5,000.00 million", unit="₹ crore")

    assert verdict(rhp, adapters, config, fresh_issue=crore) == ["offer_objects.fresh_issue"]


def test_a_bracketed_number_verifies_as_negative(rhp, adapters, config):
    quote = "Offer expenses adjustment in Rs. million (15,844.00)"
    objects = [ProceedsObject(purpose="adjustment", amount=fact(-15844.0, 2, quote, unit="₹ million")),
               ProceedsObject(purpose="sign dropped", amount=fact(15844.0, 2, quote, unit="₹ million"))]

    report = analyse(rhp, adapters, config, offer_objects(objects=objects))

    assert report.offer.objects[0].amount.value == -15844.0
    assert report.offer.objects[1].amount.not_disclosed_reason == "could not verify"
    assert report.unverified_fields == ["offer_objects.objects[1].amount"]


def test_a_leading_minus_verifies_as_negative(rhp, adapters, config):
    change = fact(-5.2, 2, "Change in promoter holding -5.2 %", unit="%")

    report = analyse(rhp, adapters, config, offer_objects(promoter_holding_post_pct=change))

    assert report.offer.promoter_holding_post_pct.value == -5.2
    assert report.unverified_fields == []


def seller(name, holding_after):
    return SellingShareholder(name=name, shares_offered=NOT_DISCLOSED, holding_after=holding_after)


def test_nil_or_a_dash_cell_verifies_as_zero(rhp, adapters, config):
    sellers = [
        seller("Investor", fact(0, 2, "Shareholder after the Offer: Nil", unit="equity shares")),
        seller("Pledge", fact(0, 2, "Shares pledged by the Promoter -", unit="equity shares")),
        # "No." is not "no": a zero needs a 0, "Nil" or a dash cell.
        seller("Serial", fact(0, 2, "Sr. No. Particulars", unit="equity shares")),
        seller("Other", fact(0, 2, "Total issue size of up to Rs. 1,250.00 crore", unit="%")),
    ]

    report = analyse(rhp, adapters, config, offer_objects(selling_shareholders=sellers))

    assert [s.holding_after.value for s in report.offer.selling_shareholders] == [0, 0, None, None]
    assert report.unverified_fields == ["offer_objects.selling_shareholders[2].holding_after",
                                        "offer_objects.selling_shareholders[3].holding_after"]


def test_every_occurrence_of_a_fuzzy_quote_is_checked(rhp, adapters, config):
    # The closest line (4,001) has the wrong number; the next-closest line has 4,000.
    offered = fact(4000, 3, "Investor Selling Shareholder offers 4,000 shares", unit="equity shares")

    report = analyse(rhp, adapters, config, offer_objects(
        selling_shareholders=[seller("Investor", NOT_DISCLOSED).model_copy(update={"shares_offered": offered})]))

    assert report.offer.selling_shareholders[0].shares_offered.value == 4000
    assert report.unverified_fields == []


def test_unverified_fields_are_sorted(rhp, adapters, config):
    wrong = fact(1205.0, 2, "Total issue size of up to Rs. 1,250.00 crore")

    assert verdict(rhp, adapters, config, issue_size=wrong, fresh_issue=wrong) == [
        "offer_objects.fresh_issue", "offer_objects.issue_size"]


def test_a_value_not_disclosed_is_reported_as_not_disclosed(rhp, adapters, config):
    report = analyse(rhp, adapters, config, offer_objects())

    assert report.offer.issue_size.value is None
    assert report.offer.issue_size.not_disclosed_reason == "not disclosed in provided pages"
    assert report.unverified_fields == []


def test_every_number_in_every_section_schema_is_a_fact():
    def raw_numbers(model, path):
        for name, field in model.model_fields.items():
            yield from walk(field.annotation, f"{path}.{name}")

    def walk(ann, path):
        if ann in (int, float):
            yield path
        elif isinstance(ann, type) and issubclass(ann, BaseModel) and ann is not Fact:
            yield from raw_numbers(ann, path)
        else:
            for arg in typing.get_args(ann):
                yield from walk(arg, path)

    assert [p for spec in SECTIONS.values() for p in raw_numbers(spec.schema, spec.group)] == []


def test_a_fact_inside_a_dict_is_refused_not_skipped(rhp, adapters, config, monkeypatch):
    class Keyed(BaseModel):
        by_name: dict[str, Fact]

    spec = SectionSpec(group="offer_objects", schema=Keyed, prompt="p", report_field="offer")
    monkeypatch.setattr(analysis, "SECTIONS", {"offer_objects": spec})
    adapters.llm = ScriptedLLM({Keyed: Keyed(by_name={"x": fact(9.0, 2, "made up number 9 here")})},
                               models=config.models)

    with pytest.raises(TypeError, match="dict"):
        analyse_rhp(make_ipo(), rhp, adapters, config)


# --- map -> reduce ------------------------------------------------------------

ISSUE = fact(1250.0, 2, "Total issue size of up to Rs. 1,250.00 crore")
SHARES = fact(3687072258, 3, "outstanding prior to the Offer 3,687,072,258", unit="equity shares")


def test_a_section_too_big_for_one_call_is_read_part_by_part_then_merged(rhp, adapters, config, monkeypatch):
    monkeypatch.setattr(section_agent, "MAX_CALL_CHARS", 60)  # each page is its own part

    def answer(user):
        if user.startswith("[page 2]"):
            return offer_objects(issue_size=ISSUE)
        if user.startswith("[page 3]"):
            return offer_objects(pre_issue_shares=SHARES)
        return offer_objects(issue_size=ISSUE, pre_issue_shares=SHARES)  # the merge

    adapters.llm = llm = ScriptedLLM({OfferObjects: answer}, models=config.models)
    report = analyse_rhp(make_ipo(), rhp, adapters, config)

    assert sorted(c["task"] for c in llm.calls) == ["map_extract", "map_extract", "offer_objects"]
    assert "We have one plant" not in "".join(c["user"] for c in llm.calls)
    [merge] = [c for c in llm.calls if c["task"] == "offer_objects"]
    assert "Total issue size of up to Rs. 1,250.00 crore" in merge["user"]
    assert "outstanding prior to the Offer 3,687,072,258" in merge["user"]
    assert report.offer.issue_size == ISSUE
    assert report.offer.pre_issue_shares == SHARES
    assert report.unverified_fields == []


def test_merged_parts_too_big_for_one_call_are_merged_in_rounds(tmp_path, adapters, config, monkeypatch):
    monkeypatch.setattr(section_agent, "MAX_CALL_CHARS", 60)  # every part, and every partial, is "too big"
    rhp = make_tiny_rhp(tmp_path / "offer_rhp.pdf", (OFFER_PAGE, CAPITAL_PAGE, OBJECTS_PAGE))
    adapters.llm = llm = ScriptedLLM({OfferObjects: lambda user: offer_objects(issue_size=ISSUE)},
                                     models=config.models)

    report = analyse_rhp(make_ipo(), rhp, adapters, config)

    # 3 map calls; pages 2-3 merged first (a map-model call), then that with page 4 (the group's task).
    maps = [c for c in llm.calls if c["task"] == "map_extract"]
    assert sorted(c["user"].split("\n", 1)[0] for c in maps) == ["[page 2]", "[page 3]", "[page 4]", "[pages 2-2]"]
    assert llm.calls[-1]["task"] == "offer_objects"
    assert llm.calls[-1]["user"].startswith("[pages 2-3]")
    assert report.offer.issue_size == ISSUE


def test_a_part_whose_reply_cannot_be_parsed_is_skipped_and_listed(rhp, adapters, config, monkeypatch):
    monkeypatch.setattr(section_agent, "MAX_CALL_CHARS", 60)

    def answer(user):
        if user.startswith("[page 3]"):
            return LLMParseError("bad json", input_tokens=10, cached_tokens=0, output_tokens=5)
        return offer_objects(issue_size=ISSUE)

    adapters.llm = ScriptedLLM({OfferObjects: answer}, models=config.models)
    report = analyse_rhp(make_ipo(), rhp, adapters, config)

    assert report.offer.issue_size == ISSUE
    assert report.unverified_fields == ["offer_objects.pages[3-3]: could not read"]


def test_the_budget_stop_still_stops_a_map_step(rhp, adapters, config, monkeypatch):
    monkeypatch.setattr(section_agent, "MAX_CALL_CHARS", 60)

    def answer(user):
        if user.startswith("[page 3]"):
            return BudgetExceeded("cap reached")
        return offer_objects(issue_size=ISSUE)

    adapters.llm = ScriptedLLM({OfferObjects: answer}, models=config.models)

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), rhp, adapters, config)


# --- golden RHPs --------------------------------------------------------------

# Golden expected.yaml keys -> OfferObjects fields.
GOLDEN_FIELDS = {
    "issue_size": "issue_size",
    "fresh_issue_size": "fresh_issue",
    "ofs_size": "ofs_amount",
    "ofs_shares": "ofs_shares",
    "pre_offer_shares": "pre_issue_shares",
    "post_offer_shares": "post_issue_shares",
    "promoter_pre_offer_holding_pct": "promoter_holding_pre_pct",
}
GOLDEN_SLUGS = ["bajaj-housing-finance", "hyundai-motor-india", "ola-electric"]
# Every golden offer fact, read from the committed expected.yaml files (the PDFs are not committed).
GOLDEN_CASES = [
    (slug, GOLDEN_FIELDS[f["key"]])
    for slug in GOLDEN_SLUGS
    for f in yaml.safe_load((REPO_ROOT / "tests" / "golden" / slug / "expected.yaml").read_text(encoding="utf-8"))["facts"]
    if f["key"] in GOLDEN_FIELDS
]


def golden_facts(golden_rhp, slug) -> dict[str, Fact]:
    """The golden offer facts as scripted answers (needs_human_review: this checks verifier agreement)."""
    expected = yaml.safe_load((golden_rhp(slug).parent / "expected.yaml").read_text(encoding="utf-8"))
    return {GOLDEN_FIELDS[f["key"]]: Fact(value=f["value"], unit=f["unit"], page=f["page"],
                                          verbatim_quote=f["verbatim_quote"])
            for f in expected["facts"] if f["key"] in GOLDEN_FIELDS}


def analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, facts):
    monkeypatch.setattr(analysis, "ingest", lambda path: golden_doc(slug))  # ingested once per session
    return analyse(golden_rhp(slug), adapters, config, offer_objects(**facts))


@pytest.mark.parametrize("slug", GOLDEN_SLUGS)
def test_golden_offer_facts_verify_on_the_real_rhp_text(slug, golden_rhp, golden_doc, adapters, config, monkeypatch):
    facts = golden_facts(golden_rhp, slug)

    report = analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, facts)

    assert report.unverified_fields == []
    assert {name: getattr(report.offer, name).value for name in facts} == {
        name: f.value for name, f in facts.items()}


@pytest.mark.parametrize("slug, field", GOLDEN_CASES)
def test_a_golden_fact_altered_by_one_is_the_only_one_rejected(slug, field, golden_rhp, golden_doc, adapters,
                                                              config, monkeypatch):
    facts = golden_facts(golden_rhp, slug)
    facts[field] = facts[field].model_copy(update={"value": facts[field].value + 1})

    report = analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, facts)

    assert report.unverified_fields == [f"offer_objects.{field}"]
