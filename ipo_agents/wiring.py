"""Builds the real adapters from the environment (secrets come from .env, loaded by the CLI)."""
from __future__ import annotations

import logging
import os

from ipo_agents.adapters import Adapters
from ipo_agents.config import Config
from ipo_agents.discovery import HttpRhpDownloader, default_exchange, make_client
from ipo_agents.llm import LangChainLLM
from ipo_agents.market import HttpMarketData
from ipo_agents.notify.telegram import telegram_notifier_from_env
from ipo_agents.peers import HttpPeerData
from ipo_agents.search import TavilySearch

log = logging.getLogger(__name__)

REQUIRED_SECRETS = ("OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


class MissingSecrets(RuntimeError):
    """Names the missing variables only; never their values."""


class _Disabled:
    """Stands in for the model and Telegram in a dry run: any use is a bug, so it fails loudly."""

    def __init__(self, what: str):
        self._what = what

    def __getattr__(self, name):
        raise RuntimeError(f"dry run: {self._what}.{name} must not be called")


def build_adapters(config: Config, *, discovery_only: bool = False) -> Adapters:
    """The real adapter set. discovery_only (dry run) needs no secrets: model and Telegram are disabled."""
    client = make_client()
    exchange, downloader = default_exchange(client), HttpRhpDownloader(client)
    if discovery_only:
        return Adapters(exchange=exchange, downloader=downloader,
                        llm=_Disabled("llm"), notifier=_Disabled("notifier"))
    missing = [k for k in REQUIRED_SECRETS if not os.environ.get(k)]
    if missing:
        raise MissingSecrets(f"missing required secrets in .env or the environment: {', '.join(missing)}")
    if not os.environ.get("GROQ_API_KEY"):
        log.warning("GROQ_API_KEY not set: Groq-routed tasks will fail over to Luna (small extra cost)")
    search = TavilySearch() if os.environ.get("TAVILY_API_KEY") else None
    if search is None:
        log.warning("TAVILY_API_KEY not set: sentiment will be reported as unavailable")
    return Adapters(exchange=exchange, downloader=downloader, llm=LangChainLLM(config.models),
                    notifier=telegram_notifier_from_env(), search=search, market=HttpMarketData(client),
                    peers=HttpPeerData(client, overrides=config.peers.symbol_overrides))
