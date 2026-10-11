import os
import subprocess
import sys
from pathlib import Path

import pymupdf as fitz
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


def pytest_configure(config):
    config.addinivalue_line("markers", "sections(*groups): the section agents analyse_rhp runs (default: offer_objects)")
    config.addinivalue_line("markers", "synthesizer: analyse_rhp makes the synthesizer call (default: it is skipped)")
    config.addinivalue_line("markers", "advisor: the Financial Advisor makes its model call (default: no advice)")
    config.addinivalue_line("markers", "renderer:analyse_rhp renders the real PDF report (default: no PDF)")


@pytest.fixture(autouse=True)
def _sections_under_test(request, monkeypatch):
    """Runs only the section agents a test is about: `@pytest.mark.sections("financials", ...)`, else
    offer_objects. Section agents run in parallel, so tests that script calls in order need one."""
    import ipo_agents.analysis as analysis
    from ipo_agents.sections import SECTIONS

    marker = request.node.get_closest_marker("sections")
    groups = marker.args if marker else ("offer_objects",)
    monkeypatch.setattr(analysis, "SECTIONS", {g: SECTIONS[g] for g in groups})


@pytest.fixture(autouse=True)
def _synthesizer_under_test(request, monkeypatch):
    """The synthesizer node runs in every graph, but makes its model call only in tests marked
    `@pytest.mark.synthesizer`; elsewhere it returns no synthesis (verdict None), so tests that script
    and count other calls aren't changed by it."""
    import ipo_agents.analysis as analysis

    if request.node.get_closest_marker("synthesizer") is None:
        monkeypatch.setattr(analysis, "synthesize", lambda report, llm: None)


@pytest.fixture(autouse=True)
def _advisor_under_test(request, monkeypatch):
    """The Financial Advisor runs after every complete analysis and in the closing-day refresh, but makes
    its model call only in tests marked `@pytest.mark.advisor`; elsewhere it gives no advice ("not
    reached"), so tests that script and count other calls aren't changed by it."""
    import ipo_agents.advisor as advisor
    import ipo_agents.analysis as analysis

    if request.node.get_closest_marker("advisor") is None:
        def no_advice(report, sentiment, llm, config, today):
            return None
        monkeypatch.setattr(analysis, "advise", no_advice)
        monkeypatch.setattr(advisor, "advise", no_advice)


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
def tiny_rhp(tmp_path):
    """A 7-page RHP with the offer size on page 2."""
    return make_tiny_rhp(tmp_path / "rhp.pdf")


@pytest.fixture
def config(tmp_path):
    from ipo_agents.config import load_config

    cfg = load_config(REPO_ROOT / "config.yaml")
    # Tests pin the budget and output limits their hand-worked arithmetic uses, so tuning production
    # limits in config.yaml (e.g. the $5/IPO revision) doesn't silently change what the tests prove.
    return cfg.model_copy(update={
        "db_path": str(tmp_path / "ipo.db"), "work_dir": str(tmp_path / "work"), "reports_dir": str(tmp_path / "reports"),
        "default_max_tokens": 4000, "map_max_tokens": 8000,
        "budget": cfg.budget.model_copy(update={"cap_usd": 1.50, "synth_reserve_usd": 0.15}),
        "advisor": cfg.advisor.model_copy(update={"cap_usd": 0.50, "max_tokens": 12000}),
    })


@pytest.fixture
def adapters(tiny_rhp, config):
    from ipo_agents.adapters import Adapters
    from tests.fakes import FakeDownloader, FakeExchange, RecordingNotifier, ScriptedLLM

    return Adapters(exchange=FakeExchange([]), downloader=FakeDownloader(tiny_rhp),
                    llm=ScriptedLLM({}, models=config.models), notifier=RecordingNotifier())


RENDER_ENV = {"DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib"}  # Homebrew pango for WeasyPrint (macOS)
_RENDER = """
import sys
from pathlib import Path
from ipo_agents.contracts import Report
from ipo_agents.report.render import render_pdf
args = sys.argv[1:]
for src, out in zip(args[::2], args[1::2]):
    print(render_pdf(Report.model_validate_json(Path(src).read_text(encoding="utf-8")), Path(out)))
"""


def render_in_subprocess(*jobs) -> list[Path]:
    """Renders each (report, out_dir) with the real renderer, in one child Python that has the library
    path WeasyPrint needs, however pytest itself was launched. Returns the PDF paths in order."""
    args = []
    for i, (report, out_dir) in enumerate(jobs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        src = out_dir / f"report_{i}.json"
        src.write_text(report.model_dump_json(), encoding="utf-8")
        args += [str(src), str(out_dir)]
    proc = subprocess.run([sys.executable, "-c", _RENDER, *args], cwd=REPO_ROOT, capture_output=True, text=True,
                          env={**os.environ, **RENDER_ENV}, timeout=120)
    if proc.returncode:
        raise RuntimeError(f"renderer subprocess failed:\n{proc.stderr}")
    return [Path(line) for line in proc.stdout.splitlines()]


def pdf_text(path: Path) -> str:
    """The PDF's text, whitespace collapsed to single spaces (table cells and wrapped lines join)."""
    with fitz.open(path) as doc:
        return " ".join(" ".join(page.get_text().split()) for page in doc)


@pytest.fixture(autouse=True)
def _renderer_under_test(request, monkeypatch):
    """analyse_rhp renders the real PDF only in tests marked `@pytest.mark.renderer` (in a child process,
    see render_in_subprocess); elsewhere the renderer reports itself unavailable and no PDF is attached,
    which keeps the suite fast."""
    import ipo_agents.analysis as analysis
    from ipo_agents.report.render import RendererUnavailable

    if request.node.get_closest_marker("renderer") is None:
        def unavailable(report, out_dir):
            raise RendererUnavailable("not rendered in this test (mark it @pytest.mark.renderer)")
        monkeypatch.setattr(analysis, "render_pdf", unavailable)
    else:
        monkeypatch.setattr(analysis, "render_pdf", lambda report, out_dir: render_in_subprocess((report, out_dir))[0])
