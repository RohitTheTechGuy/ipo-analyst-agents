"""Check golden expected.yaml files against their RHP PDFs.

For every fact: the verbatim_quote must appear on the stated 1-based PDF page
(whitespace runs collapsed to one space on both sides), and the fact's value
must be one of the numbers in that quote (compared by absolute value, since the
RHP shows negatives in brackets). Also checks the PDF's sha256 and page count,
and that printed_page, when given, appears as its own line on the page.

Usage: python -I tests/golden/check_expected.py [tests/golden]
PDFs are untrusted input: run with -I and only read them with PyMuPDF.
"""

import hashlib
import re
import sys
from pathlib import Path

import pymupdf
import yaml

NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def normalise(text: str) -> str:
    return " ".join(text.split())


def numbers_in(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in NUMBER.findall(text)]


def check_file(expected_path: Path) -> list[str]:
    spec = yaml.safe_load(expected_path.read_text(encoding="utf-8"))
    pdf_path = expected_path.parent / "rhp.pdf"
    name = expected_path.parent.name
    if not pdf_path.exists():
        return [f"{name}: missing {pdf_path} (download from {spec['rhp']['url']})"]

    failures = []
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if digest != spec["rhp"]["sha256"]:
        failures.append(f"{name}: sha256 mismatch ({digest})")

    with pymupdf.open(pdf_path) as doc:
        if doc.page_count != spec["rhp"]["pages"]:
            failures.append(f"{name}: page count {doc.page_count} != {spec['rhp']['pages']}")
        for fact in spec["facts"]:
            key, page_no = fact["key"], fact["page"]
            raw = doc[page_no - 1].get_text()
            page_text = normalise(raw)
            quote = normalise(fact["verbatim_quote"])
            if quote not in page_text:
                failures.append(f"{name}.{key}: quote not found on page {page_no}")
                continue
            if not any(abs(abs(fact["value"]) - n) < 1e-9 for n in numbers_in(quote)):
                failures.append(f"{name}.{key}: value {fact['value']} not in quote")
                continue
            printed = fact.get("printed_page")
            if printed is not None and str(printed) not in {l.strip() for l in raw.splitlines()}:
                failures.append(f"{name}.{key}: printed page {printed} not on page {page_no}")
                continue
            print(f"PASS {name}.{key} (page {page_no})")
    return failures


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    files = sorted(root.glob("*/expected.yaml"))
    failures = [f for path in files for f in check_file(path)]
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"{len(files)} files, {len(failures)} failures")
    return 1 if failures or not files else 0


if __name__ == "__main__":
    sys.exit(main())
