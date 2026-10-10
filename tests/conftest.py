import os
import subprocess
from pathlib import Path

import fitz
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _golden_dir() -> Path:
    """GOLDEN_DIR, else this checkout's tests/golden, else the main checkout's (PDFs are gitignored)."""
    if os.environ.get("GOLDEN_DIR"):
        return Path(os.environ["GOLDEN_DIR"])
    local = REPO_ROOT / "tests" / "golden"
    if any(local.glob("*/rhp.pdf")):
        return local
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return local
    return Path(common).parent / "tests" / "golden"


@pytest.fixture(scope="session")
def golden_rhp():
    """Returns a function: company slug -> path to its golden RHP, skipping if the PDF is absent."""
    root = _golden_dir()

    def _get(slug: str) -> Path:
        path = root / slug / "rhp.pdf"
        if not path.exists():
            pytest.skip(f"golden RHP not present: {path}")
        return path

    return _get


@pytest.fixture(scope="session")
def golden_doc(golden_rhp):
    """Returns a function: company slug -> its golden RHP ingested once per session (~5s each)."""
    from ipo_agents.ingestion import ingest

    cache = {}

    def _get(slug: str):
        if slug not in cache:
            cache[slug] = ingest(golden_rhp(slug))
        return cache[slug]

    return _get


TINY_OFFER_PAGE = "THE OFFER\nTotal issue size of up to Rs. 1,250.00 crore"


TINY_BUSINESS_PAGE = "OUR BUSINESS\nWe make scooters."


def make_tiny_rhp(path: Path, offer_pages: tuple[str, ...] = (TINY_OFFER_PAGE,),
                  business_pages: tuple[str, ...] = (TINY_BUSINESS_PAGE,),
                  extra_pages: tuple[str, ...] = ()) -> Path:
    """A small PDF standing in for an RHP: one short page per core section (ingest requires them),
    with the offer section from page 2 on, one page per entry of `offer_pages`; then risk factors,
    `business_pages`, financials, litigation, other regulatory disclosures, and `extra_pages` last."""
    doc = fitz.open()
    pages = [
        "TABLE OF CONTENTS\nSECTION I - GENERAL",
        *offer_pages,
        "RISK FACTORS\nWe have one plant.",
        *business_pages,
        "RESTATED FINANCIAL INFORMATION\nRevenue grew.",
        "OUTSTANDING LITIGATION AND MATERIAL DEVELOPMENTS\nNone.",
        "OTHER REGULATORY AND STATUTORY DISCLOSURES",
        *extra_pages,
    ]
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    return path


@pytest.fixture
def offer_section_only(monkeypatch):
    """Runs only the Offer & Objects section agent: for tests whose call counts, call order or spend
    arithmetic assume one section call per run (budget, routing, dedupe, offer map -> reduce)."""
    import ipo_agents.analysis as analysis
    from ipo_agents.sections import SECTIONS

    monkeypatch.setattr(analysis, "SECTIONS", {"offer_objects": SECTIONS["offer_objects"]})


@pytest.fixture
def tiny_rhp(tmp_path):
    """A 7-page RHP with the offer size on page 2."""
    return make_tiny_rhp(tmp_path / "rhp.pdf")


@pytest.fixture
def config(tmp_path):
    from ipo_agents.config import load_config

    cfg = load_config(REPO_ROOT / "config.yaml")
    return cfg.model_copy(update={"db_path": str(tmp_path / "ipo.db"), "work_dir": str(tmp_path / "work")})


@pytest.fixture
def adapters(tiny_rhp, config):
    from ipo_agents.adapters import Adapters
    from tests.fakes import FakeDownloader, FakeExchange, RecordingNotifier, ScriptedLLM

    return Adapters(exchange=FakeExchange([]), downloader=FakeDownloader(tiny_rhp),
                    llm=ScriptedLLM({}, models=config.models), notifier=RecordingNotifier())


@pytest.fixture
def rendered_pdf(monkeypatch, tmp_path):
    """Stands in for the report renderer until ticket 22 lands: seam B's Report gains a pdf_path.

    TODO(ticket 22): delete this fixture and use the real renderer output.
    """
    import ipo_agents.runner as runner

    pdf = tmp_path / "report_acme.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    real_analyse = runner.analyse_rhp
    monkeypatch.setattr(runner, "analyse_rhp", lambda *a, **k: real_analyse(*a, **k).model_copy(update={"pdf_path": str(pdf)}))
    return pdf
