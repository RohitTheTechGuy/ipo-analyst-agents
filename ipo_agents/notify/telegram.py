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


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None):
        self._token = token
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=60)

    def send_message(self, text: str) -> None:
        self._call("sendMessage", json={"chat_id": self._chat_id, "text": text})

    def send_document(self, path: Path, caption: str = "") -> None:
        path = Path(path)
        with path.open("rb") as f:
            self._call("sendDocument", data={"chat_id": self._chat_id, "caption": caption},
                       files={"document": (path.name, f, "application/pdf")})

    def _call(self, method: str, **kwargs) -> None:
        # Errors are re-raised without httpx's message, which would include the URL and so the token.
        try:
            resp = self._client.post(f"{API_BASE}/bot{self._token}/{method}", **kwargs)
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise TelegramError(f"{method} failed: {type(e).__name__}") from None
        if not body.get("ok"):
            raise TelegramError(f"{method} failed: HTTP {resp.status_code}: {body.get('description', 'no description')}")


def telegram_notifier_from_env(client: httpx.Client | None = None) -> TelegramNotifier:
    """Builds the notifier from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (loaded from .env by run_daily.py)."""
    missing = [k for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing env vars: {', '.join(missing)}")
    return TelegramNotifier(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], client=client)
