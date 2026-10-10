"""Discovery at seam A: real exchange sources replaying recorded HTTP fixtures.

Fixtures in tests/fixtures/discovery were fetched live on 2026-10-11, except
nse_ipo_current_issue.json, which NSE returned empty that day (no issue open on a Sunday).
It is built by hand from the live all-upcoming-issues records for the same IPOs (same API
family and field names), with status set to "Active".
"""
import io
import zipfile
from datetime import date
from pathlib import Path

import httpx

from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.discovery import HttpRhpDownloader, default_exchange
from ipo_agents.runner import run_daily
from tests.fakes import ScriptedLLM

FIXTURES = Path(__file__).parent / "fixtures" / "discovery"
OPEN_DAY = date(2026, 10, 13)  # HD Fire Protect is open 13-15 Oct 2026

NSE_LIST = "https://www.nseindia.com/api/ipo-current-issue"
NSE_DETAIL = "https://www.nseindia.com/api/ipo-detail?symbol=HDFIRE&series=EQ"
NSE_RHP = "https://nsearchives.nseindia.com/content/ipo/RHP_HDFIRE.zip"


def offer_llm():
    return ScriptedLLM({OfferObjects: OfferObjects(issue_size_cr=Fact(
        value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))})


def zipped(pdf: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("HD Fire Protect Limited - RHP.pdf", pdf.read_bytes())
    return buf.getvalue()


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def nse_routes(rhp_pdf: Path) -> dict:
    return {
        "https://www.nseindia.com/": b"<html></html>",
        NSE_LIST: fixture("nse_ipo_current_issue.json"),
        NSE_DETAIL: fixture("nse_ipo_detail_HDFIRE.json"),
        NSE_RHP: zipped(rhp_pdf),
    }


class Web:
    """Replays recorded responses by exact URL; anything unknown is a 404."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        body = self.routes.get(url)
        if body is None:
            return httpx.Response(404)
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, content=body)


def wire(adapters, web: Web):
    client = httpx.Client(transport=httpx.MockTransport(web))
    adapters.exchange = default_exchange(client)
    adapters.downloader = HttpRhpDownloader(client)
    adapters.llm = offer_llm()


def test_only_open_mainboard_ipos_are_analysed(adapters, config, tiny_rhp):
    wire(adapters, Web(nse_routes(tiny_rhp)))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == ["HDFIRE-2026-10-13"]
    [msg] = adapters.notifier.messages
    assert "HD Fire Protect Limited IPO (Mainboard)" in msg
    assert "Band ₹258–₹271 · Lot 55" in msg


def test_ipo_outside_its_subscription_window_is_ignored(adapters, config, tiny_rhp):
    wire(adapters, Web(nse_routes(tiny_rhp)))

    before = run_daily(date(2026, 10, 12), adapters, config)
    after = run_daily(date(2026, 10, 16), adapters, config)

    assert before.analysed == after.analysed == []
    assert adapters.notifier.messages == []


BSE_API = "https://api.bseindia.com/BseIndiaAPI/api"
BSE_LIST = f"{BSE_API}/GetPublicIssue_par_updated/w?flag=1&status=&exchange=&ir_flag="
BSE_DETAIL = f"{BSE_API}/GetMkt_ISSUE_BBS_IPO/w?IPO_NO=8034"
BSE_RHP = "https://listing.bseindia.com/Download//PreAnchor/HDFireProtectLimitedRHP_20261008095735.pdf"


def nse_blocked() -> dict:
    return {"https://www.nseindia.com/": 403, NSE_LIST: 403}


def bse_routes(rhp_pdf: Path) -> dict:
    return {BSE_LIST: fixture("bse_public_issues.json"), BSE_DETAIL: fixture("bse_issue_8034.json"),
            BSE_RHP: rhp_pdf.read_bytes()}


CG_LIST = "https://webnodejs.chittorgarh.com/cloud/report/data-read/82/1/10/2026/2026-27/0/mainboard/0?search="
CG_DETAIL = "https://www.chittorgarh.com/ipo/hd-fire-protect-ipo/2628/"
CG_RHP = "https://www.bseindia.com/corporates/download/370731/IPO%20Open/RHPsigned_20261008112437.pdf"


def bse_blocked() -> dict:
    return {BSE_LIST: 403}


def chittorgarh_routes(rhp_pdf: Path, detail: str = "chittorgarh_hd_fire_protect.html") -> dict:
    return {CG_LIST: fixture("chittorgarh_mainboard.json"), CG_DETAIL: fixture(detail), CG_RHP: rhp_pdf.read_bytes()}


def test_chittorgarh_is_used_when_nse_and_bse_fail(adapters, config, tiny_rhp):
    wire(adapters, Web(nse_blocked() | bse_blocked() | chittorgarh_routes(tiny_rhp)))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert len(summary.analysed) == 1
    [msg] = adapters.notifier.messages
    assert "HD Fire Protect Ltd. IPO (Mainboard)" in msg
    assert "Band ₹258–₹271 · Lot 55" in msg


def test_telegram_is_alerted_when_no_source_can_list_ipos(adapters, config):
    wire(adapters, Web(nse_blocked() | bse_blocked() | {CG_LIST: 503}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == []
    assert adapters.notifier.messages == ["⚠️ Could not list open IPOs from any source today"]


def test_ipo_without_rhp_link_waits_and_is_analysed_once_the_link_appears(adapters, config, tiny_rhp):
    no_rhp_yet = Web(nse_routes(tiny_rhp) | {NSE_DETAIL: fixture("nse_ipo_detail_HDFIRE_no_rhp.json")})
    wire(adapters, no_rhp_yet)

    first = run_daily(OPEN_DAY, adapters, config)

    assert first.analysed == []
    assert first.waiting_rhp == ["HDFIRE-2026-10-13"]
    assert adapters.llm.calls == []
    assert adapters.notifier.messages == []

    web = Web(nse_routes(tiny_rhp))
    wire(adapters, web)
    second = run_daily(OPEN_DAY, adapters, config)

    assert second.analysed == ["HDFIRE-2026-10-13"]
    assert NSE_RHP in web.requests


def test_drhp_only_ipo_is_held_and_the_draft_is_never_read(adapters, config, tiny_rhp):
    web = Web(nse_blocked() | bse_blocked()
              | chittorgarh_routes(tiny_rhp, detail="chittorgarh_hd_fire_protect_drhp_only.html"))
    wire(adapters, web)

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == []
    assert len(summary.waiting_rhp) == 1
    assert not any(url.endswith(".pdf") for url in web.requests)
    assert adapters.notifier.messages == []


def test_done_ipo_is_not_reanalysed_when_next_run_finds_it_on_bse(adapters, config, tiny_rhp):
    wire(adapters, Web(nse_routes(tiny_rhp)))
    run_daily(OPEN_DAY, adapters, config)

    wire(adapters, Web(nse_blocked() | bse_routes(tiny_rhp)))
    second = run_daily(OPEN_DAY, adapters, config)

    assert second.analysed == []
    assert second.skipped == ["HDFIRE-2026-10-13"]
    assert adapters.llm.calls == []
    assert len(adapters.notifier.messages) == 1


def test_rhp_link_that_is_not_a_pdf_fails_the_ipo_and_sends_nothing(adapters, config, tiny_rhp):
    wire(adapters, Web(nse_routes(tiny_rhp) | {NSE_RHP: b"<html>Access Denied</html>"}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.failed == ["HDFIRE-2026-10-13"]
    assert adapters.llm.calls == []
    assert adapters.notifier.messages == []


def test_bse_is_used_when_nse_blocks(adapters, config, tiny_rhp):
    wire(adapters, Web(nse_blocked() | bse_routes(tiny_rhp)))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == ["HDFIRE-2026-10-13"]
    [msg] = adapters.notifier.messages
    assert "HD Fire Protect Limited IPO (Mainboard)" in msg
    assert "Band ₹258–₹271 · Lot 55" in msg
