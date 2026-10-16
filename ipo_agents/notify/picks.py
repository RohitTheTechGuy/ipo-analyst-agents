"""Choosing which open IPOs to analyse, since each analysis can spend up to the budget cap.

A manual run asks in the terminal. The scheduled run analyses nothing and offers the IPOs on Telegram
(offer_message). A launchd job running `run_daily.py --telegram-picks` every few minutes reads the
replies and analyses the IPOs picked (process_picks). Only messages from TELEGRAM_CHAT_ID count.

While it analyses, a background thread watches Telegram for "stop" (or "cancel") and stops the run at its
next model call (cancel.py). A reply with any word not understood starts nothing, so "cancel 2" can never
start IPO 2."""
from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable, Sequence
from datetime import date

from ipo_agents import cancel
from ipo_agents.adapters import Adapters
from ipo_agents.advisor import ledger_id
from ipo_agents.budget import BudgetMeter
from ipo_agents.config import Config
from ipo_agents.contracts import Ipo
from ipo_agents.runner import plan, run_daily
from ipo_agents.store import Store

log = logging.getLogger(__name__)

OFFER_KEY = "offer"  # {"date": ..., "ids": [...]}: what "1", "2"... in a reply refer to
OFFSET_KEY = "update_offset"  # the next Telegram update_id to read
STOPS_KEY = "handled_stops"  # update_ids of stop replies already acted on during a run
NONE_WORDS = {"none", "skip", "no", "nothing"}
STOP_WORDS = {"stop", "cancel", "abort"}
WATCH_INTERVAL_S = 20  # how often Telegram is checked for a stop while an analysis runs
UPDATES_CONTINUE = "GMP/subscription updates for analysed IPOs continue either way."


def per_ipo_cap(config: Config) -> float:
    """The most one IPO can cost: the analysis cap plus the advisor's (summary and closing day included)."""
    return config.budget.cap_usd + config.advisor.cap_usd


def _label(ipo: Ipo) -> str:
    return ipo.symbol or ipo.ipo_id


def numbered(ipos: Sequence[Ipo]) -> list[str]:
    return [f"{n}. {_label(i)} — {i.name} (closes {i.close_date:%d %b})" for n, i in enumerate(ipos, 1)]


def _words(text: str) -> list[str]:
    return [w.lstrip("/").casefold() for w in re.split(r"[\s,]+", text.strip()) if w.strip("/")]


def is_stop(text: str) -> bool:
    return bool(set(_words(text)) & STOP_WORDS)


def resolve(text: str, offer_ids: Sequence[str], keys: dict[str, str]) -> tuple[list[str], list[str]]:
    """(ipo_ids picked, words not understood). A number is a position in the offer; other words match an
    ipo_id or symbol in `keys` (casefolded). Empty text, 'none', 'skip' or a stop word picks nothing."""
    words = _words(text)
    if not words or set(words) <= NONE_WORDS or set(words) & STOP_WORDS:
        return [], []
    picked, unknown = [], []
    for word in words:
        if word == "all":
            ids = list(offer_ids)
        elif word.isdigit() and 1 <= int(word) <= len(offer_ids):
            ids = [offer_ids[int(word) - 1]]
        elif word in keys:
            ids = [keys[word]]
        else:
            unknown.append(word)
            continue
        picked += [i for i in ids if i not in picked]
    return picked, unknown


def _keys(ipos: Sequence[Ipo]) -> dict[str, str]:
    keys = {}
    for ipo in ipos:
        keys[ipo.ipo_id.casefold()] = ipo.ipo_id
        if ipo.symbol:
            keys[ipo.symbol.casefold()] = ipo.ipo_id
    return keys


def confirmation(chosen: Sequence[Ipo], cap_usd: float) -> str:
    """What was decided: the IPOs to be analysed, or that none will be. Each analysis then announces its
    own start (start_notice)."""
    if not chosen:
        return f"OK, nothing will be analysed. {UPDATES_CONTINUE}"
    names = ", ".join(f"{_label(i)} ({i.name})" for i in chosen)
    return (f"✅ Got it: analysing {names}, up to ${cap_usd:.2f} each. "
            "The report arrives here when it is done.")


def start_notice(ipo: Ipo, n: int, total: int, stoppable: bool) -> str:
    """Sent as an analysis begins. `stoppable`: the run is watched for a stop reply (the picks job); a
    terminal run stops with Ctrl+C there."""
    of = f" ({n} of {total})" if total > 1 else ""
    how = ('Reply "stop" or "cancel" to stop it.' if stoppable
           else "Started from the terminal: stop it there with Ctrl+C.")
    return f"🚀 Analysis started: {_label(ipo)} ({ipo.name}){of}. {how}"


