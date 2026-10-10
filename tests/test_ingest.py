import hashlib
import logging
import re

import fitz
import pytest

import ipo_agents.ingestion as ingestion
from ipo_agents.ingestion import CoverageError, ingest


def test_every_page_is_assigned_to_a_section(tiny_rhp):
    doc = ingest(tiny_rhp)

    assert doc.coverage == 1.0
    assigned = sorted(p for s in doc.sections.values() for c in s for p in range(c.page_start, c.page_end + 1))
    assert assigned == list(range(1, doc.page_count + 1))


def test_chunks_keep_page_text(tiny_rhp):
    doc = ingest(tiny_rhp)

    texts = [c.text for s in doc.sections.values() for c in s]
    assert any("Rs. 1,250.00 crore" in t for t in texts)


def test_document_keeps_the_pdf_sha256(tiny_rhp):
    doc = ingest(tiny_rhp)

    assert doc.sha256 == hashlib.sha256(tiny_rhp.read_bytes()).hexdigest()


# --- synthetic RHPs ------------------------------------------------------------

class Scan:
    """A page that is a picture of `text`, optionally with a little real text (`overlay`) on top."""

    def __init__(self, text, overlay=""):
        self.text, self.overlay = text, overlay


# The smallest RHP the guard accepts: one page per core section, plus Our Management.
CORE = [
    "SECTION II: RISK FACTORS\nAn investment in equity involves risk.",
    "OBJECTS OF THE OFFER\nThe offer comprises a fresh issue.",
    "OUR BUSINESS\nWe make scooters.",
    "OUR MANAGEMENT\nOur board has six directors.",
    "RESTATED FINANCIAL INFORMATION\nRevenue grew.",
    "OUTSTANDING LITIGATION AND MATERIAL DEVELOPMENTS\nNo criminal cases.",
]
CORE_GROUPS = ["risk_factors", "offer_objects", "business_industry", "promoters_governance", "financials",
               "fine_print_legal"]


def _toc_lines(body):
    """One ToC line per body page: its first line, dot leader, printed page number (1-based)."""
    return [f"{text.splitlines()[0]} {'.' * 40} {n}" for n, text in enumerate(body, start=1)
            if isinstance(text, str)]


def _rhp(tmp_path, body, toc=None, toc_split=None, front=(), numbered=True):
    """Cover, then the ToC (split over two pages at line `toc_split`), then `front` pages, then
    `body` pages printed 1..n (the printed number is the first line unless `numbered` is False)."""
    pages = ["RED HERRING PROSPECTUS"]
    if toc is not None:
        split = toc_split or len(toc)
        pages.append("\n".join(["TABLE OF CONTENTS", *toc[:split]]))
        if toc_split:
            pages.append("\n".join(toc[split:]))
    pages += list(front)
    for n, text in enumerate(body, start=1):
        pages.append(Scan(text.text, overlay=text.overlay) if isinstance(text, Scan)
                     else (f"{n}\n{text}" if numbered else text))
    path = tmp_path / "rhp.pdf"
    pdf = fitz.open()
    for page_text in pages:
        page = pdf.new_page()
        if isinstance(page_text, Scan):
            src = fitz.open()
            src.new_page().insert_text((72, 120), page_text.text, fontsize=28)
            page.insert_image(page.rect, stream=src[0].get_pixmap(dpi=150).tobytes("png"))
            if page_text.overlay:
                page.insert_text((72, 760), page_text.overlay)
        else:
            page.insert_text((72, 72), page_text, fontsize=8)
    pdf.save(path)
    return path


def _groups(doc):
    """Group of each page 1..N ("boilerplate" for boilerplate pages)."""
    out = []
    for page in range(1, doc.page_count + 1):
        if page in doc.boilerplate_pages:
            out.append("boilerplate")
        else:
            out.append(next(s for s, chunks in doc.sections.items() for c in chunks
                            if c.page_start <= page <= c.page_end))
    return out


def _page_text(doc, page):
    chunk = next(c for s in doc.sections.values() for c in s if c.page_start <= page <= c.page_end)
    return re.split(r"\[page \d+\]", chunk.text)[page - chunk.page_start + 1]


