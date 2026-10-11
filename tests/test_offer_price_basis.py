"""Offer Price Basis section agent at seam B (plan 5.2; ticket 15; spec story 29)."""
import pytest
import yaml

import ipo_agents.analysis as analysis
from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import Eps, Fact, Kpi, OfferPriceBasis, PeriodFact, Peer, Waca
from tests.conftest import make_tiny_rhp
from tests.fakes import NOT_DISCLOSED, ScriptedLLM, found_nothing, make_ipo

pytestmark = pytest.mark.sections("offer_price_basis")

# The tiny RHP: pages 1-7 are the stock sections; the Basis for Offer Price page is page 8.
# make_ipo()'s price band is ₹100–₹105, so the upper band is ₹105.


def basis_page(primary_waca="40.00"):
    return "\n".join([
        "BASIS FOR OFFER PRICE",
        "Fiscal 2024 Basic EPS (in Rs.) 5.25 Diluted EPS (in Rs.) 5.20",
        "Fiscal 2024 NAV per Equity Share (in Rs.) 48.10",
        "Fiscal 2024 Return on Net Worth 11.20 %",
        "P/E at the Cap Price (no. of times) [●]",
        "Alpha Motors Limited P/E 32.50 EPS 12.40 RoNW 15.60% NAV 80.20",
        "Alpha Motors Limited revenue (Rs. million) 1,520.75",
        "Units sold in Fiscal 2024: 125,000 units",
        f"WACA of primary issuances (Rs. per share) {primary_waca}",
        "WACA of secondary transactions (Rs. per share) 60.00",
    ])


@pytest.fixture
def rhp(tmp_path):
    return make_tiny_rhp(tmp_path / "basis_rhp.pdf", extra_pages=(basis_page(),))


def fact(value, quote, unit, page=8):
    return Fact(value=value, unit=unit, page=page, verbatim_quote=quote)


def waca(value, quote):
    return Waca(waca=fact(value, quote, "₹ per share"), floor_price_times=NOT_DISCLOSED, cap_price_times=NOT_DISCLOSED)


PRIMARY = waca(40.00, "WACA of primary issuances (Rs. per share) 40.00")
SECONDARY = waca(60.00, "WACA of secondary transactions (Rs. per share) 60.00")
PEER = Peer(name="Alpha Motors Limited",
            pe=fact(32.50, "Alpha Motors Limited P/E 32.50", "times"),
            eps=fact(12.40, "P/E 32.50 EPS 12.40 RoNW", "₹"),
            ronw_pct=fact(15.60, "EPS 12.40 RoNW 15.60% NAV", "%"),
            nav_per_share=fact(80.20, "RoNW 15.60% NAV 80.20", "₹"),
            revenue=fact(1520.75, "Alpha Motors Limited revenue (Rs. million) 1,520.75", "₹ million"))


def basis(**fields) -> OfferPriceBasis:
    return found_nothing(OfferPriceBasis).model_copy(update=fields)


def analyse(rhp, adapters, config, answer, ipo=None):
    adapters.llm = ScriptedLLM({OfferPriceBasis: answer}, models=config.models)
    return analyse_rhp(ipo or make_ipo(), rhp, adapters, config)


def waca_flags(report):
    return [f for f in report.flags if f.code == "OFFER_PRICE_FAR_ABOVE_WACA"]


