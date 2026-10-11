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
    """The provider replied (and billed) but the reply didn't match the schema; carries the usage.

    Token counts are None when the usage was lost with the reply (the SDK raised while parsing it):
    the meter then charges the call's worst case, so the cap still holds."""

    def __init__(self, message: str, *, input_tokens: int | None, cached_tokens: int | None,
                 output_tokens: int | None):
        super().__init__(message)
        self.input_tokens = input_tokens
        self.cached_tokens = cached_tokens
        self.output_tokens = output_tokens


UNKNOWN_USAGE = dict(input_tokens=None, cached_tokens=None, output_tokens=None)


class LLM(Protocol):
    def invoke_structured(
        self, *, model: str, system: str, user: str, schema: type, max_tokens: int, task: str = ""
    ) -> LLMResult: ...


class LangChainLLM:
    """Real provider: OpenAI or Groq via LangChain, temperature 0, structured output.

    `http_client` (an httpx.Client) replaces the provider SDK's own transport; tests pass one built on
    httpx.MockTransport to drive this class against real-shaped replies."""

    def __init__(self, models: dict[str, ModelConfig], *, http_client=None):
        self._models = models
        self._http_client = http_client

    def _chat(self, model: str, max_tokens: int):
        cfg = self._models[model]
        extra = {}
        if self._http_client is not None:
            extra["http_client"] = self._http_client
        if cfg.reasoning_effort:
            # Reasoning tokens are billed as output and count against max_tokens; sent only where configured.
            # Reasoning models take reasoning_effort instead of temperature: gpt-6.1-sol rejects temperature=0
            # (langchain only drops it by name for gpt-5*), so no temperature is sent with it.
            extra["reasoning_effort"] = cfg.reasoning_effort
        else:
            extra["temperature"] = 0
        if cfg.provider == "openai":
            from langchain_openai import ChatOpenAI

            # No hidden client retries: each retry could be billed without the budget meter seeing it.
            return ChatOpenAI(model=cfg.id, max_tokens=max_tokens,
                              max_retries=0, timeout=REQUEST_TIMEOUT_S, **extra)
        if cfg.provider == "groq":
            from langchain_groq import ChatGroq

            # Also no client retries: a Groq 429 must fall back to Luna at once, never wait.
            return ChatGroq(model=cfg.id, max_tokens=max_tokens,
                            max_retries=0, timeout=REQUEST_TIMEOUT_S, **extra)
        raise ValueError(f"unknown provider {cfg.provider!r}")

    def invoke_structured(self, *, model, system, user, schema, max_tokens, task=""):
        import openai
        from langchain_core.exceptions import OutputParserException
        from langchain_openai.chat_models.base import OpenAIRefusalError
        from pydantic import ValidationError

        chain = self._chat(model, max_tokens).with_structured_output(schema, include_raw=True)
        try:
            out = chain.invoke([("system", system), ("user", user)])
        except (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError) as e:
            # Raised by the OpenAI SDK's .parse() on finish_reason length / content_filter; the completion
            # it carries has the real usage.
            raise LLMParseError(f"{model}: {e}", **_completion_usage(e.completion)) from e
        except (ValidationError, OutputParserException, OpenAIRefusalError) as e:
            # The SDK parsed the reply against the schema and failed; the reply and its usage are gone.
            raise LLMParseError(f"{model}: reply did not match {schema.__name__}: {e}", **UNKNOWN_USAGE) from e
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
        if out["parsed"] is None:
            # Groq (tool calling) answering in prose with no tool call: nothing to parse, but billed.
            raise LLMParseError(f"{model}: reply carried no {schema.__name__}", **tokens)
        return LLMResult(parsed=out["parsed"], **tokens)


def _completion_usage(completion) -> dict:
    """Token usage from an OpenAI ChatCompletion; unknown if it carries none."""
    usage = getattr(completion, "usage", None)
    if usage is None:
        return dict(UNKNOWN_USAGE)
    details = getattr(usage, "prompt_tokens_details", None)
    return dict(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
                cached_tokens=(getattr(details, "cached_tokens", None) or 0))
