import hashlib
from functools import lru_cache

import fitz
import pytest

from ipo_agents.ingestion import CoverageError, ingest


def test_every_page_is_assigned_to_a_section(tiny_rhp):
    doc = ingest(tiny_rhp)

    assert doc.page_count == 3
    assert doc.coverage == 1.0
    assigned = sorted(p for s in doc.sections.values() for c in s for p in range(c.page_start, c.page_end + 1))
    assert assigned == [1, 2, 3]


def test_chunks_keep_page_text(tiny_rhp):
    doc = ingest(tiny_rhp)

    texts = [c.text for s in doc.sections.values() for c in s]
    assert any("Rs. 1,250.00 crore" in t for t in texts)


def test_document_keeps_the_pdf_sha256(tiny_rhp):
    doc = ingest(tiny_rhp)

    assert doc.sha256 == hashlib.sha256(tiny_rhp.read_bytes()).hexdigest()


def _pdf(tmp_path, pages):
    path = tmp_path / "rhp.pdf"
    pdf = fitz.open()
    for text in pages:
        pdf.new_page().insert_text((72, 72), text)
    pdf.save(path)
    return path


def _group_of(doc, page):
    if page in doc.boilerplate_pages:
        return "boilerplate"
    return next(s for s, chunks in doc.sections.items() for c in chunks if c.page_start <= page <= c.page_end)


def test_without_a_table_of_contents_all_caps_headings_start_sections(tmp_path):
    path = _pdf(tmp_path, [
        "RED HERRING PROSPECTUS",
        "1\nSECTION II: RISK FACTORS\nAn investment in equity involves risk.",
        "2\nPARTICULARS\nMore risks.",                  # an ALL-CAPS table header is not a heading
        "3\nOBJECTS OF THE OFFER\nThe offer comprises a fresh issue.",
        "4\nOUR BUSINESS\nWe make scooters.",
        "5\nKEY REGULATIONS AND POLICIES\nThe Motor Vehicles Act applies.",
    ])

    doc = ingest(path)

    assert [_group_of(doc, p) for p in range(1, 7)] == [
        "other_disclosures", "risk_factors", "risk_factors", "offer_objects", "business_industry",
        "other_disclosures"]


TOC = ("TABLE OF CONTENTS\n"
       "SECTION II: RISK FACTORS ........................ 1\n"
       "A HEADING NO MAPPING KNOWS ........................ 2\n"
       "OUR BUSINESS ........................ 3\n")


def test_toc_headings_with_no_mapping_go_to_other_disclosures(tmp_path):
    path = _pdf(tmp_path, [TOC, "1\nRISK FACTORS", "2\nA HEADING NO MAPPING KNOWS", "3\nOUR BUSINESS"])

    doc = ingest(path)

    assert [_group_of(doc, p) for p in range(1, 5)] == [
        "other_disclosures", "risk_factors", "other_disclosures", "business_industry"]
    assert doc.coverage == 1.0


def test_toc_pointing_past_the_last_page_raises_coverage_error(tmp_path):
    # A truncated download: the ToC promises pages the PDF does not have.
    toc = TOC + "MATERIAL CONTRACTS AND DOCUMENTS FOR INSPECTION ........................ 9\n"
    path = _pdf(tmp_path, [toc, "1\nRISK FACTORS", "2\nA HEADING NO MAPPING KNOWS", "3\nOUR BUSINESS"])

    with pytest.raises(CoverageError):
        ingest(path)


def test_long_sections_split_at_page_boundaries_under_8k_tokens(tmp_path):
    path = tmp_path / "long.pdf"
    pdf = fitz.open()
    for n in range(1, 7):  # 6 pages of ~12k characters (~3k tokens) each, all one section
        page = pdf.new_page()
        heading = "RISK FACTORS\n" if n == 1 else ""
        page.insert_text((20, 20), heading + "\n".join(f"risk {n} " + "x" * 110 for _ in range(100)), fontsize=4)
    pdf.save(path)

    doc = ingest(path)

    chunks = doc.sections["risk_factors"]
    assert len(chunks) > 1
    assert all(len(c.text) <= 8000 * 4 for c in chunks)  # ~4 characters per token
    pages = [p for c in chunks for p in range(c.page_start, c.page_end + 1)]
    assert pages == list(range(1, 7))
    for c in chunks:  # each page's text is in its own chunk, tagged with its page number
        for p in range(c.page_start, c.page_end + 1):
            assert f"[page {p}]" in c.text and f"risk {p} " in c.text


def _text_page_as_image(pdf, text):
    """Append a page that is only a picture of `text` (no text layer), like a scanned page."""
    src = fitz.open()
    src.new_page().insert_text((72, 120), text, fontsize=28)
    png = src[0].get_pixmap(dpi=150).tobytes("png")
    page = pdf.new_page()
    page.insert_image(page.rect, stream=png)


def test_image_only_pages_are_ocred(tmp_path):
    path = tmp_path / "scanned.pdf"
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "OTHER REGULATORY AND STATUTORY DISCLOSURES")
    _text_page_as_image(pdf, "DECLARATION SIGNED BY DIRECTORS")
    pdf.save(path)

    doc = ingest(path)

    page_two = [c.text for s in doc.sections.values() for c in s if c.page_start <= 2 <= c.page_end]
    assert "DECLARATION SIGNED BY DIRECTORS" in page_two[0]


# --- golden RHPs -------------------------------------------------------------

