import json
import logging
from datetime import date

import httpx
import pytest

from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.notify.telegram import TelegramNotifier, telegram_notifier_from_env
from ipo_agents.runner import run_daily
from tests.fakes import FakeExchange, ScriptedLLM, make_ipo

TODAY = date(2026, 10, 13)
TOKEN, CHAT_ID = "123:secret-token", "-1001"


def offer_llm():
    return ScriptedLLM({OfferObjects: OfferObjects(issue_size_cr=Fact(
        value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))})


def telegram(requests, ok=True):
    def handler(request):
        requests.append(request)
        if not ok:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
        return httpx.Response(200, json={"ok": True, "result": {}})

    return TelegramNotifier(TOKEN, CHAT_ID, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_summary_is_sent_to_the_telegram_chat_as_plain_text(adapters, config):
    requests = []
    adapters.notifier = telegram(requests)
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm()

    summary = run_daily(TODAY, adapters, config)

    assert summary.notify_failed == []
    [req] = requests
    assert req.method == "POST"
    assert str(req.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.loads(req.content)
    assert body["chat_id"] == CHAT_ID
    assert "parse_mode" not in body
    assert body["text"].startswith("📄 Acme Ltd IPO (Mainboard)")
    assert "₹1,250.00 cr" in body["text"]
    assert body["text"].endswith("Not investment advice; generated from RHP + public sources.")


def test_telegram_rejecting_the_summary_is_reported_without_leaking_the_token(adapters, config, caplog):
    caplog.set_level(logging.DEBUG)
    requests = []
    adapters.notifier = telegram(requests, ok=False)
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm()

    summary = run_daily(TODAY, adapters, config)

    assert summary.analysed == ["ACME-2026-10-13"]
    assert summary.notify_failed == ["ACME-2026-10-13"]
    assert "chat not found" in caplog.text
    assert TOKEN not in caplog.text


def test_report_pdf_is_uploaded_with_send_document_after_the_summary(adapters, config, monkeypatch, tmp_path):
    # The renderer (ticket 22) does not exist yet, so stand in for seam B's output.
    import ipo_agents.runner as runner

    pdf = tmp_path / "report_acme.pdf"
    pdf.write_bytes(b"%PDF-1.4 acme report bytes")
    real_analyse = runner.analyse_rhp
    monkeypatch.setattr(runner, "analyse_rhp", lambda *a: real_analyse(*a).model_copy(update={"pdf_path": str(pdf)}))
    requests = []
    adapters.notifier = telegram(requests)
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm()

    run_daily(TODAY, adapters, config)

    assert [str(r.url).rsplit("/", 1)[-1] for r in requests] == ["sendMessage", "sendDocument"]
    doc = requests[1]
    assert doc.method == "POST"
    assert doc.headers["content-type"].startswith("multipart/form-data")
    body = doc.read()
    assert b'name="chat_id"\r\n\r\n-1001\r\n' in body
    assert b'name="caption"\r\n\r\nAcme Ltd IPO report\r\n' in body
    assert b'name="document"; filename="report_acme.pdf"' in body
    assert b"%PDF-1.4 acme report bytes" in body


def test_notifier_built_from_env_posts_to_the_configured_bot_and_chat(adapters, config, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    requests = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: requests.append(r) or httpx.Response(200, json={"ok": True, "result": {}})))
    adapters.notifier = telegram_notifier_from_env(client=client)
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm()

    run_daily(TODAY, adapters, config)

    [req] = requests
    assert str(req.url) == "https://api.telegram.org/bot999:env-token/sendMessage"
    assert json.loads(req.content)["chat_id"] == "42"


def test_notifier_from_env_refuses_to_start_without_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        telegram_notifier_from_env()