def notice_sender(notify: Callable[[str], object], stoppable: bool) -> Callable[[Ipo, int, int], object]:
    """A run_daily `on_start` that sends start_notice through `notify`."""
    return lambda ipo, n, total: notify(start_notice(ipo, n, total, stoppable))


def ask_in_terminal(ask: Callable[[str], str], cap_usd: float,
                    notify: Callable[[str], object] | None = None) -> Callable[[list[Ipo]], list[Ipo]]:
    """A run_daily `select` that lists the IPOs ready for analysis and asks which to analyse; the answer is
    confirmed through `notify` (Telegram), like a Telegram reply. A failed send never stops the run."""
    def select(ipos: list[Ipo]) -> list[Ipo]:
        print(f"Open IPOs ready for analysis (up to ${cap_usd:.2f} each):")
        print("\n".join(f"  {line}" for line in numbered(ipos)))
        ids = [i.ipo_id for i in ipos]
        while True:
            picked, unknown = resolve(ask("Analyse which? (e.g. 1,3 / all / none) [none]: "), ids, _keys(ipos))
            if not unknown:
                break
            print(f"Not in the list: {', '.join(unknown)}")
        chosen = [i for i in ipos if i.ipo_id in picked]
        text = confirmation(chosen, cap_usd)
        print(text)
        if notify is not None:
            try:
                notify(text)
            except Exception:
                log.exception("could not send the pick confirmation")
        return chosen
    return select


def offer_message(ipos: Sequence[Ipo], cap_usd: float) -> str:
    return "\n".join([
        f"🆕 IPOs open and ready for analysis (up to ${cap_usd:.2f} each). "
        "Reply with numbers or symbols to analyse, e.g. \"1,3\":",
        *numbered(ipos),
        "",
        f"Reply \"all\" for every one, or \"none\" to skip; \"stop\" stops an analysis that is running. "
        f"{UPDATES_CONTINUE}",
    ])


def send_offer(today: date, ipos: Sequence[Ipo], adapters: Adapters, config: Config) -> None:
    """Saves the offer first, so a reply of "2" means what this message showed as 2."""
    store = Store(config.db_path)
    try:
        store.set_state(OFFER_KEY, json.dumps({"date": today.isoformat(), "ids": [i.ipo_id for i in ipos]}))
    finally:
        store.close()
    adapters.notifier.send_message(offer_message(ipos, per_ipo_cap(config)))


def _read_replies(updates_source, chat_id: str, config: Config) -> tuple[list[str], list[str]]:
    """(texts from our chat, the offered ipo_ids). Saves the new offset before anything is analysed,
    so a crash part-way never re-runs the same reply."""
    store = Store(config.db_path)
    try:
        offset = store.state(OFFSET_KEY)
        updates = updates_source.get_updates(int(offset) if offset is not None else None)
        if not updates:
            if offset is None:
                store.set_state(OFFSET_KEY, "0")  # read once: from now on every message is a reply
            return [], []
        store.set_state(OFFSET_KEY, str(max(u["update_id"] for u in updates) + 1))
        if offset is None:
            log.info("first read of Telegram replies: skipped %d earlier message(s)", len(updates))
            return [], []  # never act on messages sent before the picks job existed
        handled = set(json.loads(store.state(STOPS_KEY) or "[]"))
        if handled:
            store.set_state(STOPS_KEY, "[]")  # the offset is past them now
        texts = []
        for u in updates:
            if u["update_id"] in handled:
                continue  # a stop already acted on while the last run was going
            msg = u.get("message") or {}
            if str((msg.get("chat") or {}).get("id")) != str(chat_id):
                log.warning("ignored a Telegram message from another chat")
                continue
            if msg.get("text"):
                texts.append(msg["text"])
        offer = json.loads(store.state(OFFER_KEY) or "{}")
        return texts, offer.get("ids", [])
    finally:
        store.close()


