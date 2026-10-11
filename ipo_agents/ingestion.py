"""RHP PDF -> SectionedDocument (spec seam C)."""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf as fitz
import pytesseract
from PIL import Image

from ipo_agents.contracts import Chunk, SectionedDocument

log = logging.getLogger(__name__)

OTHER = "other_disclosures"
BOILERPLATE = "boilerplate"  # covered, never sent to a model; not a section group
# Every real RHP has these; ingest fails if any ends up empty.
CORE_GROUPS = ("risk_factors", "offer_objects", "business_industry", "financials", "fine_print_legal")
MIN_TOC_GROUPS = 5   # a ToC mapping fewer distinct groups is too partial to trust
MIN_VERIFIED = 0.8   # share of ToC headings that must appear on their computed page
MAX_CHUNK_CHARS = 8000 * 3  # ~8k tokens at ~3 characters per token (numeric tables tokenize densely)
OCR_BELOW_CHARS = 50  # an image page with less text than this is treated as a scan
TESSERACT_PATHS = ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract")

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


_TOC_LINE = re.compile(r"^(?P<title>.*?)\s*\.{3,}\s*(?P<page>\d+|[ivxlcdm]+)\s*$", re.IGNORECASE)
_SECTION_PREFIX = re.compile(r"^SECTION [IVXL]+\s*[:\-–]\s*")


class CoverageError(Exception):
    """Raised when any page is not assigned to a section, or the section map can't be trusted."""


class TesseractMissing(RuntimeError):
    """Raised when a scanned page needs OCR but tesseract is not installed."""


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

    log.info("section map: %s; boilerplate pages %s", {
        g: _runs(sorted(p for c in chunks for p in range(c.page_start, c.page_end + 1)))
        for g, chunks in sections.items()}, _runs(boilerplate))
    doc = SectionedDocument(page_count=page_count, sha256=hashlib.sha256(data).hexdigest(),
                            sections=sections, boilerplate_pages=boilerplate)
    if doc.coverage < 1.0:
        raise CoverageError(f"coverage {doc.coverage:.0%} < 100%")
    empty = [g for g in CORE_GROUPS if not sections.get(g)]
    if empty:
        # Every real RHP has these; an empty one means a section was mis-mapped and no agent reads it.
        raise CoverageError(f"no pages mapped to {empty}")
    return doc


def _runs(pages: list[int]) -> list[str]:
    runs: list[list[int]] = []
    for p in pages:
        if runs and runs[-1][1] + 1 == p:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    return [f"{a}-{b}" for a, b in runs]


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


_PAGE_TAG = re.compile(r"^\[page (\d+)\]\n", re.MULTILINE)


def page_texts(chunks: list[Chunk]) -> dict[int, str]:
    """Page number -> that page's text, recovered from the chunks' "[page N]" tags."""
    pages: dict[int, str] = {}
    for chunk in chunks:
        parts = _PAGE_TAG.split(chunk.text)  # ["", "N", text, "N+1", text, ...]
        pages.update((int(n), text) for n, text in zip(parts[1::2], parts[2::2]))
    return pages


def _page_text(page: fitz.Page) -> str:
    """The text layer, plus OCR for scans: pages with no text, or with a few characters over an image."""
    text = page.get_text()
    if not text.strip():
        return _ocr(page)
    if len(text.strip()) < OCR_BELOW_CHARS and page.get_images():
        return f"{text}\n{_ocr(page)}"
    return text


def _ocr(page: fitz.Page) -> str:
    """OCR one page (~1s), so it runs only on pages that need it."""
    pytesseract.pytesseract.tesseract_cmd = _tesseract()
    image = Image.open(io.BytesIO(page.get_pixmap(dpi=200).tobytes("png")))
    return pytesseract.image_to_string(image)


