"""RHP PDF -> SectionedDocument (spec seam C)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import fitz

from ipo_agents.contracts import Chunk, SectionedDocument


class CoverageError(Exception):
    """Raised when any page is not assigned to a section."""


def ingest(rhp_pdf: Path) -> SectionedDocument:
    data = Path(rhp_pdf).read_bytes()
    with fitz.open(stream=data, filetype="pdf") as pdf:
        chunks = [
            Chunk(section="other_disclosures", page_start=i, page_end=i, text=page.get_text())
            for i, page in enumerate(pdf, start=1)
        ]
        page_count = pdf.page_count
    doc = SectionedDocument(
        page_count=page_count,
        sha256=hashlib.sha256(data).hexdigest(),
        sections={"other_disclosures": chunks},
    )
    if doc.coverage < 1.0:
        raise CoverageError(f"coverage {doc.coverage:.0%} < 100%")
    return doc
