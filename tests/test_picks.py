"""Choosing which IPOs to analyse: terminal prompt, --updates-only, Telegram offer and replies. Offline."""
import json
from datetime import date

import pytest

from ipo_agents import cli
from ipo_agents.contracts import Fact, OfferObjects, RunSummary
from ipo_agents.notify.picks import OFFER_KEY, OFFSET_KEY, process_picks, resolve
from ipo_agents.runner import run_daily
from ipo_agents.store import Store
from tests.fakes import FakeExchange, ScriptedLLM, make_ipo, offer_objects

DAY1, DAY2 = date(2026, 10, 13), date(2026, 10, 14)
CHAT = "-1001"
ACME = make_ipo().model_copy(update={"close_date": date(2026, 10, 16)})
ZETA = ACME.model_copy(update={"ipo_id": "ZETA-2026-10-14", "name": "Zeta Ltd", "symbol": "ZETA",
                               "open_date": DAY2})


def offer_llm(config):
    return ScriptedLLM({OfferObjects: offer_objects(issue_size=Fact(
        value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))},
        models=config.models)


@pytest.fixture
def config_file(tmp_path, config):
    path = tmp_path / "config.yaml"
    path.write_text(config.model_dump_json())
    return path


def run(argv, adapters, config_file, tmp_path, *, interactive=False, answers=()):
    answers = list(answers)
    try:
        return cli.main([*argv, "--config", str(config_file)], build=lambda cfg, **kw: adapters,
                        ask=lambda prompt: answers.pop(0), interactive=lambda: interactive,
                        log_dir=tmp_path / "logs", env_file=tmp_path / "no.env")
    finally:
        cli.remove_logging()


def analysed_ids(config):
    store = Store(config.db_path)
    try:
        return {i for i in (ACME.ipo_id, ZETA.ipo_id) if store.status(i) == "done"}
    finally:
        store.close()


@pytest.fixture
def acme_done(adapters, config):
    """Acme analysed on day 1 and still open on day 2, when Zeta opens."""
    adapters.llm = offer_llm(config)
    adapters.exchange = FakeExchange([ACME])
    run_daily(DAY1, adapters, config)
    adapters.exchange = FakeExchange([ACME, ZETA])
    adapters.notifier.messages.clear()
    return adapters


def updates_sent(adapters):
    return [m for m in adapters.notifier.messages if m.startswith(("📊", "⏰"))]


# --- parsing ---------------------------------------------------------------------------------------

def test_resolve_reads_numbers_symbols_all_and_none():
    offer, keys = ["A-1", "B-1"], {"a-1": "A-1", "acme": "A-1", "b-1": "B-1", "zeta": "B-1"}

    assert resolve("2", offer, keys) == (["B-1"], [])
    assert resolve("1, zeta", offer, keys) == (["A-1", "B-1"], [])
    assert resolve("ACME acme", offer, keys) == (["A-1"], [])
    assert resolve("all", offer, keys) == (["A-1", "B-1"], [])
    assert resolve("", offer, keys) == ([], [])
    assert resolve("None", offer, keys) == ([], [])
    assert resolve("3 bogus", offer, keys) == ([], ["3", "bogus"])


# --- command line ----------------------------------------------------------------------------------

def test_terminal_prompt_analyses_only_the_ipo_picked(acme_done, config, config_file, tmp_path, capsys):
    other = ZETA.model_copy(update={"ipo_id": "OMEGA-2026-10-14", "name": "Omega Ltd", "symbol": "OMEGA"})
    acme_done.exchange = FakeExchange([ACME, ZETA, other])

    code = run(["--date", "2026-10-14"], acme_done, config_file, tmp_path, interactive=True, answers=["nope", "zeta"])

    assert code == 0
    out = capsys.readouterr().out
    assert "1. ZETA — Zeta Ltd (closes 16 Oct)" in out and "2. OMEGA" in out
    assert "Not in the list: nope" in out
    assert analysed_ids(config) == {ACME.ipo_id, ZETA.ipo_id}
    store = Store(config.db_path)
    assert store.status(other.ipo_id) is None  # untouched: offered again tomorrow
    store.close()
    assert len(updates_sent(acme_done)) == 1  # Acme's update still goes out


@pytest.mark.parametrize("argv, interactive, answers", [
    (["--updates-only"], True, []),
    ([], True, [""]),  # Enter = none
    ([], False, []),  # no terminal to ask: analyse none
])
def test_analysing_none_still_sends_the_updates(acme_done, config, config_file, tmp_path, argv, interactive, answers):
    code = run([*argv, "--date", "2026-10-14"], acme_done, config_file, tmp_path,
               interactive=interactive, answers=answers)

    assert code == 0
    assert analysed_ids(config) == {ACME.ipo_id}
    assert len(updates_sent(acme_done)) == 1


