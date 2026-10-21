"""Telegram questions on an analysed RHP (rhp_qa.py), the quiet-day line. Offline: `claude` is faked."""
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from ipo_agents import cli, rhp_qa
from ipo_agents.contracts import RunSummary
from ipo_agents.notify.picks import OFFER_KEY, OFFSET_KEY, is_question, is_stop, process_picks
from ipo_agents.rhp_qa import LAST_KEY, SESSIONS_KEY, answer_question, ensure_pages, which_ipo
from ipo_agents.store import Store
from tests.fakes import make_ipo
from tests.test_picks import CHAT, FakeUpdates

ACME = make_ipo()
ZETA = ACME.model_copy(update={"ipo_id": "ZETA-2026-10-14", "name": "Zeta Ltd", "symbol": "ZETA"})
DAY = date(2026, 10, 20)


class FakeClaude:
    """Stands in for subprocess.run: `claude auth status`, then `claude -p`."""

    def __init__(self, answer="Debt is ₹120 crore (p. 7).", auth="claude.ai", fail=None):
        self.answer, self.auth, self.fail, self.calls = answer, auth, fail, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if cmd[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"loggedIn": True, "authMethod": self.auth}), "")
        if self.fail == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        if self.fail == "limit":
            out = {"is_error": True, "result": "Claude usage limit reached", "session_id": "s1"}
        else:
            out = {"is_error": False, "result": self.answer, "session_id": f"s{len(self.calls)}"}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    @property
    def asks(self):
        return [(cmd, kw) for cmd, kw in self.calls if "-p" in cmd]


def analyse(config, tiny_rhp, *ipos, status="done"):
    store = Store(config.db_path)
    for ipo in ipos:
        store.upsert(ipo, status)
        if status == "done":
            store.mark_done(ipo.ipo_id, "neutral", "sha")
            work = Path(config.work_dir) / ipo.ipo_id
            work.mkdir(parents=True, exist_ok=True)
            shutil.copy(tiny_rhp, work / "rhp.pdf")
    store.close()


@pytest.fixture
def claude_cli(monkeypatch):
    monkeypatch.setattr(rhp_qa, "claude_path", lambda: "/bin/claude")


# --- classifying replies ------------------------------------------------------------------------------

@pytest.mark.parametrize("text, question, stop", [
    ("ZETWERK: how much debt does it have?", True, False),
    ("what if they cancel the issue?", True, False),
    ("ask zetwerk promoters", True, False),
    ("who are the promoters of this company", True, False),
    ("stop", False, True), ("cancel 2", False, True), ("1,3", False, False), ("zetwerk", False, False),
])
def test_a_reply_is_a_question_a_stop_or_a_pick(text, question, stop):
    assert is_question(text) is question and is_stop(text) is stop


def test_the_ipo_is_the_one_named_else_the_last_asked_about_else_the_only_one():
    ipos = [("ACME-2026-10-13", "ACME", "Acme Ltd"), ("ZETA-2026-10-14", "ZETA", "Zeta Ltd")]
    assert which_ipo("ZETA: what is the debt?", ipos, "ACME-2026-10-13") == "ZETA-2026-10-14"
    assert which_ipo("and the year before?", ipos, "ACME-2026-10-13") == "ACME-2026-10-13"
    assert which_ipo("what is the debt?", ipos, None) is None
    assert which_ipo("acme or zeta?", ipos, None) is None
    assert which_ipo("what is the debt?", ipos[:1], None) == "ACME-2026-10-13"
    jio = [("JPL-2026-10-21", "JPL", "Jio Platforms Limited"), ("NEWCO-2026-10-21", "NEWCO", "The New Co Ltd")]
    assert which_ipo("jio: who are the promoters?", jio, None) == "JPL-2026-10-21"
    assert which_ipo("the debt?", jio, None) is None


# --- answering ------------------------------------------------------------------------------------------