def test_price_basis_fields_and_the_peer_table_are_extracted_as_facts_that_verify(rhp, adapters, config):
    answer = basis(
        eps=[Eps(period="Fiscal 2024", basic=fact(5.25, "Fiscal 2024 Basic EPS (in Rs.) 5.25", "₹"),
                 diluted=fact(5.20, "Diluted EPS (in Rs.) 5.20", "₹"))],
        nav_per_share=[PeriodFact(period="Fiscal 2024",
                                  value=fact(48.10, "NAV per Equity Share (in Rs.) 48.10", "₹ per share"))],
        ronw_pct=[PeriodFact(period="Fiscal 2024", value=fact(11.20, "Return on Net Worth 11.20 %", "%"))],
        peers=[PEER],
        kpis=[Kpi(name="Units sold", values=[PeriodFact(
            period="Fiscal 2024", value=fact(125000, "Units sold in Fiscal 2024: 125,000 units", "units"))])],
        waca_primary=PRIMARY,
        waca_secondary=SECONDARY,
    )

    report = analyse(rhp, adapters, config, answer)

    assert report.unverified_fields == []
    pb = report.price_basis
    assert (pb.eps[0].basic.value, pb.eps[0].diluted.value) == (5.25, 5.20)
    assert pb.nav_per_share[0].value.value == 48.10
    assert pb.ronw_pct[0].value.value == 11.20
    assert pb.pe_at_cap.value is None  # printed as [●]
    assert pb.peers == [PEER]  # the RHP's own peer table, for peer valuation
    assert pb.kpis[0].values[0].value.value == 125000
    assert (pb.waca_primary.waca.value, pb.waca_secondary.waca.value) == (40.00, 60.00)


def test_the_agent_reads_the_basis_for_offer_price_pages(rhp, adapters, config):
    analyse(rhp, adapters, config, basis())

    [call] = [c for c in adapters.llm.calls if c["task"] == "offer_price_basis"]
    assert call["model"] == "terra"
    assert "WACA of primary issuances" in call["user"]
    assert "We make scooters" not in call["user"]


def test_offer_price_over_twice_the_waca_is_flagged(rhp, adapters, config):
    # Upper band ₹105 / primary WACA ₹40.00 = 2.625x (> 2x); / secondary WACA ₹60.00 = 1.75x.
    report = analyse(rhp, adapters, config, basis(waca_primary=PRIMARY, waca_secondary=SECONDARY))

    primary = report.calculations["cap_price_to_waca_primary"]
    assert primary.value == pytest.approx(2.625)
    assert [(i.field, i.page) for i in primary.inputs] == [
        ("ipo.price_band", None), ("offer_price_basis.waca_primary.waca", 8)]
    assert report.calculations["cap_price_to_waca_secondary"].value == pytest.approx(1.75)
    [flag] = waca_flags(report)
    assert "primary" in flag.message and "secondary" not in flag.message
    assert (flag.section, flag.evidence_pages) == ("offer_price_basis", [8])


def test_offer_price_under_twice_the_waca_is_quiet(tmp_path, adapters, config):
    # ₹105 / ₹55.00 = 1.909x.
    rhp = make_tiny_rhp(tmp_path / "basis_rhp.pdf", extra_pages=(basis_page("55.00"),))

    report = analyse(rhp, adapters, config, basis(
        waca_primary=waca(55.00, "WACA of primary issuances (Rs. per share) 55.00")))

    assert report.unverified_fields == []
    assert report.calculations["cap_price_to_waca_primary"].value == pytest.approx(105 / 55)
    assert waca_flags(report) == []


def test_a_waca_that_fails_verification_raises_no_flag(rhp, adapters, config):
    report = analyse(rhp, adapters, config, basis(
        waca_primary=waca(4.00, "WACA of primary issuances (Rs. per share) 40.00")))

    assert report.unverified_fields == ["offer_price_basis.waca_primary.waca"]
    assert report.calculations["cap_price_to_waca_primary"].not_computable_reason == (
        "offer_price_basis.waca_primary.waca: could not verify")
    assert waca_flags(report) == []


def test_waca_not_disclosed_is_not_computable(rhp, adapters, config):
    report = analyse(rhp, adapters, config, basis())

    primary = report.calculations["cap_price_to_waca_primary"]
    assert primary.value is None
    assert primary.not_computable_reason == "offer_price_basis.waca_primary.waca: not disclosed"


# --- golden RHPs --------------------------------------------------------------

