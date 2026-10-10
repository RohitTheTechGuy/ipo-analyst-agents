"""The LLM provider boundary: one structured call in, parsed object + token usage out."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ipo_agents.config import ModelConfig


# A hung request must not stall a graph branch; the meter charges a timed-out call its worst case.
REQUEST_TIMEOUT_S = 120


@dataclass
class LLMResult:
    parsed: Any
    input_tokens: int
    cached_tokens: int
    output_tokens: int


class LLMParseError(Exception):
    """The provider replied (and billed) but the reply didn't match the schema; carries the usage."""

    def __init__(self, message: str, *, input_tokens: int, cached_tokens: int, output_tokens: int):
        super().__init__(message)
        self.input_tokens = input_tokens
        self.cached_tokens = cached_tokens
        self.output_tokens = output_tokens


class LLM(Protocol):
    def invoke_structured(
        self, *, model: str, system: str, user: str, schema: type, max_tokens: int, task: str = ""
    ) -> LLMResult: ...


class LangChainLLM:
    """Real provider: OpenAI or Groq via LangChain, temperature 0, structured output."""

    def __init__(self, models: dict[str, ModelConfig]):
        self._models = models

    def _chat(self, model: str, max_tokens: int):
        cfg = self._models[model]
        if cfg.provider == "openai":
            from langchain_openai import ChatOpenAI

            # No hidden client retries: each retry could be billed without the budget meter seeing it.
            return ChatOpenAI(model=cfg.id, temperature=0, max_tokens=max_tokens,
                              max_retries=0, timeout=REQUEST_TIMEOUT_S)
        if cfg.provider == "groq":
            from langchain_groq import ChatGroq

            # Also no client retries: a Groq 429 must fall back to Luna at once, never wait.
            return ChatGroq(model=cfg.id, temperature=0, max_tokens=max_tokens,
                            max_retries=0, timeout=REQUEST_TIMEOUT_S)
        raise ValueError(f"unknown provider {cfg.provider!r}")

    def invoke_structured(self, *, model, system, user, schema, max_tokens, task=""):
        chain = self._chat(model, max_tokens).with_structured_output(schema, include_raw=True)
        out = chain.invoke([("system", system), ("user", user)])
        usage = getattr(out["raw"], "usage_metadata", None)
        if not usage:
            # Can't cost the call; failing makes the meter charge the worst case instead of $0.
            raise RuntimeError(f"{model}: provider response carried no token usage")
        tokens = dict(
            input_tokens=usage.get("input_tokens", 0),
            cached_tokens=(usage.get("input_token_details") or {}).get("cache_read", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
        if out["parsing_error"]:
            # Read usage first: a failed parse was still billed and must be metered.
            raise LLMParseError(str(out["parsing_error"]), **tokens) from out["parsing_error"]
        return LLMResult(parsed=out["parsed"], **tokens)
