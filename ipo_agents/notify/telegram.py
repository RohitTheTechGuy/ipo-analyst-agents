"""Telegram Bot API notifier (spec stories 46-48). Plain text only: no parse mode."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

API_BASE = "https://api.telegram.org"

# httpx logs every request URL at INFO, and Bot API URLs contain the token.
logging.getLogger("httpx").setLevel(logging.WARNING)


class TelegramError(RuntimeError):
    pass


MAX_MESSAGE_CHARS = 4096  # Telegram's sendMessage limit


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None):
        self._token = token
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=60)

    def send_message(self, text: str) -> None:
        """Sends `text`, as several messages in order if it is over Telegram's limit, split between lines."""
        for part in split_message(text):
            self._call("sendMessage", json={"chat_id": self._chat_id, "text": part})

    def send_document(self, path: Path, caption: str = "") -> None:
        path = Path(path)
        with path.open("rb") as f:
            self._call("sendDocument", data={"chat_id": self._chat_id, "caption": caption},
                       files={"document": (path.name, f, "application/pdf")})

    def send_typing(self) -> None:
        """Shows "typing…" in the chat for a few seconds, before a slow reply."""
        self._call("sendChatAction", json={"chat_id": self._chat_id, "action": "typing"})

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        """Updates (incoming messages) after `offset`, oldest first; Telegram keeps them for 24 hours.
        `timeout` > 0 long-polls: waits up to that many seconds for a message (under the client's 60s)."""
        params = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", json=params) or []

    def _call(self, method: str, **kwargs):
        # Errors are re-raised without httpx's message, which would include the URL and so the token.
        try:
            resp = self._client.post(f"{API_BASE}/bot{self._token}/{method}", **kwargs)
            body = resp.json()
        except httpx.HTTPError as e:
            raise TelegramError(f"{method} failed: {type(e).__name__}") from None
        except ValueError:
            raise TelegramError(f"{method} failed: HTTP {resp.status_code}, non-JSON response") from None
        if not isinstance(body, dict) or not body.get("ok"):
            body = body if isinstance(body, dict) else {}
            raise TelegramError(f"{method} failed: HTTP {resp.status_code}: {body.get('description', 'no description')}")
        return body.get("result")


def telegram_notifier_from_env(client: httpx.Client | None = None) -> TelegramNotifier:
    """Builds the notifier from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (loaded from .env by run_daily.py)."""
    missing = [k for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing env vars: {', '.join(missing)}")
    return TelegramNotifier(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], client=client)


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Parts of at most `limit` characters, split between lines; a single over-long line is split inside it.
    Joining the parts with newlines gives back the text."""
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a line on its own over the limit: hard-split it
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if current and len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current or not parts:
        parts.append(current)
    return parts