# Golden expected.yaml keys -> OfferPriceBasis fields ("parent.field").
GOLDEN_FIELDS = {
    "waca_all_shares_last_one_year": "waca_primary.waca",
    "industry_pe_average": "industry_pe.average",
}
# expected.yaml cites the WACA on the offer-document summary pages, which this agent never sees. These
# are the same numbers where the Basis for Offer Price section prints them (its WACA of primary
# issuances), so the agent can cite them; value and unit still come from expected.yaml. Hyundai's
# industry P/E is already on a section page, and Hyundai has no WACA (no primary or secondary deals).
PRICE_BASIS_CITATIONS = {
    ("bajaj-housing-finance", "waca_all_shares_last_one_year"): (
        147, "Weighted average cost of acquisition (WACA) of Primary issuances 18.1"),
    ("ola-electric", "waca_all_shares_last_one_year"): (
        158, "Weighted Average Cost of Acquisition (WACA) (Primary Transactions) (₹ per Equity Share) 47.00"),
}
GOLDEN_SLUGS = ["bajaj-housing-finance", "hyundai-motor-india", "ola-electric"]
GOLDEN_CASES = [("bajaj-housing-finance", "waca_primary.waca"), ("ola-electric", "waca_primary.waca"),
                ("hyundai-motor-india", "industry_pe.average")]


def golden_expected(golden_rhp, slug):
    return yaml.safe_load((golden_rhp(slug).parent / "expected.yaml").read_text(encoding="utf-8"))


def golden_facts(golden_rhp, slug) -> dict[str, Fact]:
    """The golden facts, cited on Basis for Offer Price pages (needs_human_review: checks verifier agreement)."""
    facts = {}
    for f in golden_expected(golden_rhp, slug)["facts"]:
        if f["key"] in GOLDEN_FIELDS:
            page, quote = PRICE_BASIS_CITATIONS.get((slug, f["key"]), (f["page"], f["verbatim_quote"]))
            facts[GOLDEN_FIELDS[f["key"]]] = Fact(value=f["value"], unit=f["unit"], page=page, verbatim_quote=quote)
    return facts


def golden_answer(facts: dict[str, Fact]) -> OfferPriceBasis:
    answer = basis()
    for path, f in facts.items():
        parent, name = path.split(".")
        answer = answer.model_copy(update={parent: getattr(answer, parent).model_copy(update={name: f})})
    return answer


def analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, facts):
    monkeypatch.setattr(analysis, "ingest", lambda path: golden_doc(slug))  # ingested once per session
    lo, hi = golden_expected(golden_rhp, slug)["ipo"]["price_band"]
    ipo = make_ipo().model_copy(update={"price_band": f"₹{lo}–₹{hi}"})
    return analyse(golden_rhp(slug), adapters, config, golden_answer(facts), ipo=ipo)


def get(model, path):
    parent, name = path.split(".")
    return getattr(getattr(model, parent), name)


@pytest.mark.parametrize("slug", GOLDEN_SLUGS)
def test_golden_price_basis_facts_verify_on_the_real_section_pages(slug, golden_rhp, golden_doc, adapters, config,
                                                                  monkeypatch):
    facts = golden_facts(golden_rhp, slug)

    report = analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, facts)

    assert report.unverified_fields == []
    assert {path: get(report.price_basis, path).value for path in facts} == {p: f.value for p, f in facts.items()}


@pytest.mark.parametrize("slug, path", GOLDEN_CASES)
def test_a_golden_price_basis_fact_altered_by_one_is_rejected(slug, path, golden_rhp, golden_doc, adapters, config,
                                                             monkeypatch):
    facts = golden_facts(golden_rhp, slug)
    facts[path] = facts[path].model_copy(update={"value": facts[path].value + 1})

    report = analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch, facts)

    assert report.unverified_fields == [f"offer_price_basis.{path}"]


@pytest.mark.parametrize("slug, ratio, fires", [
    ("bajaj-housing-finance", 70 / 18.1, True),  # ₹70 / ₹18.1 = 3.87x: insiders bought far cheaper
    ("ola-electric", 76 / 47.00, False),  # ₹76 / ₹47.00 = 1.62x
    ("hyundai-motor-india", None, False),  # no primary transactions: WACA N.A.
])
def test_golden_offer_price_vs_waca(slug, ratio, fires, golden_rhp, golden_doc, adapters, config, monkeypatch):
    report = analyse_golden(slug, golden_rhp, golden_doc, adapters, config, monkeypatch,
                            golden_facts(golden_rhp, slug))

    primary = report.calculations["cap_price_to_waca_primary"]
    assert primary.value == (pytest.approx(ratio) if ratio else None)
    assert bool(waca_flags(report)) is fires
