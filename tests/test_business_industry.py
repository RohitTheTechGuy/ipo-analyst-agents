"""Business & Industry section agent at seam B (plan 5.2; ticket 13)."""
import pytest

from ipo_agents.analysis import analyse_rhp
from ipo_agents.contracts import (BusinessIndustry, CapacityLine, Cited, Dependence, Fact, IndustryClaim,
                                  RevenueShare)
from tests.conftest import make_tiny_rhp
from tests.fakes import ScriptedLLM, empty_answer, make_ipo

# The tiny RHP: page 1 ToC, 2 the offer, 3 risk factors, then these business pages from page 4.
BUSINESS_PAGE = "\n".join([
    "OUR BUSINESS",
    "We make electric scooters and sell them to retail buyers.",
    "Our largest customer contributed 24.10% of revenue in Fiscal 2024.",
    "Our top 10 customers contributed 58.30% of revenue in Fiscal 2024.",
    "Our largest supplier accounted for 31.50% of total purchases.",
    "Scooters contributed 72.40% of revenue from operations.",
    "All our manufacturing is at a single plant in Krishnagiri.",
    "Installed capacity of 1,000,000 units with utilisation of 64.20%",
    "Our order book stood at Rs. 1,234.50 million as of March 31, 2024.",
    "We compete with TVS Motor and Bajaj Auto.",
    "Our strength is our in-house battery cell technology.",
])
INDUSTRY_PAGE = "\n".join([
    "INDUSTRY OVERVIEW",
    "The EV two-wheeler market is expected to reach Rs. 2,500 billion by 2030.",
])
QUIET_PAGE = "\n".join([
    "OUR BUSINESS",
    "Our largest customer contributed 12.00% of revenue in Fiscal 2024.",
    "Our top 10 customers contributed 45.00% of revenue in Fiscal 2024.",
])


@pytest.fixture
def rhp(tmp_path):
    return make_tiny_rhp(tmp_path / "business_rhp.pdf", business_pages=(BUSINESS_PAGE, INDUSTRY_PAGE))


def fact(value, quote, unit="%", page=4):
    return Fact(value=value, unit=unit, page=page, verbatim_quote=quote)


TOP1 = fact(24.10, "Our largest customer contributed 24.10% of revenue")
TOP10 = fact(58.30, "Our top 10 customers contributed 58.30% of revenue")


def business(**fields) -> BusinessIndustry:
    return empty_answer(BusinessIndustry).model_copy(update=fields)


def analyse(rhp, adapters, config, answer):
    adapters.llm = ScriptedLLM({BusinessIndustry: answer}, models=config.models)
    return analyse_rhp(make_ipo(), rhp, adapters, config)


def codes(report):
    return [f.code for f in report.red_flags if f.section == "business_industry"]


def test_every_business_field_is_extracted_as_a_page_cited_fact_that_verifies(rhp, adapters, config):
    answer = business(
        what_it_sells="Electric scooters, sold to retail buyers",
        revenue_mix=[RevenueShare(name="Scooters", basis="product",
                                  share=fact(72.40, "Scooters contributed 72.40% of revenue from operations"))],
        top1_customer_pct=TOP1,
        top10_customers_pct=TOP10,
        top_supplier_pct=fact(31.50, "Our largest supplier accounted for 31.50% of total purchases"),
        capacity=[CapacityLine(
            facility="Krishnagiri plant",
            installed_capacity=fact(1000000, "Installed capacity of 1,000,000 units", unit="units"),
            utilisation_pct=fact(64.20, "1,000,000 units with utilisation of 64.20%"))],
        order_book=fact(1234.50, "Our order book stood at Rs. 1,234.50 million", unit="₹ million"),
        competitors=[Cited(text="TVS Motor", page=4), Cited(text="Bajaj Auto", page=4)],
        moats=[Cited(text="In-house battery cell technology", page=4)],
    )

    report = analyse(rhp, adapters, config, answer)

    assert report.unverified_fields == []
    assert report.business.top1_customer_pct == TOP1
    assert report.business.top10_customers_pct.value == 58.30
    assert report.business.top_supplier_pct.value == 31.50
    assert report.business.revenue_mix[0].share.value == 72.40
    assert report.business.capacity[0].utilisation_pct.value == 64.20
    assert report.business.order_book.value == 1234.50
    assert [c.text for c in report.business.competitors] == ["TVS Motor", "Bajaj Auto"]


