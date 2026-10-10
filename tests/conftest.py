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


@pytest.fixture
def tiny_rhp(tmp_path):
    """A 7-page PDF standing in for an RHP: one short page per core section (ingest requires them),
    with the offer size on page 2."""
    path = tmp_path / "rhp.pdf"
    doc = fitz.open()
    pages = [
        "TABLE OF CONTENTS\nSECTION I - GENERAL",
        "THE OFFER\nTotal issue size of up to Rs. 1,250.00 crore",
        "RISK FACTORS\nWe have one plant.",
        "OUR BUSINESS\nWe make scooters.",
        "RESTATED FINANCIAL INFORMATION\nRevenue grew.",
        "OUTSTANDING LITIGATION AND MATERIAL DEVELOPMENTS\nNone.",
        "OTHER REGULATORY AND STATUTORY DISCLOSURES",
    ]
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    return path


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
