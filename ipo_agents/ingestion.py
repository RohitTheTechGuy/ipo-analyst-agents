"""RHP PDF -> SectionedDocument (spec seam C)."""
from __future__ import annotations

import hashlib
import io
import re
import shutil
from collections import Counter
from pathlib import Path

import fitz
import pytesseract
from PIL import Image

from ipo_agents.contracts import Chunk, SectionedDocument

OTHER = "other_disclosures"
BOILERPLATE = "boilerplate"  # covered, never sent to a model; not a section group
MAX_CHUNK_CHARS = 8000 * 4  # ~8k tokens at ~4 characters per token

# SEBI ICDR standard headings -> section group (ipo-agents-plan.md §4). First match wins;
# anything unmatched is OTHER.
HEADING_GROUPS: list[tuple[str, str]] = [
    (r"DEFINITIONS AND ABBREVIATIONS", BOILERPLATE),
    (r"SUMMARY OF THE OFFER DOCUMENT|OFFER DOCUMENT SUMMARY", "offer_objects"),
    (r"THE (OFFER|ISSUE)$", "offer_objects"),
    (r"CAPITAL STRUCTURE", "offer_objects"),
    (r"OBJECTS? OF THE (OFFER|ISSUE)", "offer_objects"),
    (r"(OFFER|ISSUE) STRUCTURE", "offer_objects"),
    (r"TERMS OF THE (OFFER|ISSUE)", "offer_objects"),
    (r"INDUSTRY OVERVIEW|OUR BUSINESS", "business_industry"),
    (r"RESTATED .*FINANCIAL (INFORMATION|STATEMENTS)", "financials"),
    (r"OTHER FINANCIAL INFORMATION", "financials"),
    (r"MANAGEMENT'S DISCUSSION AND ANALYSIS", "financials"),
    (r"CAPITALI[SZ]ATION STATEMENT", "financials"),
    (r"FINANCIAL INDEBTEDNESS", "financials"),
    (r"RISK FACTORS", "risk_factors"),
    (r"OUTSTANDING LITIGATION", "fine_print_legal"),
    (r"GOVERNMENT AND OTHER APPROVALS", "fine_print_legal"),
    (r"MATERIAL CONTRACTS", "fine_print_legal"),
    (r"OUR MANAGEMENT", "promoters_governance"),
    (r"OUR PROMOTERS? AND PROMOTER GROUP", "promoters_governance"),
    (r"(OUR )?GROUP COMPANIES", "promoters_governance"),
    (r"HISTORY AND (CERTAIN )?CORPORATE", "promoters_governance"),
    (r"OUR SUBSIDIARIES", "promoters_governance"),
    (r"DIVIDEND POLICY", "promoters_governance"),
    (r"BASIS (FOR|OF) (OFFER|ISSUE) PRICE", "offer_price_basis"),
    # Standard headings that belong to OTHER: listed so that, without a ToC, they still end the
    # previous section.
    (r"GENERAL INFORMATION|KEY REGULATIONS|OTHER REGULATORY AND STATUTORY|(OFFER|ISSUE) PROCEDURE"
     r"|STATEMENT OF (POSSIBLE )?(SPECIAL )?TAX BENEFITS|SUMMARY OF FINANCIAL INFORMATION|FORWARD-LOOKING"
     r"|CERTAIN CONVENTIONS|MAIN PROVISIONS OF|RESTRICTIONS ON FOREIGN OWNERSHIP|SELECTED STATISTICAL"
     r"|DECLARATION", OTHER),
]

_TOC_LINE = re.compile(r"^(?P<title>.*?)\s*\.{3,}\s*(?P<page>\d+)\s*$")
_SECTION_PREFIX = re.compile(r"^SECTION [IVXL]+\s*[:\-–]\s*")


class CoverageError(Exception):
    """Raised when any page is not assigned to a section."""


def ingest(rhp_pdf: Path) -> SectionedDocument:
    data = Path(rhp_pdf).read_bytes()
    with fitz.open(stream=data, filetype="pdf") as pdf:
        texts = [_page_text(page) for page in pdf]
    page_count = len(texts)

    sections: dict[str, list[Chunk]] = {}
    boilerplate: list[int] = []
    for group, start, end in _section_ranges(texts):
        if group == BOILERPLATE:
            boilerplate.extend(range(start, end + 1))
            continue
        sections.setdefault(group, []).extend(_chunks(group, start, end, texts))

    doc = SectionedDocument(page_count=page_count, sha256=hashlib.sha256(data).hexdigest(),
                            sections=sections, boilerplate_pages=boilerplate)
    if doc.coverage < 1.0:
        raise CoverageError(f"coverage {doc.coverage:.0%} < 100%")
    return doc