def process_picks(today: date, adapters: Adapters, config: Config, updates_source, chat_id: str,
                  run: Callable = run_daily, watch_interval_s: float = WATCH_INTERVAL_S) -> list[str]:
    """Reads new replies and analyses the IPOs picked; returns their ipo_ids. Answers every reply."""
    texts, offer_ids = _read_replies(updates_source, chat_id, config)
    if not texts:
        return []
    todo = plan(today, adapters, config)
    ready = {i.ipo_id: i for i in todo.analyse}
    done = {i.ipo_id for i, _ in todo.skip}
    held = {i.ipo_id for i in todo.hold}
    keys = _keys([*todo.analyse, *(i for i, _ in todo.skip), *todo.hold])
    picked, unknown, stops = [], [], 0
    for text in texts:
        if is_stop(text):
            stops += 1
            continue
        ids, words = resolve(text, offer_ids, keys)
        picked += [i for i in ids if i not in picked]
        unknown += words

    lines, chosen = [], []
    for ipo_id in picked:
        if ipo_id in ready:
            chosen.append(ready[ipo_id])
        elif ipo_id in done:
            lines.append(f"{ipo_id} is already analysed.")
        elif ipo_id in held:
            lines.append(f"{ipo_id} has no final RHP yet; it can't be analysed until it does.")
        else:
            lines.append(f"{ipo_id} is not open today.")
    if stops:
        lines.append("Nothing is being analysed right now, so there is nothing to stop.")
    if unknown:
        chosen = []  # never guess: "cancel 2" must not start IPO 2
        lines.append(f"Didn't recognise: {', '.join(unknown)}. Nothing was started; reply again.")
        if todo.analyse:
            lines += ["Ready for analysis:", *numbered(todo.analyse)]
        else:
            lines.append("No IPO is waiting for analysis today.")
    if chosen or not lines:
        lines.append(confirmation(chosen, per_ipo_cap(config)))
    adapters.notifier.send_message("\n".join(lines))
    if not chosen:
        return []
    ids = {i.ipo_id for i in chosen}
    log.info("telegram picks: analysing %s", ", ".join(sorted(ids)))
    cancel.clear()
    watcher = StopWatcher(updates_source, chat_id, config, adapters.notifier, interval_s=watch_interval_s)
    watcher.start()
    try:
        summary = run(today, adapters, config, daily_updates=False,
                      select=lambda ipos: [i for i in ipos if i.ipo_id in ids],
                      on_start=notice_sender(adapters.notifier.send_message, stoppable=True))
    finally:
        watcher.finish()
        cancel.clear()
    log.info("telegram picks finished: %s", summary.model_dump())
    if summary.cancelled:
        _send_stopped(summary.cancelled, {i.ipo_id: i for i in chosen}, adapters, config)
    return sorted(ids)


def _send_stopped(ipo_ids: list[str], ipos: dict[str, Ipo], adapters: Adapters, config: Config) -> None:
    store = Store(config.db_path)
    try:
        statuses = {i: store.status(i) for i in ipo_ids}
    finally:
        store.close()
    lines = ["⏹ Stopped at your request."]
    for ipo_id in ipo_ids:
        label = _label(ipos[ipo_id]) if ipo_id in ipos else ipo_id
        if statuses[ipo_id] == "cancelled":
            spent = (BudgetMeter(ipo_id, config).spent
                     + BudgetMeter(ledger_id(ipo_id), config, cap=config.advisor.cap_usd).spent)
            lines.append(f"{label}: stopped part-way, ${spent:.2f} spent. Its progress is saved: reply {label} "
                         "to continue from there.")
        else:
            lines.append(f"{label}: not started.")
    try:
        adapters.notifier.send_message("\n".join(lines))
    except Exception:
        log.exception("could not send the stop confirmation")


def stop_reply(updates_source, chat_id: str, offset: int | None) -> int | None:
    """The update_id of the first stop reply from our chat at or after `offset`, or None. Only peeks:
    reading at the saved offset confirms nothing, so the next picks run still sees every other reply."""
    for u in updates_source.get_updates(offset):
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) == str(chat_id) and is_stop(msg.get("text") or ""):
            return u["update_id"]
    return None


class StopWatcher(threading.Thread):
    """Checks Telegram every `interval_s` while a picks run analyses; a stop reply calls cancel.request(),
    is recorded so the next picks run doesn't answer it again, and is acknowledged at once."""

    def __init__(self, updates_source, chat_id: str, config: Config, notifier, interval_s: float = WATCH_INTERVAL_S):
        super().__init__(name="telegram-stop-watcher", daemon=True)
        self._source, self._chat_id, self._config, self._notifier = updates_source, chat_id, config, notifier
        self._interval_s = interval_s
        self._done = threading.Event()

    def run(self) -> None:
        while not self._done.wait(self._interval_s):
            if self.poll():
                return

    def poll(self) -> bool:
        """One check; True once a stop was found and requested."""
        store = Store(self._config.db_path)
        try:
            offset = store.state(OFFSET_KEY)
            update_id = stop_reply(self._source, self._chat_id, int(offset) if offset is not None else None)
            if update_id is None:
                return False
            cancel.request()
            handled = json.loads(store.state(STOPS_KEY) or "[]")
            store.set_state(STOPS_KEY, json.dumps([*handled, update_id]))
        except Exception:
            log.warning("could not check Telegram for a stop", exc_info=True)
            return False
        finally:
            store.close()
        log.warning("stop requested from Telegram")
        try:
            self._notifier.send_message("⏹ Stopping at the next step; a few seconds to a minute.")
        except Exception:
            log.exception("could not acknowledge the stop")
        return True

    def finish(self) -> None:
        self._done.set()
        self.join(timeout=5)