def test_the_agent_reads_both_business_and_industry_pages(rhp, adapters, config):
    report = analyse(rhp, adapters, config, business())

    [call] = [c for c in adapters.llm.calls if c["task"] == "business_industry"]
    assert "[page 4]" in call["user"] and "[page 5]" in call["user"]
    assert "Total issue size" not in call["user"]  # the offer page
    assert report.business is not None


def test_a_concentration_figure_not_on_its_page_could_not_be_verified(rhp, adapters, config):
    report = analyse(rhp, adapters, config, business(top1_customer_pct=fact(42.10, TOP1.verbatim_quote)))

    assert report.business.top1_customer_pct.not_disclosed_reason == "could not verify"
    assert report.unverified_fields == ["business_industry.top1_customer_pct"]
    assert "customer_concentration" not in codes(report)  # flags use verified facts only


def test_industry_claims_are_tagged_as_from_the_company_commissioned_report(rhp, adapters, config):
    claim = IndustryClaim(claim="The EV two-wheeler market will reach Rs. 2,500 billion by 2030", page=5,
                          figure=fact(2500, "market is expected to reach Rs. 2,500 billion by 2030",
                                      unit="₹ billion", page=5))

    report = analyse(rhp, adapters, config, business(industry_claims=[claim]))

    [tagged] = report.business.industry_claims
    assert tagged.attribution == "per company-commissioned industry report"
    assert tagged.figure.value == 2500
    assert report.unverified_fields == []


@pytest.mark.parametrize("top1, top10", [
    (TOP1, TOP10),  # 24.10% > 20% and 58.30% > 50%
    (TOP1, None),  # top 1 alone: 24.10% > 20%
    (None, TOP10),  # top 10 alone: 58.30% > 50%
])
def test_customer_concentration_is_flagged(rhp, adapters, config, top1, top10):
    fields = {k: v for k, v in {"top1_customer_pct": top1, "top10_customers_pct": top10}.items() if v}

    report = analyse(rhp, adapters, config, business(**fields))

    [flag] = [f for f in report.red_flags if f.code == "customer_concentration"]
    assert flag.section == "business_industry"
    assert flag.pages == [4]


def test_customer_concentration_under_the_limits_stays_quiet(tmp_path, adapters, config):
    rhp = make_tiny_rhp(tmp_path / "quiet.pdf", business_pages=(QUIET_PAGE,))
    answer = business(top1_customer_pct=fact(12.00, "Our largest customer contributed 12.00% of revenue"),
                      top10_customers_pct=fact(45.00, "Our top 10 customers contributed 45.00% of revenue"))

    report = analyse(rhp, adapters, config, answer)

    assert report.unverified_fields == []
    assert codes(report) == []


def test_single_plant_dependence_is_flagged_with_its_page(rhp, adapters, config):
    plant = Dependence(dependent=True, evidence=Cited(text="All manufacturing at one plant in Krishnagiri", page=4))

    report = analyse(rhp, adapters, config, business(single_plant=plant))

    [flag] = [f for f in report.red_flags if f.code == "single_plant_dependence"]
    assert flag.pages == [4]
    assert "Krishnagiri" in flag.message


def test_single_geography_dependence_is_flagged(rhp, adapters, config):
    region = Dependence(dependent=True, evidence=Cited(text="Most revenue from Tamil Nadu", page=4))

    report = analyse(rhp, adapters, config, business(single_geography=region))

    assert codes(report) == ["single_geography_dependence"]


def test_no_dependence_raises_no_flag(rhp, adapters, config):
    report = analyse(rhp, adapters, config, business())

    assert codes(report) == []