def test_a_question_is_answered_from_the_rhp_on_the_subscription_with_read_only_tools(
        config, tiny_rhp, claude_cli, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-would-bill")
    analyse(config, tiny_rhp, ACME)
    fake = FakeClaude()

    reply = answer_question("ACME: how much debt?", config, run=fake)
    follow = answer_question("and the year before?", config, run=fake)

    assert reply == ("Debt is ₹120 crore (p. 7).\n\n💬 From the ACME RHP (Claude, your subscription). "
                     "Not investment advice.")
    assert follow.startswith("Debt is")
    (first, kw), (second, _) = fake.asks
    assert kw["cwd"] == Path(config.work_dir) / ACME.ipo_id and kw["stdin"] is subprocess.DEVNULL
    assert "ANTHROPIC_API_KEY" not in kw["env"]
    assert kw["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == kw["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert first[first.index("--tools") + 1] == "Read,Grep,Glob" and "--strict-mcp-config" in first
    assert first[first.index("--setting-sources") + 1] == ""
    assert "--resume" not in first and second[second.index("--resume") + 1] == "s2"
    assert (Path(config.work_dir) / ACME.ipo_id / "rhp_pages" / "page-0001.txt").exists()
    store = Store(config.db_path)
    assert store.state(LAST_KEY) == ACME.ipo_id and json.loads(store.state(SESSIONS_KEY)) == {ACME.ipo_id: "s4"}
    store.close()


def test_a_non_analysed_ipo_gets_an_error_never_another_ipos_answer(config, tiny_rhp, claude_cli):
    analyse(config, tiny_rhp, ACME)
    analyse(config, tiny_rhp, ZETA, status="waiting_rhp")
    fake = FakeClaude()

    held = answer_question("ZETA: how much debt?", config, run=fake)
    offered = answer_question("JIO: who are the promoters?", config, run=fake, offer_ids=["JIO-2026-10-21"])

    assert held.startswith("ZETA isn't analysed, so there's no RHP to answer from.")
    assert "analysed IPOs: ACME" in held
    assert offered.startswith("JIO isn't analysed")
    assert fake.calls == []


def test_nothing_analysed_or_no_ipo_named_among_several_asks_which(config, tiny_rhp, claude_cli):
    assert answer_question("how much debt?", config, run=FakeClaude()).startswith("No IPO has been analysed")
    analyse(config, tiny_rhp, ACME, ZETA)
    assert answer_question("how much debt?", config, run=FakeClaude()) == (
        "Which IPO? Analysed: ACME, ZETA. Put its name in the question.")


@pytest.mark.parametrize("fake, reason", [
    (FakeClaude(auth="console"), "isn't logged in with your Claude subscription"),
    (FakeClaude(fail="timeout"), "no answer within"),
    (FakeClaude(fail="limit"), "Claude usage limit reached"),
])
def test_no_subscription_a_timeout_or_a_limit_is_a_cant_answer_reply_never_a_paid_fallback(
        config, tiny_rhp, claude_cli, fake, reason):
    analyse(config, tiny_rhp, ACME)
    reply = answer_question("ACME: debt?", config, run=fake)
    assert reply.startswith("Can't answer right now:") and reason in reply
    if fake.auth != "claude.ai":
        assert fake.asks == []  # never asked off the subscription


def test_pages_are_written_once(tmp_path, tiny_rhp):
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(tiny_rhp, work / "rhp.pdf")
    pages = ensure_pages(work)
    first = sorted(p.name for p in pages.glob("page-*.txt"))
    (pages / first[0]).write_text("kept", encoding="utf-8")
    ensure_pages(work)
    assert first and (pages / first[0]).read_text(encoding="utf-8") == "kept"


# --- through the picks job ------------------------------------------------------------------------------

def test_the_picks_job_answers_a_question_and_listens_for_follow_ups(adapters, config):
    store = Store(config.db_path)
    store.set_state(OFFSET_KEY, "100")
    store.set_state(OFFER_KEY, json.dumps({"date": DAY.isoformat(), "ids": []}))
    store.close()
    source = FakeUpdates("ACME: how much debt?")
    asked, now = [], [0.0]

    def ask(question, config, before_slow=None, offer_ids=None, quoted=""):
        asked.append(question)
        if len(asked) == 1:
            source.add("and the year before?")  # arrives during the follow-up window
        return f"answer to {question}"

    def clock():
        now[0] += 100  # each check moves on 100 s; the 600 s window closes after a few polls
        return now[0]

    waits = []
    get = source.get_updates
    source.get_updates = lambda offset=None, timeout=0: waits.append(timeout) or get(offset)

    assert process_picks(DAY, adapters, config, source, CHAT, ask=ask, clock=clock) == []
    assert asked == ["ACME: how much debt?", "and the year before?"]
    assert adapters.notifier.messages == ["answer to ACME: how much debt?", "answer to and the year before?"]
    assert waits[0] == 0 and all(0 < w <= 50 for w in waits[1:])


# --- quiet day ------------------------------------------------------------------------------------------

def test_only_a_truly_quiet_day_gets_the_no_ipo_line():
    assert cli.quiet_day(RunSummary())
    for busy in ({"updated": ["A"]}, {"skipped": ["A"]}, {"waiting_rhp": ["A"]}, {"discovery_failed": True},
                 {"analysed": ["A"]}):
        assert not cli.quiet_day(RunSummary(**busy))


def test_a_telegram_blip_while_reading_replies_is_a_warning_not_a_failed_run(adapters, config):
    from ipo_agents.notify.telegram import TelegramError

    class Down:
        def get_updates(self, offset=None, timeout=0):
            raise TelegramError("getUpdates failed: ConnectTimeout")

    assert process_picks(DAY, adapters, config, Down(), CHAT) == []
    assert adapters.notifier.messages == []


# --- the conversation follows the chat --------------------------------------------------------------------

def test_a_reply_to_an_earlier_message_is_about_its_ipo_and_carries_its_text(config, tiny_rhp, claude_cli):
    analyse(config, tiny_rhp, ACME, ZETA)
    rhp_qa.set_topic(config, ACME.ipo_id)  # the chat was on Acme...
    fake = FakeClaude()

    reply = answer_question("why?", config, run=fake, quoted="📄 Zeta Ltd IPO\n🧭 Our call: NO BUY")

    assert "From the ZETA RHP" in reply  # ...but the reply is to Zeta's summary
    (cmd, kw), = fake.asks
    assert kw["cwd"] == Path(config.work_dir) / ZETA.ipo_id
    prompt = cmd[cmd.index("-p") + 1]
    assert prompt.startswith('The user is replying to this earlier message in the chat:\n"""\n📄 Zeta Ltd IPO')
    assert prompt.endswith("\n\nwhy?")


def test_a_reply_to_a_message_about_an_ipo_that_isnt_analysed_gets_the_error(config, tiny_rhp, claude_cli):
    analyse(config, tiny_rhp, ACME)
    fake = FakeClaude()
    reply = answer_question("is it any good?", config, run=fake, offer_ids=["JIO-2026-10-21"],
                            quoted="🚀 Analysis started: JIO (Jio Platforms Limited).")
    assert reply.startswith("JIO isn't analysed") and fake.calls == []


def test_the_summary_sent_makes_its_ipo_the_chats_topic(adapters, config):
    from tests.test_picks import offer_llm
    from tests.fakes import FakeExchange
    from ipo_agents.runner import run_daily
    adapters.llm = offer_llm(config)
    adapters.exchange = FakeExchange([ACME])
    run_daily(date(2026, 10, 13), adapters, config)
    store = Store(config.db_path)
    assert store.state(LAST_KEY) == ACME.ipo_id
    store.close()


def chat(config, *messages):
    """Telegram updates; a (text, quoted) pair is a reply to an earlier message."""
    store = Store(config.db_path)
    store.set_state(OFFSET_KEY, "100")
    store.set_state(OFFER_KEY, json.dumps({"date": DAY.isoformat(), "ids": [ZETA.ipo_id]}))
    store.close()
    source = FakeUpdates()
    source.updates = [{"update_id": 100 + n, "message": {
        "chat": {"id": int(CHAT)}, "text": m[0] if isinstance(m, tuple) else m,
        **({"reply_to_message": {"text": m[1]}} if isinstance(m, tuple) else {})}} for n, m in enumerate(messages)]
    return source


def recorder():
    asked = []

    def ask(question, config, before_slow=None, offer_ids=None, quoted=""):
        asked.append((question, quoted))
        return f"answer to {question}"
    return asked, ask


def no_window(config):
    return config.model_copy(update={"qa": config.qa.model_copy(update={"window_s": 0})})


def test_a_short_telegram_reply_to_a_bot_message_is_a_question_with_that_message(adapters, config):
    config = no_window(config)
    asked, ask = recorder()
    source = chat(config, ("why", "📄 Acme Ltd IPO\n🧭 Our call: NO BUY"))

    assert process_picks(DAY, adapters, config, source, CHAT, ask=ask) == []
    assert asked == [("why", "📄 Acme Ltd IPO\n🧭 Our call: NO BUY")]
    assert adapters.notifier.messages == ["answer to why"]


def test_soon_after_an_answer_a_short_follow_up_is_a_question_but_picks_and_stops_are_not(adapters, config):
    config = no_window(config)
    store = Store(config.db_path)
    store.set_state(rhp_qa.ANSWERED_AT_KEY, str(__import__("time").time()))
    store.close()
    asked, ask = recorder()
    source = chat(config, "more on debt", ("1", "🆕 IPOs open and ready for analysis"))

    process_picks(DAY, adapters, config, source, CHAT, ask=ask, run=lambda *a, **kw: RunSummary())
    assert asked == [("more on debt", "")]  # "1" in reply to the offer is still a pick


def test_long_after_the_last_answer_a_word_not_understood_is_not_a_question(adapters, config):
    config = no_window(config)
    store = Store(config.db_path)
    store.set_state(rhp_qa.ANSWERED_AT_KEY, "0")
    store.close()
    asked, ask = recorder()
    process_picks(DAY, adapters, config, chat(config, "zetwrk"), CHAT, ask=ask)
    assert asked == [] and adapters.notifier.messages[0].startswith("Didn't recognise: zetwrk")
