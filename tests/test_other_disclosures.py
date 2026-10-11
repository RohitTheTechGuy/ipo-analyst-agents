"""Other Disclosures section agent at seam B (plan 5.2; ticket 16): "nothing material" is a valid result."""
import pytest

from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import Fact, MaterialItem, OtherDisclosures, Quote
from tests.conftest import make_tiny_rhp
from tests.fakes import ScriptedLLM, make_ipo

pytestmark = pytest.mark.sections("other_disclosures")

# The tiny RHP's other-disclosure pages: 1 (contents), 7 (other regulatory disclosures) and 8 (this page).
GENERAL_PAGE = "\n".join([
    "GENERAL INFORMATION",
    "SEBI imposed a penalty of Rs. 5.00 lakh on our Company in 2023.",
])
PENALTY = MaterialItem(
    summary="SEBI imposed a penalty on the company in 2023",
    why_material="A regulator penalised the company shortly before the IPO",
    source=Quote(page=8, verbatim_quote="SEBI imposed a penalty of Rs. 5.00 lakh on our Company in 2023"),
    figures=[Fact(value=5.00, unit="₹ lakh", page=8, verbatim_quote="imposed a penalty of Rs. 5.00 lakh")])


@pytest.fixture
def rhp(tmp_path):
    return make_tiny_rhp(tmp_path / "other_rhp.pdf", extra_pages=(GENERAL_PAGE,))


def analyse(rhp, adapters, config, answer):
    adapters.llm = ScriptedLLM({OtherDisclosures: answer}, models=config.models)
    return analyse_rhp(make_ipo(), rhp, adapters, config)


def test_nothing_material_is_a_valid_result(rhp, adapters, config):
    report = analyse(rhp, adapters, config, OtherDisclosures(items=[]))

    assert report.other.nothing_material is True
    assert report.other.items == []
    assert report.unverified_fields == []


def test_a_planted_material_item_is_surfaced_with_its_page(rhp, adapters, config):
    report = analyse(rhp, adapters, config, OtherDisclosures(items=[PENALTY]))

    assert report.other.nothing_material is False
    [item] = report.other.items
    assert item.source.page == 8
    assert item.figures[0].value == 5.00
    assert report.unverified_fields == []


def test_a_number_in_an_item_must_verify(rhp, adapters, config):
    wrong = PENALTY.model_copy(update={"figures": [PENALTY.figures[0].model_copy(update={"value": 50.0})]})

    report = analyse(rhp, adapters, config, OtherDisclosures(items=[wrong]))

    assert report.other.items[0].figures[0].not_disclosed_reason == "could not verify"
    assert report.unverified_fields == ["other_disclosures.items[0].figures[0]"]


def test_the_agent_reads_only_pages_no_other_section_owns(rhp, adapters, config):
    analyse(rhp, adapters, config, OtherDisclosures(items=[]))

    [call] = [c for c in adapters.llm.calls if c["task"] == "other_disclosures"]
    assert all(f"[page {n}]" in call["user"] for n in (1, 7, 8))
    assert "Total issue size" not in call["user"] and "We make scooters" not in call["user"]


def test_the_agent_is_routed_to_groq(rhp, adapters, config):
    # Its call is capped at 1,500 output tokens, so prompt + schema + pages fit Groq's 6,000-token limit.
    analyse(rhp, adapters, config, OtherDisclosures(items=[]))

    [call] = [c for c in adapters.llm.calls if c["task"] == "other_disclosures"]
    assert (call["model"], call["max_tokens"]) == ("groq", 1500)


def test_a_call_too_big_for_groq_falls_back_to_luna(rhp, adapters, config, monkeypatch):
    import ipo_agents.analysis as analysis

    spec = analysis.SECTIONS["other_disclosures"]
    monkeypatch.setattr(analysis, "SECTIONS", {"other_disclosures": type(spec)(
        group=spec.group, schema=spec.schema, prompt=spec.prompt, report_field=spec.report_field, max_tokens=5000)})

    analyse(rhp, adapters, config, OtherDisclosures(items=[]))

    [call] = [c for c in adapters.llm.calls if c["task"] == "other_disclosures"]
    assert call["model"] == "luna"