OTHER = "other_disclosures"


def test_toc_headings_map_to_section_groups(tmp_path):
    doc = ingest(_rhp(tmp_path, CORE, toc=_toc_lines(CORE)))

    assert _groups(doc) == [OTHER, OTHER, *CORE_GROUPS]


def test_toc_continuing_onto_a_second_page_is_read_in_full(tmp_path):
    doc = ingest(_rhp(tmp_path, CORE, toc=_toc_lines(CORE), toc_split=3))

    assert _groups(doc) == [OTHER, OTHER, OTHER, *CORE_GROUPS]


def test_toc_lines_with_roman_or_no_page_numbers_do_not_corrupt_the_next_heading(tmp_path):
    toc = ["DEFINITIONS AND ABBREVIATIONS " + "." * 40 + " i", "Page No.", *_toc_lines(CORE)]
    front = ["i\nDEFINITIONS AND ABBREVIATIONS\nTerm Description"]

    doc = ingest(_rhp(tmp_path, CORE, toc=toc, front=front))

    assert _groups(doc) == [OTHER, OTHER, "boilerplate", *CORE_GROUPS]


def test_offset_is_found_by_heading_when_pages_carry_no_printed_numbers(tmp_path):
    doc = ingest(_rhp(tmp_path, CORE, toc=_toc_lines(CORE), numbered=False))

    assert _groups(doc) == [OTHER, OTHER, *CORE_GROUPS]


def test_toc_whose_headings_never_appear_on_their_pages_raises_coverage_error(tmp_path):
    body = [text.splitlines()[1] for text in CORE]  # pages without their headings

    with pytest.raises(CoverageError):
        ingest(_rhp(tmp_path, body, toc=_toc_lines(CORE)))


def test_partial_toc_falls_back_to_headings(tmp_path):
    toc = [line for line in _toc_lines(CORE) if "RISK" in line or "BUSINESS" in line]

    doc = ingest(_rhp(tmp_path, CORE, toc=toc))

    assert _groups(doc) == [OTHER, OTHER, *CORE_GROUPS]


def test_missing_core_section_raises_coverage_error(tmp_path):
    body = CORE[:-1]  # no Outstanding Litigation

    with pytest.raises(CoverageError, match="fine_print_legal"):
        ingest(_rhp(tmp_path, body, toc=_toc_lines(body)))


def test_toc_headings_with_no_mapping_go_to_other_disclosures_and_are_logged(tmp_path, caplog):
    body = [*CORE, "A HEADING NO MAPPING KNOWS\nSomething unusual."]
    caplog.set_level(logging.INFO, logger="ipo_agents.ingestion")

    doc = ingest(_rhp(tmp_path, body, toc=_toc_lines(body)))

    assert _groups(doc) == [OTHER, OTHER, *CORE_GROUPS, OTHER]
    assert "A HEADING NO MAPPING KNOWS" in caplog.text
    assert "risk_factors" in caplog.text


def test_toc_pointing_past_the_last_page_raises_coverage_error(tmp_path):
    # A truncated download: the ToC promises pages the PDF does not have.
    toc = [*_toc_lines(CORE), "MATERIAL CONTRACTS AND DOCUMENTS FOR INSPECTION " + "." * 40 + " 99"]

    with pytest.raises(CoverageError):
        ingest(_rhp(tmp_path, CORE, toc=toc))


def test_without_a_table_of_contents_all_caps_headings_start_sections(tmp_path):
    body = [CORE[0], "PARTICULARS\nMore risks.",  # an ALL-CAPS table header is not a heading
            *CORE[1:3], "KEY REGULATIONS AND POLICIES\nThe Motor Vehicles Act applies.", *CORE[3:]]

    doc = ingest(_rhp(tmp_path, body))

    assert _groups(doc) == [OTHER, "risk_factors", "risk_factors", "offer_objects", "business_industry", OTHER,
                            *CORE_GROUPS[3:]]