def _tesseract() -> str:
    # launchd's PATH may lack Homebrew, so also try its usual locations.
    found = shutil.which("tesseract") or next((p for p in TESSERACT_PATHS if os.access(p, os.X_OK)), None)
    if not found:
        raise TesseractMissing("tesseract is needed to OCR scanned RHP pages; install it with `brew install tesseract`")
    return found


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
    """PDF page -> group for each table-of-contents heading; {} when the ToC is missing or too
    partial to trust (the caller then scans headings). Raises CoverageError when the ToC's page
    numbers can't be matched to the pages that carry its headings."""
    toc_pages, entries = _toc_entries(texts)
    mapped = {_group(title) for title, _ in entries} - {OTHER, BOILERPLATE}
    if len(mapped) < MIN_TOC_GROUPS:
        if entries:
            log.info("ToC maps only %s; scanning headings instead", sorted(mapped))
        return {}

    arabic = [(title, int(page)) for title, page in entries if page.isdigit()]
    offset = _verified_offset(texts, toc_pages, arabic)
    starts: dict[int, str] = {}
    for title, printed in arabic:
        starts[printed + offset] = _group(title)  # later (more specific) heading wins on a shared page
    # Roman-numbered front matter sits between the ToC and printed page 1: find it by its heading.
    first_arabic = min(starts, default=len(texts) + 1)
    for title, page in entries:
        if not page.isdigit():
            found = next((p for p in range(max(toc_pages) + 1, first_arabic)
                          if _shows_heading(texts[p - 1], title)), None)
            if found:
                starts[found] = _group(title)
            else:
                log.info("ToC heading %r (page %s) not found; its pages stay with the previous section", title, page)
    log.info("ToC headings read as %s: %s", OTHER, [t for t, _ in entries if _group(t) == OTHER])
    return starts


def _verified_offset(texts: list[str], toc_pages: list[int], entries: list[tuple[str, int]]) -> int:
    """The printed->PDF page offset under which most ToC headings appear at the top of their page:
    the page-number vote if it checks out, else the best offset within ±10 of it."""
    def score(offset: int) -> float:
        hits = sum(1 for title, printed in entries
                   if 1 <= printed + offset <= len(texts) and printed + offset not in toc_pages
                   and _shows_heading(texts[printed + offset - 1], title))
        return hits / len(entries) if entries else 0.0

    voted = _page_offset(texts)
    if score(voted) >= MIN_VERIFIED:
        return voted
    best = max(range(voted - 10, voted + 11), key=lambda o: (score(o), -abs(o - voted)))
    if score(best) < MIN_VERIFIED:
        raise CoverageError(f"ToC page numbers don't match the document: at best {score(best):.0%} of "
                            f"headings found on their pages (offset {best}; page-number vote {voted})")
    log.info("page-number vote gave offset %s; headings verify offset %s instead", voted, best)
    return best


def _shows_heading(text: str, title: str) -> bool:
    """Loose match: the title's start appears in one of the page's first lines, ignoring case,
    spacing and punctuation, tolerating a few OCR-style character errors."""
    key = _alnum(title)[:25]
    if not key:
        return False
    lines = [_alnum(line) for line in text.splitlines() if line.strip()][:8]
    if key in "".join(lines):
        return True
    return any(SequenceMatcher(None, key, line[:len(key)]).ratio() >= 0.8 for line in lines)


def _alnum(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", _normalise(s))


def _heading_starts(texts: list[str]) -> dict[int, str]:
    """No usable ToC: a page starts a section when one of its first lines is a known ALL-CAPS heading."""
    starts: dict[int, str] = {}
    for i, text in enumerate(texts, start=1):
        lines = [l.strip() for l in text.splitlines()
                 if l.strip() and not l.strip().isdigit() and "..." not in l]  # skip ToC lines
        for line in lines[:3]:
            group = _match(line) if line.isupper() else None
            if group:
                starts[i] = group
                break
    return starts


def _toc_entries(texts: list[str]) -> tuple[list[int], list[tuple[str, str]]]:
    """(ToC PDF pages, [(heading, printed page as written)]). The ToC starts at the first page with a
    standalone 'TABLE OF CONTENTS' line and runs on while following pages are mostly ToC lines."""
    start = next((i for i, t in enumerate(texts[:20], start=1)
                  if any(l.strip().upper() == "TABLE OF CONTENTS" for l in t.splitlines())), None)
    if start is None:
        return [], []
    pages = [start]
    while pages[-1] < len(texts) and _is_toc_page(texts[pages[-1]]):
        pages.append(pages[-1] + 1)

    entries, pending = [], ""
    for line in (l.strip() for p in pages for l in texts[p - 1].splitlines()):
        if not line or line.upper() == "TABLE OF CONTENTS" or line.isdigit():
            continue
        m = _TOC_LINE.match(line)
        if m:
            entries.append((f"{pending} {m['title']}".strip(), m["page"].lower()))
            pending = ""
        elif line.isupper() and "..." not in line:
            pending = f"{pending} {line}".strip()  # a title wrapped onto the next line
        else:
            pending = ""  # a stray line ("Page No.") or a dot leader with no page number
    return pages, entries


def _is_toc_page(text: str) -> bool:
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().isdigit()]
    return bool(lines) and sum(1 for l in lines if _TOC_LINE.match(l)) >= max(3, len(lines) // 2)


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
