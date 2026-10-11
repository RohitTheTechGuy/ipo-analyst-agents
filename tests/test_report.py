"""PDF report renderer (ticket 22; plan 5.8; spec stories 42-45).

The renderer is a pure function of the Report, so these tests render hand-built Reports
(tests/report_fixture.py) and read the PDF's text back. WeasyPrint runs in a child process with the
library path it needs (conftest.render_in_subprocess); both PDFs are rendered once for the module.
"""
import re

import pytest

from ipo_agents.contracts import Fact
from ipo_agents.report import fmt
from ipo_agents.report.render import render_html, walk
from tests.conftest import pdf_text, render_in_subprocess
from tests.report_fixture import INJECTION, full_report, partial_report

SECTIONS = ["Summary", "Offer structure", "Business", "Financials", "Valuation vs peers", "Risks and fine print",
            "Promoters and governance", "Market sentiment", "Appendix"]


@pytest.fixture(scope="module")
def texts(tmp_path_factory):
    out = tmp_path_factory.mktemp("reports")
    full, partial = render_in_subprocess((full_report(), out / "full"), (partial_report(), out / "partial"))
    return {"full": pdf_text(full), "partial": pdf_text(partial)}


def test_a_full_report_has_all_nine_sections_in_order(texts):
    positions = [re.search(rf"\b{n} {re.escape(title)}\b", texts["full"]) for n, title in enumerate(SECTIONS, 1)]
    assert all(positions), [t for t, p in zip(SECTIONS, positions) if p is None]
    assert [p.start() for p in positions] == sorted(p.start() for p in positions)


def test_rhp_numbers_carry_their_page_in_indian_format(texts):
    text = texts["full"]
    assert "₹500 cr (p. 19)" in text  # issue size: ₹5,000 million
    assert "1,25,000 equity shares (p. 20)" in text  # OFS shares
    assert "₹412.6 cr (p. 287)" in text  # Fiscal 2026 revenue: ₹4,126 million
    assert "₹45.5 lakh (p. 210)" in text  # MD pay: ₹4.55 million
    assert "₹10.32 (p. 110)" in text  # basic EPS, a per-share rupee figure


def test_every_verified_rhp_number_in_the_report_shows_its_page(texts):
    text = texts["full"]
    facts = [(path, f) for path, f in walk(full_report(), "") if isinstance(f, Fact) and f.value is not None]
    missing = [path for path, f in facts if f"{fmt.fact_value(f)} (p. {f.page})" not in text]
    assert len(facts) > 60
    assert missing == []


def test_derived_numbers_say_which_pages_they_were_computed_from(texts):
    # OFS 60% of the issue: ₹300 cr OFS / ₹500 cr issue, both on p. 19.
    assert "60% of issue (computed from p. 19)" in texts["full"]
    # Market cap at the upper band: post-issue shares (p. 21) x the exchange's band.
    assert "(computed from p. 21; exchange price band)" in texts["full"]


def test_page_one_has_the_verdict_label_confidence_and_top_three_red_flags(texts):
    page_one = texts["full"].split("2 Offer structure")[0]
    assert "SUBSCRIBE – LISTING GAINS ONLY" in page_one
    assert "Confidence: MEDIUM" in page_one
    assert page_one.count("RED FLAG") == 3
    assert "A fourth flag that page 1 leaves out" not in page_one
    assert "POSITIVE RoNW of 14.2% with low debt." in page_one
    assert "Minimum investment ₹14,910 (lot 142 × upper band ₹105)" in page_one


def test_third_party_numbers_are_claimed_by_their_source(texts):
    text = texts["full"]
    assert "₹42 unofficial; claimed by IPO Watch, as of 09 Oct 2026" in text  # GMP tile
    assert "₹26,000 cr claimed by NSE, as of 09 Oct 2026" in text  # a peer's market cap


def test_appendix_lists_unverified_fields_sources_and_the_rhp_hash(texts):
    appendix = texts["full"].split("9 Appendix")[1]
    assert "offer.anchor_pct could not verify" in appendix
    assert "financials.periods[0].roce_pct not disclosed" in appendix
    assert "IPO Watch 09 Oct 2026 GMP https://ipowatch.example/gmp" in appendix
    assert "ab" * 16 in appendix.replace(" ", "")
    assert "Not investment advice" in appendix


def test_partial_report_has_the_banner_and_says_what_was_not_reached(texts):
    text = texts["partial"]
    assert text.startswith("PARTIAL — stopped at the budget cap")
    assert "VERDICT NOT REACHED" in text
    assert "₹500 cr (p. 19)" in text  # what was verified is still shown
    assert "Not reached: the run stopped at the budget cap before the financials." in text
    assert "financials not reached" in text.split("9 Appendix")[1]
    assert all(re.search(rf"\b{n} {re.escape(title)}\b", text) for n, title in enumerate(SECTIONS, 1))


def test_model_and_rhp_text_is_escaped_never_markup(texts):
    assert INJECTION in texts["full"]  # printed as text in the PDF
    html = render_html(full_report())
    assert "<script>" not in html
    assert 'href="javascript:' not in html  # a source URL that isn't http(s) is not linked


@pytest.mark.parametrize("value, decimals, expected", [
    (125000, 0, "1,25,000"), (999, 0, "999"), (1000, 0, "1,000"), (12345678.9, 1, "1,23,45,678.9"),
    (-2500000, 0, "-25,00,000"), (412.60, 2, "412.6")])
def test_indian_grouping(value, decimals, expected):
    assert fmt.indian(value, decimals) == expected


@pytest.mark.parametrize("value, unit, expected", [
    (5000, "₹ million", "₹500 cr"), (1250, "₹ crore", "₹1,250 cr"), (4.55, "₹ million", "₹45.5 lakh"),
    (2.5, "₹", "₹2.5"), (38.4, "%", "38.4%"), (7, "cases", "7 cases")])
def test_fact_display(value, unit, expected):
    assert fmt.fact_value(Fact(value=value, unit=unit, page=1, verbatim_quote="q")) == expected