def test_long_sections_split_at_page_boundaries_under_8k_tokens(tmp_path):
    # 6 pages of ~12k characters each, all Risk Factors; ~3 characters per token on dense tables
    risk = ["\n".join(f"risk {n} " + "x" * 110 for _ in range(90)) for n in range(1, 7)]
    risk[0] = "RISK FACTORS\n" + risk[0]
    path = tmp_path / "long.pdf"
    pdf = fitz.open()
    for text in [*risk, *CORE[1:]]:
        pdf.new_page().insert_text((20, 20), text, fontsize=4)
    pdf.save(path)

    doc = ingest(path)

    chunks = doc.sections["risk_factors"]
    assert len(chunks) > 1
    assert all(len(c.text) <= 8000 * 3 for c in chunks)
    pages = [p for c in chunks for p in range(c.page_start, c.page_end + 1)]
    assert pages == list(range(1, 7))
    for c in chunks:  # each page's text is in its own chunk, tagged with its page number
        for p in range(c.page_start, c.page_end + 1):
            assert f"[page {p}]" in c.text and f"risk {p} " in c.text


def test_image_only_pages_are_ocred(tmp_path):
    doc = ingest(_rhp(tmp_path, [*CORE, Scan("DECLARATION SIGNED BY DIRECTORS")]))

    assert "DECLARATION SIGNED BY DIRECTORS" in _page_text(doc, 8)


def test_image_pages_with_only_a_few_characters_of_text_are_ocred(tmp_path):
    doc = ingest(_rhp(tmp_path, [*CORE, Scan("MANAGEMENT ORGANISATION CHART", overlay="7")]))

    assert "MANAGEMENT ORGANISATION CHART" in _page_text(doc, 8)


def test_missing_tesseract_names_the_install_step(tmp_path, monkeypatch):
    path = _rhp(tmp_path, [*CORE, Scan("DECLARATION SIGNED BY DIRECTORS")])
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(ingestion, "TESSERACT_PATHS", ())

    with pytest.raises(RuntimeError, match="brew install tesseract"):
        ingest(path)



# --- golden RHPs -------------------------------------------------------------

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
        # ToC p.135; PDF 140 opens "135 / SECTION IV: ABOUT OUR COMPANY / INDUSTRY OVERVIEW"
        ("business_industry", 140),
        # ToC p.221; PDF 226 opens "221 / HISTORY AND CERTAIN CORPORATE MATTERS"
        ("promoters_governance", 226),
    },
    "ola-electric": {
        ("risk_factors", 33),        # ToC p.29; PDF 33 opens "29 / SECTION II: RISK FACTORS"
        ("offer_objects", 126),      # ToC p.122; PDF 126 opens "122 / OBJECTS OF THE OFFER"
        ("offer_price_basis", 150),  # ToC p.146; PDF 150 opens "146 / BASIS FOR OFFER PRICE"
        ("financials", 263),         # ToC p.259; PDF 263 is the restated financials divider
        ("fine_print_legal", 361),   # ToC p.357; PDF 361 opens with OUTSTANDING LITIGATION
        ("business_industry", 170),  # ToC p.166; PDF 170 opens "166 / ... INDUSTRY OVERVIEW"
        ("promoters_governance", 231),  # ToC p.227; PDF 231 opens "227 / HISTORY AND CERTAIN CORPORATE MATTERS"
    },
    "bajaj-housing-finance": {
        ("risk_factors", 41),        # ToC p.36; PDF 41 opens "36 / SECTION II: RISK FACTORS"
        ("offer_objects", 115),      # ToC p.110; PDF 115 opens "110 / OBJECTS OF THE OFFER"
        ("offer_price_basis", 121),  # ToC p.116; PDF 121 opens "116 / BASIS FOR OFFER PRICE"
        ("financials", 314),         # ToC p.309; PDF 314 is the "RESTATED FINANCIAL INFORMATION" divider
        ("fine_print_legal", 449),   # ToC p.444; PDF 449 opens with OUTSTANDING LITIGATION
        ("business_industry", 156),  # ToC p.151; PDF 156 opens "151 / ... INDUSTRY OVERVIEW"
        ("promoters_governance", 260),  # ToC p.255; PDF 260 opens "255 / HISTORY AND CERTAIN CORPORATE MATTERS"
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