@lru_cache(maxsize=None)
def _ingest_cached(path):
    return ingest(path)


@pytest.fixture
def golden_doc(golden_rhp):
    return lambda slug: _ingest_cached(golden_rhp(slug))


# Hand-checked PDF page indices (1-based) where each section's heading first appears, read from
# each RHP's table of contents and confirmed by opening the page. Printed page = PDF page - offset
# (Hyundai 5, Ola 4, Bajaj 5): the printed number is the page's first or last text line.
SECTION_STARTS = {
    "hyundai-motor-india": {
        # ToC p.34; PDF 39 opens "34 / SECTION II: RISK FACTORS"
        ("risk_factors", 39),
        # ToC p.115; PDF 120 opens "115 / OBJECTS OF THE OFFER"
        ("offer_objects", 120),
        # ToC p.118; PDF 123 opens "118 / BASIS FOR OFFER PRICE"
        ("offer_price_basis", 123),
        # ToC p.262; PDF 267 is the "RESTATED CONSOLIDATED FINANCIAL INFORMATION" divider
        ("financials", 267),
        # ToC p.365; PDF 370 opens "365 / ... OUTSTANDING LITIGATION AND MATERIAL DEVELOPMENTS"
        ("fine_print_legal", 370),
    },
    "ola-electric": {
        ("risk_factors", 33),        # ToC p.29; PDF 33 opens "29 / SECTION II: RISK FACTORS"
        ("offer_objects", 126),      # ToC p.122; PDF 126 opens "122 / OBJECTS OF THE OFFER"
        ("offer_price_basis", 150),  # ToC p.146; PDF 150 opens "146 / BASIS FOR OFFER PRICE"
        ("financials", 263),         # ToC p.259; PDF 263 is the restated financials divider
        ("fine_print_legal", 361),   # ToC p.357; PDF 361 opens with OUTSTANDING LITIGATION
    },
    "bajaj-housing-finance": {
        ("risk_factors", 41),        # ToC p.36; PDF 41 opens "36 / SECTION II: RISK FACTORS"
        ("offer_objects", 115),      # ToC p.110; PDF 115 opens "110 / OBJECTS OF THE OFFER"
        ("offer_price_basis", 121),  # ToC p.116; PDF 121 opens "116 / BASIS FOR OFFER PRICE"
        ("financials", 314),         # ToC p.309; PDF 314 is the "RESTATED FINANCIAL INFORMATION" divider
        ("fine_print_legal", 449),   # ToC p.444; PDF 449 opens with OUTSTANDING LITIGATION
    },
}


@pytest.mark.parametrize("slug", sorted(SECTION_STARTS))
def test_golden_section_boundaries_match_hand_checked_pages(golden_doc, slug):
    doc = golden_doc(slug)

    for section, start in SECTION_STARTS[slug]:
        starts = {c.page_start for c in doc.sections.get(section, [])}
        assert start in starts, f"{section} should start a chunk at PDF page {start}"


# Risk Factors runs up to the page before "SECTION III: INTRODUCTION / THE OFFER"
# (ToC p.90 -> PDF 95, p.79 -> PDF 83, p.81 -> PDF 86).
RISK_FACTOR_PAGES = {"hyundai-motor-india": (39, 94), "ola-electric": (33, 82), "bajaj-housing-finance": (41, 85)}


@pytest.mark.parametrize("slug", sorted(RISK_FACTOR_PAGES))
def test_golden_risk_factors_cover_exactly_their_toc_range(golden_doc, slug):
    doc = golden_doc(slug)

    first, last = RISK_FACTOR_PAGES[slug]
    pages = sorted(p for c in doc.sections["risk_factors"] for p in range(c.page_start, c.page_end + 1))
    assert pages == list(range(first, last + 1))


# Definitions and abbreviations run from printed p.1 to the page before the next ToC heading
# (Hyundai "Summary of the Offer Document" p.16, Ola "Offer Document Summary" p.13,
# Bajaj "Certain Conventions" p.19); confirmed by the "Term / Description" glossary pages.
DEFINITION_PAGES = {"hyundai-motor-india": (6, 20), "ola-electric": (5, 16), "bajaj-housing-finance": (6, 23)}


@pytest.mark.parametrize("slug", sorted(DEFINITION_PAGES))
def test_golden_every_page_is_in_exactly_one_group_or_boilerplate(golden_doc, slug):
    doc = golden_doc(slug)

    assigned = [p for s in doc.sections.values() for c in s for p in range(c.page_start, c.page_end + 1)]
    assigned += doc.boilerplate_pages
    assert doc.coverage == 1.0
    assert sorted(assigned) == list(range(1, doc.page_count + 1))


def test_golden_scanned_declaration_page_is_ocred(golden_doc):
    # Hyundai PDF 468-475 (ToC "Declaration" p.463 on) are images with no text layer.
    doc = golden_doc("hyundai-motor-india")

    text = next(c.text for s in doc.sections.values() for c in s if c.page_start <= 468 <= c.page_end)
    assert "I hereby certify and declare" in text.split("[page 468]")[1]


@pytest.mark.parametrize("slug", sorted(DEFINITION_PAGES))
def test_golden_definitions_pages_are_boilerplate(golden_doc, slug):
    doc = golden_doc(slug)

    first, last = DEFINITION_PAGES[slug]
    assert doc.boilerplate_pages == list(range(first, last + 1))
