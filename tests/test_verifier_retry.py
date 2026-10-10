"""Verifier retry loop at seam B (spec story 19): a section whose facts fail verification is re-asked for
those fields, at most twice, before they are left "could not verify"."""
import pytest

import ipo_agents.section_agent as section_agent
from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded
from ipo_agents.config import RunEstimate
from ipo_agents.contracts import Fact, OfferObjects, ProceedsObject
from tests.conftest import make_tiny_rhp
from tests.fakes import ScriptedLLM, make_ipo, offer_objects
from tests.test_offer_objects import CAPITAL_PAGE, COULD_NOT_VERIFY, OBJECTS_PAGE, OFFER_PAGE, fact

ISSUE = fact(1250.0, 2, "Total issue size of up to Rs. 1,250.00 crore")
# A genuine quote from page 2 with a value that is not in it.
WRONG_ISSUE = fact(1205.0, 2, "Total issue size of up to Rs. 1,250.00 crore")
FRESH = fact(5000.0, 2, "1 Fresh Issue Rs. 5,000.00 million", unit="₹ million")
WRONG_FRESH = fact(5000.0, 2, "1 Fresh Issue Rs. 5,000.00 million", unit="₹ crore")  # 10x off


def is_retry(user: str) -> bool:
    return "failed verification" in user


@pytest.fixture
def rhp(tmp_path):
    return make_tiny_rhp(tmp_path / "offer_rhp.pdf", (OFFER_PAGE, CAPITAL_PAGE))


def run(rhp, adapters, config, answer):
    adapters.llm = llm = ScriptedLLM({OfferObjects: answer}, models=config.models)
    return analyse_rhp(make_ipo(), rhp, adapters, config), llm


def test_a_field_fixed_on_the_first_retry_is_verified(rhp, adapters, config):
    report, llm = run(rhp, adapters, config,
                      lambda user: offer_objects(issue_size=ISSUE if is_retry(user) else WRONG_ISSUE))

    assert report.offer.issue_size == ISSUE
    assert report.unverified_fields == []
    assert [c["task"] for c in llm.calls] == ["offer_objects", "offer_objects"]


def test_a_field_never_fixed_gets_two_retries_then_could_not_verify(rhp, adapters, config):
    report, llm = run(rhp, adapters, config, lambda user: offer_objects(issue_size=WRONG_ISSUE))

    assert len(llm.calls) == 1 + 2
    assert report.offer.issue_size == COULD_NOT_VERIFY
    assert report.unverified_fields == ["offer_objects.issue_size"]


def test_the_retry_prompt_lists_the_failed_field_its_value_quote_and_reason(rhp, adapters, config):
    _, llm = run(rhp, adapters, config, lambda user: offer_objects(issue_size=WRONG_ISSUE))

    retry = llm.calls[1]["user"]
    assert "offer_objects.issue_size" in retry
    assert "1205.0" in retry
    assert "page 2" in retry
    assert '"Total issue size of up to Rs. 1,250.00 crore"' in retry
    assert "the value is not a number printed in the quote" in retry  # the verifier's reason
    assert "OFFER" in retry and "Total issue size" in retry  # the same section pages are sent again


def test_a_retry_that_breaks_an_already_verified_field_keeps_the_original(rhp, adapters, config):
    # First answer: issue size right, fresh issue wrong. Retry: fresh issue fixed, issue size broken.
    def answer(user):
        if is_retry(user):
            return offer_objects(issue_size=WRONG_ISSUE, fresh_issue=FRESH)
        return offer_objects(issue_size=ISSUE, fresh_issue=WRONG_FRESH)

    report, llm = run(rhp, adapters, config, answer)

    assert report.offer.issue_size == ISSUE
    assert report.offer.fresh_issue == FRESH
    assert report.unverified_fields == []
    assert len(llm.calls) == 2


def test_a_retry_that_does_not_fit_the_budget_stops_the_run(rhp, adapters, config):
    # Each call may spend 65,000 output tokens x $12/1M = $0.78 (terra). The first reports exactly that,
    # leaving $1.50 - $0.78 = $0.72 under the cap, less than the retry's $0.78 worst case.
    no_estimate = RunEstimate(tokens_per_page=0, output_tokens_per_page=0, fixed_usd=0)
    config = config.model_copy(update={"default_max_tokens": 65_000, "budget": config.budget.model_copy(
        update={"cap_usd": 1.50, "estimate": no_estimate})})
    adapters.llm = llm = ScriptedLLM({OfferObjects: offer_objects(issue_size=WRONG_ISSUE)},
                                     models=config.models, usage=[(0, 0, 65_000)])

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), rhp, adapters, config)

    assert len(llm.calls) == 1


def test_a_multi_part_section_retries_against_the_cited_chunk_and_its_neighbours(tmp_path, adapters, config,
                                                                                  monkeypatch):
    # Pages 2, 3 and 4 are one chunk each, and each its own part. A failed fact cites page 4: the retry
    # sends page 4 and its neighbour, page 3, but not page 2.
    monkeypatch.setattr(section_agent, "MAX_CALL_CHARS", 60)
    rhp = make_tiny_rhp(tmp_path / "offer_rhp.pdf", (OFFER_PAGE, CAPITAL_PAGE, OBJECTS_PAGE))
    quote = "Repayment of borrowings Rs. 800.00 crore"

    def answer(user):
        amount = fact(800.0 if is_retry(user) else 900.0, 4, quote)
        return offer_objects(issue_size=ISSUE, objects=[ProceedsObject(purpose="repayment", amount=amount)])

    report, llm = run(rhp, adapters, config, answer)

    [retry] = [c for c in llm.calls if is_retry(c["user"])]
    assert retry["task"] == "offer_objects"
    assert "[page 3]" in retry["user"] and "[page 4]" in retry["user"]
    assert "[page 2]" not in retry["user"]
    assert report.offer.objects[0].amount == fact(800.0, 4, quote)
    assert report.offer.issue_size == ISSUE
    assert report.unverified_fields == []
