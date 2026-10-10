from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.ingestion import ingest
from tests.fakes import ScriptedLLM, make_ipo, offer_objects


def test_report_carries_page_cited_offer_facts(tiny_rhp, adapters, config):
    adapters.llm = ScriptedLLM({OfferObjects: offer_objects(issue_size=Fact(
        value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.status == "complete"
    assert report.offer.issue_size.value == 1250.0
    assert report.offer.issue_size.page == 2
    assert report.rhp_sha256 == ingest(tiny_rhp).sha256


def test_offer_agent_reads_only_its_own_section(tiny_rhp, adapters, config):
    llm = ScriptedLLM({OfferObjects: offer_objects()}, models=config.models)
    adapters.llm = llm

    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    [call] = [c for c in llm.calls if c["task"] == "offer_objects"]
    assert "Total issue size of up to Rs. 1,250.00 crore" in call["user"]  # page 2, The Offer
    assert "We have one plant" not in call["user"]  # page 3, Risk Factors
    assert "We make scooters" not in call["user"]  # page 4, Our Business


def test_each_page_is_tagged_once_in_the_prompt(tiny_rhp, adapters, config):
    llm = ScriptedLLM({OfferObjects: offer_objects(issue_size=Fact(
        value=None, unit="₹ crore", page=None, verbatim_quote=None, not_disclosed_reason="not in pages"))}, models=config.models)
    adapters.llm = llm

    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    [call] = [c for c in llm.calls if c["task"] == "offer_objects"]
    assert call["user"].count("[page 2]") == 1