def _chunks(group: str, start: int, end: int, texts: list[str]) -> list[Chunk]:
    """Pages start..end as few chunks as fit MAX_CHUNK_CHARS; a page is never split (an oversized
    page is a chunk of its own). Each page is tagged "[page N]" so facts can cite it."""
    chunks: list[Chunk] = []
    first, parts = start, []
    for p in range(start, end + 1):
        part = f"[page {p}]\n{texts[p - 1]}\n"
        if parts and sum(map(len, parts)) + len(part) > MAX_CHUNK_CHARS:
            chunks.append(Chunk(section=group, page_start=first, page_end=p - 1, text="".join(parts)))
            first, parts = p, []
        parts.append(part)
    chunks.append(Chunk(section=group, page_start=first, page_end=end, text="".join(parts)))
    return chunks


def _page_text(page: fitz.Page) -> str:
    text = page.get_text()
    return text if text.strip() else _ocr(page)


def _ocr(page: fitz.Page) -> str:
    """Text of a page with no text layer (a scan). Only called for such pages: OCR is ~1s a page."""
    if not pytesseract.pytesseract.tesseract_cmd or not shutil.which(pytesseract.pytesseract.tesseract_cmd):
        # launchd's PATH may lack Homebrew; fall back to its usual location.
        pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract") or "/opt/homebrew/bin/tesseract"
    image = Image.open(io.BytesIO(page.get_pixmap(dpi=200).tobytes("png")))
    return pytesseract.image_to_string(image)


def _section_ranges(texts: list[str]) -> list[tuple[str, int, int]]:
    """(group, first_page, last_page) per heading, 1-based PDF pages, covering 1..N with no overlap."""
    page_count = len(texts)
    starts: dict[int, str] = {1: OTHER}  # front matter (cover, ToC) until the first heading
    starts.update(_toc_starts(texts) or _heading_starts(texts))
    missing = [p for p in starts if p > page_count]
    if missing:
        # The ToC lists sections on pages the PDF does not have (e.g. a truncated download).
        raise CoverageError(f"ToC sections start on PDF pages {sorted(missing)} but the PDF has {page_count} pages")
    bounds = sorted(p for p in starts if p >= 1)
    ends = [b - 1 for b in bounds[1:]] + [page_count]
    return [(starts[b], b, e) for b, e in zip(bounds, ends)]


def _toc_starts(texts: list[str]) -> dict[int, str]:
    """PDF page -> group for each table-of-contents heading."""
    offset = _page_offset(texts)
    starts: dict[int, str] = {}
    for title, printed in _toc_entries(texts):
        starts[printed + offset] = _group(title)  # later (more specific) heading wins on a shared page
    return starts


def _heading_starts(texts: list[str]) -> dict[int, str]:
    """No ToC: a page starts a section when one of its first lines is a known ALL-CAPS heading."""
    starts: dict[int, str] = {}
    for i, text in enumerate(texts, start=1):
        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().isdigit()]
        for line in lines[:3]:
            group = _match(line) if line.isupper() else None
            if group:
                starts[i] = group
                break
    return starts


def _toc_entries(texts: list[str]) -> list[tuple[str, int]]:
    """(heading, printed page) from the first 'TABLE OF CONTENTS' page; wrapped titles are joined."""
    toc = next((t for t in texts[:20] if "TABLE OF CONTENTS" in t.upper()), None)
    if toc is None:
        return []
    entries, pending = [], ""
    for line in toc.splitlines():
        line = line.strip()
        if not line or line.upper() == "TABLE OF CONTENTS":
            continue
        m = _TOC_LINE.match(line)
        if m:
            entries.append((f"{pending} {m['title']}".strip(), int(m["page"])))
            pending = ""
        else:
            pending = f"{pending} {line}".strip()
    return entries


def _page_offset(texts: list[str]) -> int:
    """PDF index minus printed page number, by vote over pages whose first or last line is a number."""
    votes: Counter[int] = Counter()
    for i, text in enumerate(texts, start=1):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        for line in lines[:1] + lines[-1:]:
            if line.isdigit() and int(line) <= i:
                votes[i - int(line)] += 1
                break
    return votes.most_common(1)[0][0] if votes else 0


def _group(heading: str) -> str:
    return _match(heading) or OTHER


def _match(heading: str) -> str | None:
    """Group of a recognised standard heading, else None."""
    h = _normalise(heading)
    for pattern, group in HEADING_GROUPS:
        if re.match(pattern, h):
            return group
    return None


def _normalise(heading: str) -> str:
    h = re.sub(r"\s+", " ", heading.upper().replace("’", "'")).strip()
    return _SECTION_PREFIX.sub("", h)
