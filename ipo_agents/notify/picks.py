"""Choosing which open IPOs to analyse, since each analysis can spend up to the budget cap.

A manual run asks in the terminal. The scheduled run analyses nothing and offers the IPOs on Telegram
(offer_message). A launchd job running `run_daily.py --telegram-picks` every few minutes reads the
replies and analyses the IPOs picked (process_picks). Only messages from TELEGRAM_CHAT_ID count."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from datetime import date

from ipo_agents.adapters import Adapters
from ipo_agents.config import Config
from ipo_agents.contracts import Ipo
from ipo_agents.runner import plan, run_daily
from ipo_agents.store import Store

log = logging.getLogger(__name__)

OFFER_KEY = "offer"  # {"date": ..., "ids": [...]}: what "1", "2"... in a reply refer to
OFFSET_KEY = "update_offset"  # the next Telegram update_id to read
NONE_WORDS = {"none", "skip", "no", "nothing"}
UPDATES_CONTINUE = "GMP/subscription updates for analysed IPOs continue either way."


def _label(ipo: Ipo) -> str:
    return ipo.symbol or ipo.ipo_id


def numbered(ipos: Sequence[Ipo]) -> list[str]:
    return [f"{n}. {_label(i)} — {i.name} (closes {i.close_date:%d %b})" for n, i in enumerate(ipos, 1)]


def resolve(text: str, offer_ids: Sequence[str], keys: dict[str, str]) -> tuple[list[str], list[str]]:
    """(ipo_ids picked, words not understood). A number is a position in the offer; other words match an
    ipo_id or symbol in `keys` (casefolded). Empty text, 'none' or 'skip' picks nothing."""
    words = [w.lstrip("/").casefold() for w in re.split(r"[\s,]+", text.strip()) if w.strip("/")]
    if not words or set(words) <= NONE_WORDS:
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


def ask_in_terminal(ask: Callable[[str], str], cap_usd: float) -> Callable[[list[Ipo]], list[Ipo]]:
    """A run_daily `select` that lists the IPOs ready for analysis and asks which to analyse."""
    def select(ipos: list[Ipo]) -> list[Ipo]:
        print(f"Open IPOs ready for analysis (up to ${cap_usd:.2f} each):")
        print("\n".join(f"  {line}" for line in numbered(ipos)))
        ids = [i.ipo_id for i in ipos]
        while True:
            picked, unknown = resolve(ask("Analyse which? (e.g. 1,3 / all / none) [none]: "), ids, _keys(ipos))
            if not unknown:
                return [i for i in ipos if i.ipo_id in picked]
            print(f"Not in the list: {', '.join(unknown)}")
    return select


def offer_message(ipos: Sequence[Ipo], cap_usd: float) -> str:
    return "\n".join([
        f"🆕 IPOs open and ready for analysis (up to ${cap_usd:.2f} each). "
        "Reply with numbers or symbols to analyse, e.g. \"1,3\":",
        *numbered(ipos),
        "",
        f"Reply \"all\" for every one, or \"none\" to skip. {UPDATES_CONTINUE}",
    ])


def send_offer(today: date, ipos: Sequence[Ipo], adapters: Adapters, config: Config) -> None:
    """Saves the offer first, so a reply of "2" means what this message showed as 2."""
    store = Store(config.db_path)
    try:
        store.set_state(OFFER_KEY, json.dumps({"date": today.isoformat(), "ids": [i.ipo_id for i in ipos]}))
    finally:
        store.close()
    adapters.notifier.send_message(offer_message(ipos, config.budget.cap_usd))


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
        texts = []
        for u in updates:
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
                  run: Callable = run_daily) -> list[str]:
    """Reads new replies and analyses the IPOs picked; returns their ipo_ids. Answers every reply."""
    texts, offer_ids = _read_replies(updates_source, chat_id, config)
    if not texts:
        return []
    todo = plan(today, adapters, config)
    ready = {i.ipo_id: i for i in todo.analyse}
    done = {i.ipo_id for i, _ in todo.skip}
    held = {i.ipo_id for i in todo.hold}
    keys = _keys([*todo.analyse, *(i for i, _ in todo.skip), *todo.hold])
    picked, unknown = [], []
    for text in texts:
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
    if unknown:
        lines.append(f"Didn't recognise: {', '.join(unknown)}.")
        if todo.analyse:
            lines += ["Ready for analysis:", *numbered(todo.analyse)]
        else:
            lines.append("No IPO is waiting for analysis today.")
    if chosen:
        names = ", ".join(f"{_label(i)} ({i.name})" for i in chosen)
        lines.append(f"▶️ Starting analysis of {names}, up to ${config.budget.cap_usd:.2f} each. "
                     "The report arrives here when it is done.")
    elif not lines:
        lines.append(f"OK, nothing will be analysed. {UPDATES_CONTINUE}")
    adapters.notifier.send_message("\n".join(lines))
    if not chosen:
        return []
    ids = {i.ipo_id for i in chosen}
    log.info("telegram picks: analysing %s", ", ".join(sorted(ids)))
    summary = run(today, adapters, config, daily_updates=False, select=lambda ipos: [i for i in ipos if i.ipo_id in ids])
    log.info("telegram picks finished: %s", summary.model_dump())
    return sorted(ids)
