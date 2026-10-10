"""Discovery at seam A: real exchange sources replaying recorded HTTP fixtures.

Fixtures in tests/fixtures/discovery were fetched live on 2026-10-11, except
nse_ipo_current_issue.json, which NSE returned empty that day (no issue open on a Sunday).
It is built by hand from the live all-upcoming-issues records for the same IPOs (same API
family and field names), with status set to "Active".
"""
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import httpx

from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.discovery import HttpRhpDownloader, default_exchange
from ipo_agents.runner import run_daily
from tests.fakes import ScriptedLLM, offer_objects

FIXTURES = Path(__file__).parent / "fixtures" / "discovery"
OPEN_DAY = date(2026, 10, 13)  # HD Fire Protect is open 13-15 Oct 2026

NSE_LIST = "https://www.nseindia.com/api/ipo-current-issue"
NSE_DETAIL = "https://www.nseindia.com/api/ipo-detail?symbol=HDFIRE&series=EQ"
NSE_RHP = "https://nsearchives.nseindia.com/content/ipo/RHP_HDFIRE.zip"


def offer_llm():
    return ScriptedLLM({OfferObjects: offer_objects(issue_size=Fact(
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
    """Replays recorded responses by exact URL; anything unknown is a 404.

    A route is bytes (200), an int (that status), an httpx.Response, an httpx transport
    error class (raised), or a callable taking the request.
    """

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        body = self.routes.get(url)
        if body is None:
            return httpx.Response(404)
        if isinstance(body, type) and issubclass(body, httpx.TransportError):
            raise body("simulated", request=request)
        if callable(body):
            return body(request)
        if isinstance(body, httpx.Response):
            return body
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, content=body)


def wire(adapters, web: Web, **exchange_kwargs):
    client = httpx.Client(transport=httpx.MockTransport(web), follow_redirects=True)  # as make_client()
    adapters.exchange = default_exchange(client, **exchange_kwargs)
    adapters.downloader = HttpRhpDownloader(client)
    adapters.llm = offer_llm()


def as_json(obj) -> bytes:
    return json.dumps(obj).encode()


def nse_list_with(*extra_rows: dict) -> bytes:
    rows = json.loads(fixture("nse_ipo_current_issue.json"))
    return as_json(rows + list(extra_rows))


def nse_row(symbol: str, name: str, **fields) -> dict:
    row = json.loads(fixture("nse_ipo_current_issue.json"))[0]
    return row | {"symbol": symbol, "companyName": name} | fields


def nse_detail_with(**by_title: str) -> bytes:
    data = json.loads(fixture("nse_ipo_detail_HDFIRE.json"))
    for item in data["issueInfo"]["dataList"]:
        if item.get("title") in by_title:
            item["value"] = by_title[item["title"]]
    return as_json(data)


def bse_detail_with(**fields) -> bytes:
    data = json.loads(fixture("bse_issue_8034.json"))
    record = data["IPONO_0"][0]
    for key, value in fields.items():
        if value is None:
            record.pop(key)
        else:
            record[key] = value
    return as_json(data)


def chittorgarh_list_with(**fields) -> bytes:
    data = json.loads(fixture("chittorgarh_mainboard.json"))
    for row in data["reportTableData"]:
        if row["~URLRewrite_Folder_Name"] == "hd-fire-protect-ipo":
            row.update(fields)
    return as_json(data)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def slow(self, body: bytes, seconds: float):
        def respond(request):
            self.now += seconds
            return httpx.Response(200, content=body)
        return respond


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


def test_telegram_is_alerted_with_each_sources_error_when_no_source_can_list_ipos(adapters, config, tiny_rhp):
    broken_bse = bse_routes(tiny_rhp) | {BSE_DETAIL: bse_detail_with(Market_Lot=None)}
    wire(adapters, Web(nse_blocked() | broken_bse | {CG_LIST: httpx.ReadTimeout}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == []
    [msg] = adapters.notifier.messages
    assert msg.startswith("⚠️ Could not list open IPOs from any source today")
    assert "NSE: HTTP 403" in msg
    assert "BSE: KeyError 'Market_Lot'" in msg
    assert "Chittorgarh: ReadTimeout" in msg


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


def test_ipo_found_via_chittorgarh_is_not_reanalysed_when_nse_lists_it_under_its_symbol(adapters, config, tiny_rhp):
    # Chittorgarh has no NSE symbol before listing, so its id differs ("HD Fire Protect Ltd." vs
    # NSE's "HD Fire Protect Limited"/HDFIRE). Same company + same open date is the same issue.
    wire(adapters, Web(nse_blocked() | bse_blocked() | chittorgarh_routes(tiny_rhp)))
    first = run_daily(OPEN_DAY, adapters, config)
    assert len(first.analysed) == 1

    wire(adapters, Web(nse_routes(tiny_rhp)))
    second = run_daily(OPEN_DAY, adapters, config)

    assert second.analysed == []
    assert second.skipped == first.analysed
    assert adapters.llm.calls == []


# --- review fixes -------------------------------------------------------------------------

def test_mainboard_ipo_with_sme_inside_a_word_is_kept(adapters, config, tiny_rhp):
    cosmetics = nse_row("COSMICCOS", "Cosmic Cosmetics Limited")
    wire(adapters, Web(nse_routes(tiny_rhp) | {
        NSE_LIST: nse_list_with(cosmetics),
        "https://www.nseindia.com/api/ipo-detail?symbol=COSMICCOS&series=EQ": fixture("nse_ipo_detail_HDFIRE.json"),
    }))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == ["HDFIRE-2026-10-13", "COSMICCOS-2026-10-13"]


def test_empty_nse_list_falls_through_to_bse(adapters, config, tiny_rhp):
    wire(adapters, Web(nse_routes(tiny_rhp) | {NSE_LIST: b"[]"} | bse_routes(tiny_rhp)))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == ["HDFIRE-2026-10-13"]


def test_every_source_answering_empty_is_a_quiet_day_without_alert(adapters, config):
    wire(adapters, Web({"https://www.nseindia.com/": b"", NSE_LIST: b"[]", BSE_LIST: b'{"Table": []}',
                        CG_LIST: b'{"reportTableData": []}'}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == []
    assert adapters.notifier.messages == []


def test_payload_of_the_wrong_type_counts_as_a_failed_source(adapters, config, tiny_rhp):
    wire(adapters, Web(nse_routes(tiny_rhp) | {NSE_LIST: b'{"message": "Resource not found"}'} | bse_routes(tiny_rhp)))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == ["HDFIRE-2026-10-13"]


def test_nse_read_timeout_falls_through_to_bse(adapters, config, tiny_rhp):
    wire(adapters, Web(nse_routes(tiny_rhp) | {NSE_LIST: httpx.ReadTimeout} | bse_routes(tiny_rhp)))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == ["HDFIRE-2026-10-13"]


def test_source_over_its_time_budget_counts_as_failed(adapters, config, tiny_rhp):
    clock = Clock()
    slow_nse = {"https://www.nseindia.com/": clock.slow(b"", 60),
                NSE_LIST: clock.slow(fixture("nse_ipo_current_issue.json"), 60)}
    web = Web(nse_routes(tiny_rhp) | slow_nse | bse_routes(tiny_rhp))
    wire(adapters, web, clock=clock)

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == ["HDFIRE-2026-10-13"]
    assert NSE_DETAIL not in web.requests
    assert BSE_LIST in web.requests


def test_one_malformed_row_is_skipped_and_the_rest_still_analysed(adapters, config, tiny_rhp):
    broken = nse_row("BROKEN", "Broken Data Limited")
    del broken["issueEndDate"]
    wire(adapters, Web(nse_routes(tiny_rhp) | {NSE_LIST: nse_list_with(broken)}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == ["HDFIRE-2026-10-13"]


def test_unsafe_symbol_is_dropped(adapters, config, tiny_rhp):
    evil = nse_row("../../ETC", "Path Traversal Limited")
    web = Web(nse_routes(tiny_rhp) | {NSE_LIST: nse_list_with(evil)})
    wire(adapters, web)

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == ["HDFIRE-2026-10-13"]
    assert not any("ETC" in url for url in web.requests)


def chittorgarh_page(edit) -> bytes:
    return edit(fixture("chittorgarh_hd_fire_protect.html").decode("utf-8")).encode("utf-8")


def test_chittorgarh_reads_the_ipos_own_record_not_the_first_match_on_the_page(adapters, config, tiny_rhp):
    decoy = {"urlrewrite_folder_name": "decoy-ipo", "market_lot_size": 999, "issue_price_lower": 1,
             "issue_price_upper": 2, "prospectus_rhp": "https://www.bseindia.com/decoy.pdf", "nse_symbol": "DECOY"}
    push = f"<script>self.__next_f.push([1,{json.dumps(json.dumps(decoy, separators=(',', ':')))}])</script>"

    def with_decoy(page: str) -> str:
        at = page.index('<script>self.__next_f.push([1,"')
        return page[:at] + push + page[at:]

    wire(adapters, Web(nse_blocked() | bse_blocked() | chittorgarh_routes(tiny_rhp)
                       | {CG_DETAIL: chittorgarh_page(with_decoy)}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert len(summary.analysed) == 1
    assert "Band ₹258–₹271 · Lot 55" in adapters.notifier.messages[0]


def test_chittorgarh_record_missing_optional_fields_is_still_analysed(adapters, config, tiny_rhp):
    def without_optional(page: str) -> str:
        drhp = (r'\"prospectus_drhp\":\"https://nsearchives.nseindia.com/corporate/'
                r'Registration_25092025023015_HDFIREPROTECTLTD.pdf\",')
        for field in (r'\"nse_symbol\":\"\",', drhp):
            assert page.count(field) == 1
            page = page.replace(field, "")
        return page

    wire(adapters, Web(nse_blocked() | bse_blocked() | chittorgarh_routes(tiny_rhp)
                       | {CG_DETAIL: chittorgarh_page(without_optional)}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.analysed == ["HDFIREPROTECT-2026-10-13"]


def test_rhp_larger_than_the_cap_by_content_length_is_rejected(adapters, config, tiny_rhp):
    huge = httpx.Response(200, headers={"Content-Length": str(500 * 2**20)}, content=tiny_rhp.read_bytes())
    wire(adapters, Web(nse_routes(tiny_rhp) | {NSE_RHP: huge}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.failed == ["HDFIRE-2026-10-13"]
    assert adapters.llm.calls == []


def test_rhp_streaming_past_the_cap_is_rejected(adapters, config, tiny_rhp):
    mb = b"\0" * 2**20
    endless = httpx.Response(200, content=iter([tiny_rhp.read_bytes(), b"\n"] + [mb] * 160))
    wire(adapters, Web(nse_routes(tiny_rhp) | {NSE_RHP: endless}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.failed == ["HDFIRE-2026-10-13"]
    assert adapters.llm.calls == []


def zip_of(**members: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_whose_largest_member_is_not_a_pdf_is_rejected(adapters, config, tiny_rhp):
    payload = zip_of(**{"RHP.pdf": tiny_rhp.read_bytes(), "setup.exe": b"MZ" + bytes(range(256)) * 400})
    wire(adapters, Web(nse_routes(tiny_rhp) | {NSE_RHP: payload}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.failed == ["HDFIRE-2026-10-13"]
    assert adapters.llm.calls == []


def test_zip_bomb_is_rejected(adapters, config, tiny_rhp):
    payload = zip_of(**{"RHP.pdf": tiny_rhp.read_bytes() + b"\n" + b"\0" * (20 * 2**20)})
    wire(adapters, Web(nse_routes(tiny_rhp) | {NSE_RHP: payload}))

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.failed == ["HDFIRE-2026-10-13"]
    assert adapters.llm.calls == []


def test_rhp_on_a_host_outside_the_allowlist_is_never_fetched(adapters, config, tiny_rhp):
    evil = "https://evil.example/HDFireRHP.pdf"
    web = Web(nse_blocked() | bse_routes(tiny_rhp) | {BSE_DETAIL: bse_detail_with(Prospectus_GID=evil),
                                                      evil: tiny_rhp.read_bytes()})
    wire(adapters, web)

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.failed == ["HDFIRE-2026-10-13"]
    assert evil not in web.requests


def test_rhp_redirect_to_a_host_outside_the_allowlist_is_not_followed(adapters, config, tiny_rhp):
    evil = "https://evil.example/HDFireRHP.pdf"
    redirect = httpx.Response(302, headers={"Location": evil})
    web = Web(nse_routes(tiny_rhp) | {NSE_RHP: redirect, evil: zipped(tiny_rhp)})
    wire(adapters, web)

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.failed == ["HDFIRE-2026-10-13"]
    assert evil not in web.requests


def test_chittorgarh_name_with_a_parenthetical_is_the_same_company_as_nse(adapters, config, tiny_rhp):
    renamed = chittorgarh_list_with(Company='<a href="https://www.chittorgarh.com/ipo/hd-fire-protect-ipo/2628/">'
                                            'HD Fire Protect Pvt. Ltd. (HD Fire IPO)</a>')
    wire(adapters, Web(nse_blocked() | bse_blocked() | chittorgarh_routes(tiny_rhp) | {CG_LIST: renamed}))
    first = run_daily(OPEN_DAY, adapters, config)
    assert len(first.analysed) == 1

    wire(adapters, Web(nse_routes(tiny_rhp)))
    second = run_daily(OPEN_DAY, adapters, config)

    assert second.analysed == []
    assert second.skipped == first.analysed


def test_different_companies_opening_the_same_day_are_both_analysed(adapters, config, tiny_rhp):
    other = chittorgarh_list_with(Company='<a href="https://www.chittorgarh.com/ipo/hd-fire-protect-ipo/2628/">'
                                          'HD Fire Systems Ltd.</a>')
    wire(adapters, Web(nse_blocked() | bse_blocked() | chittorgarh_routes(tiny_rhp) | {CG_LIST: other}))
    run_daily(OPEN_DAY, adapters, config)

    wire(adapters, Web(nse_routes(tiny_rhp)))
    second = run_daily(OPEN_DAY, adapters, config)

    assert second.analysed == ["HDFIRE-2026-10-13"]


def test_same_name_with_a_different_close_date_is_not_merged(adapters, config, tiny_rhp):
    later_close = chittorgarh_list_with(**{"Closing Date": "16-Oct-2026"})
    wire(adapters, Web(nse_blocked() | bse_blocked() | chittorgarh_routes(tiny_rhp) | {CG_LIST: later_close}))
    run_daily(OPEN_DAY, adapters, config)

    wire(adapters, Web(nse_routes(tiny_rhp)))
    second = run_daily(OPEN_DAY, adapters, config)

    assert second.analysed == ["HDFIRE-2026-10-13"]


class BrokenLLM:
    def invoke_structured(self, **kwargs):
        raise RuntimeError("provider down")


def test_known_rhp_is_used_when_a_later_source_has_no_link(adapters, config, tiny_rhp):
    wire(adapters, Web(nse_blocked() | bse_routes(tiny_rhp)))
    adapters.llm = BrokenLLM()
    assert run_daily(OPEN_DAY, adapters, config).failed == ["HDFIRE-2026-10-13"]

    web = Web(nse_routes(tiny_rhp) | {NSE_DETAIL: fixture("nse_ipo_detail_HDFIRE_no_rhp.json"),
                                      BSE_RHP: tiny_rhp.read_bytes()})
    wire(adapters, web)
    second = run_daily(OPEN_DAY, adapters, config)

    assert second.waiting_rhp == []
    assert second.analysed == ["HDFIRE-2026-10-13"]
    assert BSE_RHP in web.requests


def test_nse_link_to_a_drhp_is_held(adapters, config, tiny_rhp):
    drhp = "https://nsearchives.nseindia.com/content/ipo/DRHP_HDFIRE.zip"
    web = Web(nse_routes(tiny_rhp) | {NSE_DETAIL: nse_detail_with(**{"Red Herring Prospectus": drhp}),
                                      drhp: zipped(tiny_rhp)})
    wire(adapters, web)

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.waiting_rhp == ["HDFIRE-2026-10-13"]
    assert drhp not in web.requests


def test_bse_link_to_a_draft_is_held(adapters, config, tiny_rhp):
    draft = "https://listing.bseindia.com/Download//PreAnchor/HDFireProtectLimitedDraft_20261008.pdf"
    web = Web(nse_blocked() | bse_routes(tiny_rhp) | {BSE_DETAIL: bse_detail_with(Prospectus_GID=draft),
                                                      draft: tiny_rhp.read_bytes()})
    wire(adapters, web)

    summary = run_daily(OPEN_DAY, adapters, config)

    assert summary.waiting_rhp == ["HDFIRE-2026-10-13"]
    assert draft not in web.requests
