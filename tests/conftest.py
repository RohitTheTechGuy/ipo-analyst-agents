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
