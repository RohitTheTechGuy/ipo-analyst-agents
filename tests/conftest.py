from pathlib import Path

import fitz
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tiny_rhp(tmp_path):
    """A 3-page PDF standing in for an RHP."""
    path = tmp_path / "rhp.pdf"
    doc = fitz.open()
    pages = [
        "TABLE OF CONTENTS\nSECTION I - GENERAL",
        "THE OFFER\nTotal issue size of up to Rs. 1,250.00 crore",
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
def adapters(tiny_rhp):
    from ipo_agents.adapters import Adapters
    from tests.fakes import FakeDownloader, FakeExchange, RecordingNotifier, ScriptedLLM

    return Adapters(exchange=FakeExchange([]), downloader=FakeDownloader(tiny_rhp),
                    llm=ScriptedLLM({}), notifier=RecordingNotifier())


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
