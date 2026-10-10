"""Offer & Objects section agent + grounding verifier at seam B (spec stories 17-22; plan 5.5)."""
import pytest
import yaml

import ipo_agents.section_agent as section_agent
from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import Fact, OfferObjects, ProceedsObject, SellingShareholder
from tests.conftest import make_tiny_rhp
from tests.fakes import NOT_DISCLOSED, ScriptedLLM, make_ipo, offer_objects

OFFER_PAGE = "\n".join([
    "THE OFFER",
    "Total issue size of up to Rs. 1,250.00 crore",
    "Offer for Sale of up to",
    "84,941,997 equity shares",
    "Offer expenses adjustment (15,844.00)",
    "Shares held by the Investor Selling",
    "Shareholder after the Offer: Nil",
])
CAPITAL_PAGE = "CAPITAL STRUCTURE\nEquity Shares outstanding prior to the Offer 3,687,072,258"
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

    report = analyse(rhp, adapters, config, offer_objects(issue_size=issue))

    assert report.unverified_fields == ["offer_objects.issue_size"]


def test_a_quote_not_on_the_page_is_rejected_even_when_its_number_is(rhp, adapters, config):
    issue = fact(1250.0, 2, "Anchor investors were allotted shares worth Rs. 1,250.00 crore")

    report = analyse(rhp, adapters, config, offer_objects(issue_size=issue))

    assert report.unverified_fields == ["offer_objects.issue_size"]


def test_a_quote_matches_across_the_pages_line_breaks(rhp, adapters, config):
    ofs = fact(84941997, 2, "Offer for Sale of up to 84,941,997 equity shares", unit="equity shares")

    report = analyse(rhp, adapters, config, offer_objects(ofs_shares=ofs))

    assert report.offer.ofs_shares.value == 84941997
    assert report.unverified_fields == []


def test_a_bracketed_number_verifies_as_negative(rhp, adapters, config):
    quote = "Offer expenses adjustment (15,844.00)"
    objects = [ProceedsObject(purpose="adjustment", amount=fact(-15844.0, 2, quote, unit="₹ million")),
               ProceedsObject(purpose="sign dropped", amount=fact(15844.0, 2, quote, unit="₹ million"))]

    report = analyse(rhp, adapters, config, offer_objects(objects=objects))

    assert report.offer.objects[0].amount.value == -15844.0
    assert report.offer.objects[1].amount.not_disclosed_reason == "could not verify"
    assert report.unverified_fields == ["offer_objects.objects[1].amount"]


def test_nil_verifies_as_zero(rhp, adapters, config):
    sellers = [
        SellingShareholder(name="Investor", shares_offered=NOT_DISCLOSED,
                           holding_after=fact(0, 2, "Shareholder after the Offer: Nil", unit="equity shares")),
        # A zero whose quote has neither a 0 nor "Nil" is not on the page.
        SellingShareholder(name="Other", shares_offered=NOT_DISCLOSED,
                           holding_after=fact(0, 2, "Total issue size of up to Rs. 1,250.00 crore", unit="%")),
    ]

    report = analyse(rhp, adapters, config, offer_objects(selling_shareholders=sellers))

    assert report.offer.selling_shareholders[0].holding_after.value == 0
    assert report.unverified_fields == ["offer_objects.selling_shareholders[1].holding_after"]


def test_a_value_not_disclosed_is_reported_as_not_disclosed(rhp, adapters, config):
    report = analyse(rhp, adapters, config, offer_objects())

    assert report.offer.issue_size.value is None
    assert report.offer.issue_size.not_disclosed_reason == "not disclosed in provided pages"
    assert report.unverified_fields == []


def test_a_section_too_big_for_one_call_is_read_part_by_part_then_merged(rhp, adapters, config, monkeypatch):
    monkeypatch.setattr(section_agent, "MAX_CALL_CHARS", 60)  # each page is its own part
    issue = fact(1250.0, 2, "Total issue size of up to Rs. 1,250.00 crore")
    shares = fact(3687072258, 3, "outstanding prior to the Offer 3,687,072,258", unit="equity shares")

    def answer(user):
        if user.startswith("[page 2]"):
            return offer_objects(issue_size=issue)
        if user.startswith("[page 3]"):
            return offer_objects(pre_issue_shares=shares)
        return offer_objects(issue_size=issue, pre_issue_shares=shares)  # the merge

    adapters.llm = llm = ScriptedLLM({OfferObjects: answer}, models=config.models)
    report = analyse_rhp(make_ipo(), rhp, adapters, config)

    assert [c["task"] for c in llm.calls] == ["map_extract", "map_extract", "offer_objects"]
    assert "We have one plant" not in "".join(c["user"] for c in llm.calls)
    merge_input = llm.calls[2]["user"]
    assert "Total issue size of up to Rs. 1,250.00 crore" in merge_input
    assert "outstanding prior to the Offer 3,687,072,258" in merge_input
    assert report.offer.issue_size == issue
    assert report.offer.pre_issue_shares == shares
    assert report.unverified_fields == []


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


@pytest.mark.parametrize("slug", ["bajaj-housing-finance", "hyundai-motor-india", "ola-electric"])
def test_golden_offer_facts_verify_on_the_real_rhp_text(slug, golden_rhp, adapters, config):
    """Verifier agreement on real RHP text, not truth: the golden facts are still needs_human_review."""
    pdf = golden_rhp(slug)
    expected = yaml.safe_load((pdf.parent / "expected.yaml").read_text(encoding="utf-8"))
    facts = {GOLDEN_FIELDS[f["key"]]: Fact(value=f["value"], unit=f["unit"], page=f["page"],
                                           verbatim_quote=f["verbatim_quote"])
             for f in expected["facts"] if f["key"] in GOLDEN_FIELDS}

    report = analyse(pdf, adapters, config, offer_objects(**facts))

    assert report.unverified_fields == []
    assert {name: getattr(report.offer, name).value for name in facts} == {
        name: f.value for name, f in facts.items()}

    altered = next(iter(facts))
    facts[altered] = facts[altered].model_copy(update={"value": facts[altered].value + 1})
    report = analyse(pdf, adapters, config, offer_objects(**facts))

    assert report.unverified_fields == [f"offer_objects.{altered}"]
