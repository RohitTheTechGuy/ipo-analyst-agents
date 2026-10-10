from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.ingestion import ingest
from tests.fakes import ScriptedLLM, make_ipo


def test_report_carries_page_cited_offer_facts(tiny_rhp, adapters, config):
    adapters.llm = ScriptedLLM({OfferObjects: OfferObjects(issue_size_cr=Fact(
        value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))})

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.status == "complete"
    assert report.offer.issue_size_cr.value == 1250.0
    assert report.offer.issue_size_cr.page == 2
    assert report.rhp_sha256 == ingest(tiny_rhp).sha256


def test_offer_agent_reads_the_rhp_text(tiny_rhp, adapters, config):
    llm = ScriptedLLM({OfferObjects: OfferObjects(issue_size_cr=Fact(
        value=None, unit="₹ crore", page=None, verbatim_quote=None, not_disclosed_reason="not in pages"))})
    adapters.llm = llm

    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert any("Total issue size" in c["user"] for c in llm.calls)
