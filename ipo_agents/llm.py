"""The LLM provider boundary: one structured call in, parsed object + token usage out."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ipo_agents.config import ModelConfig


@dataclass
class LLMResult:
    parsed: Any
    input_tokens: int
    cached_tokens: int
    output_tokens: int


class LLM(Protocol):
    def invoke_structured(self, *, model: str, system: str, user: str, schema: type, max_tokens: int) -> LLMResult: ...


class LangChainLLM:
    """Real provider: OpenAI or Groq via LangChain, temperature 0, structured output."""

    def __init__(self, models: dict[str, ModelConfig]):
        self._models = models

    def _chat(self, model: str, max_tokens: int):
        cfg = self._models[model]
        if cfg.provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=cfg.id, temperature=0, max_tokens=max_tokens)
        if cfg.provider == "groq":
            from langchain_groq import ChatGroq

            return ChatGroq(model=cfg.id, temperature=0, max_tokens=max_tokens)
        raise ValueError(f"unknown provider {cfg.provider!r}")

    def invoke_structured(self, *, model, system, user, schema, max_tokens):
        chain = self._chat(model, max_tokens).with_structured_output(schema, include_raw=True)
        out = chain.invoke([("system", system), ("user", user)])
        if out["parsing_error"]:
            raise out["parsing_error"]
        usage = out["raw"].usage_metadata or {}
        return LLMResult(
            parsed=out["parsed"],
            input_tokens=usage.get("input_tokens", 0),
            cached_tokens=(usage.get("input_token_details") or {}).get("cache_read", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