def test_ask_telegram_sends_updates_then_the_offer_and_saves_it(acme_done, config, config_file, tmp_path):
    code = run(["--ask-telegram", "--date", "2026-10-14"], acme_done, config_file, tmp_path)

    assert code == 0
    assert analysed_ids(config) == {ACME.ipo_id}
    update, offer = acme_done.notifier.messages
    assert update.startswith(("📊", "⏰"))
    assert offer.startswith("🆕") and "1. ZETA — Zeta Ltd (closes 16 Oct)" in offer
    assert '"none" to skip' in offer and "updates for analysed IPOs continue" in offer
    store = Store(config.db_path)
    assert json.loads(store.state(OFFER_KEY)) == {"date": "2026-10-14", "ids": [ZETA.ipo_id]}
    store.close()


def test_ipo_cannot_be_combined_with_a_pick_mode(adapters, config_file, tmp_path):
    with pytest.raises(SystemExit):
        run(["--ipo", "ACME", "--ask-telegram"], adapters, config_file, tmp_path)


# --- Telegram replies ------------------------------------------------------------------------------

class FakeUpdates:
    def __init__(self, *messages, chat=CHAT):
        self.offsets = []
        self.updates = [{"update_id": 100 + n, "message": {"chat": {"id": int(m[1]) if isinstance(m, tuple) else int(chat)},
                                                           "text": m[0] if isinstance(m, tuple) else m}}
                        for n, m in enumerate(messages)]

    def add(self, text):
        self.updates.append({"update_id": self.updates[-1]["update_id"] + 1,
                             "message": {"chat": {"id": int(CHAT)}, "text": text}})

    def get_updates(self, offset=None):
        self.offsets.append(offset)
        return [u for u in self.updates if offset is None or u["update_id"] >= offset]


def offered(config, *ipos):
    store = Store(config.db_path)
    store.set_state(OFFER_KEY, json.dumps({"date": DAY2.isoformat(), "ids": [i.ipo_id for i in ipos]}))
    store.set_state(OFFSET_KEY, "100")  # the picks job has read Telegram before
    store.close()


def test_a_numbered_reply_analyses_that_ipo_once(acme_done, config):
    offered(config, ZETA)
    source = FakeUpdates("1")

    assert process_picks(DAY2, acme_done, config, source, CHAT) == [ZETA.ipo_id]
    assert process_picks(DAY2, acme_done, config, source, CHAT) == []  # the same reply is not read twice

    assert analysed_ids(config) == {ACME.ipo_id, ZETA.ipo_id}
    assert source.offsets == [100, 101]
    assert any(m.startswith("▶️ Starting analysis of ZETA (Zeta Ltd)") for m in acme_done.notifier.messages)
    assert updates_sent(acme_done) == []  # updates belong to the daily run


def test_messages_from_another_chat_are_ignored(acme_done, config):
    offered(config, ZETA)

    assert process_picks(DAY2, acme_done, config, FakeUpdates(("1", "-999")), CHAT) == []
    assert analysed_ids(config) == {ACME.ipo_id}
    assert acme_done.notifier.messages == []


def test_offset_is_saved_before_the_analysis_runs(acme_done, config):
    offered(config, ZETA)

    def crash(*a, **kw):
        raise RuntimeError("killed mid-run")

    with pytest.raises(RuntimeError):
        process_picks(DAY2, acme_done, config, FakeUpdates("zeta"), CHAT, run=crash)
    store = Store(config.db_path)
    assert store.state(OFFSET_KEY) == "101"
    store.close()


def test_replies_for_none_done_and_unknown_are_answered_without_analysing(acme_done, config):
    offered(config, ZETA)
    ran = []

    def fake_run(*a, **kw):
        ran.append(kw)
        return RunSummary()

    source = FakeUpdates("none")
    process_picks(DAY2, acme_done, config, source, CHAT, run=fake_run)
    for text in ("acme", "bogus"):
        source.add(text)
        process_picks(DAY2, acme_done, config, source, CHAT, run=fake_run)

    none_reply, done_reply, unknown_reply = acme_done.notifier.messages
    assert none_reply.startswith("OK, nothing will be analysed")
    assert done_reply == f"{ACME.ipo_id} is already analysed."
    assert "Didn't recognise: bogus." in unknown_reply and "1. ZETA" in unknown_reply
    assert ran == []


def test_first_read_skips_messages_sent_before_the_picks_job_existed(acme_done, config):
    source = FakeUpdates("zeta")

    assert process_picks(DAY2, acme_done, config, source, CHAT) == []
    assert analysed_ids(config) == {ACME.ipo_id} and acme_done.notifier.messages == []
    source.add("zeta")
    assert process_picks(DAY2, acme_done, config, source, CHAT) == [ZETA.ipo_id]


def test_an_empty_first_read_still_counts_so_the_next_reply_is_acted_on(acme_done, config):
    source = FakeUpdates("zeta")
    source.updates.clear()

    assert process_picks(DAY2, acme_done, config, source, CHAT) == []
    source.updates.append({"update_id": 500, "message": {"chat": {"id": int(CHAT)}, "text": "zeta"}})
    assert process_picks(DAY2, acme_done, config, source, CHAT) == [ZETA.ipo_id]
