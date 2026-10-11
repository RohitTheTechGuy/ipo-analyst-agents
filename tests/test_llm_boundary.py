"""Chat models are built only in ipo_agents/llm.py, so every call passes the budget meter.
The real LangChainLLM is driven against real-shaped provider replies (httpx.MockTransport): a reply the
schema can't take must become LLMParseError, never an unhandled provider exception."""
import json
import re

import httpx
import pytest
from pydantic import BaseModel

from ipo_agents.config import load_config
from ipo_agents.contracts import Fact
from ipo_agents.llm import LangChainLLM, LLMParseError
from tests.conftest import REPO_ROOT

CHAT_MODEL = re.compile(r"\bChat(OpenAI|Groq)\b|\binit_chat_model\b|^\s*(from|import)\s+(openai|groq|langchain_openai|langchain_groq)\b", re.M)
PACKAGE = REPO_ROOT / "ipo_agents"


def test_no_module_but_llm_py_references_a_chat_model():
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in PACKAGE.rglob("*.py")
        if p.name != "llm.py" and CHAT_MODEL.search(p.read_text())
    ]

    assert CHAT_MODEL.search((PACKAGE / "llm.py").read_text()), "scan is not seeing the package"
    assert offenders == []


@pytest.mark.parametrize("model", ["luna", "terra", "groq"])
def test_chat_clients_never_retry_behind_the_meter_and_time_out(monkeypatch, model):
    # Client retries would be billed without the meter seeing them, and would make a Groq 429 wait
    # instead of falling back to Luna at once; a hung call must not stall a graph branch forever.
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("GROQ_API_KEY", "test")
    chat = LangChainLLM(load_config(REPO_ROOT / "config.yaml").models)._chat(model, 100)

    assert chat.max_retries == 0
    assert chat.request_timeout == 120


# --- the real LangChainLLM against real-shaped provider replies ------------------------------------

USAGE = {"prompt_tokens": 1000, "completion_tokens": 300, "total_tokens": 1300,
         "prompt_tokens_details": {"cached_tokens": 100}}
NOT_DISCLOSED_JSON = {"value": None, "unit": "", "page": None, "verbatim_quote": None,
                      "not_disclosed_reason": "not disclosed in provided pages"}
# Valid JSON that doesn't fit the schema: a value that isn't a number. (A value with no page is valid
# JSON for Fact by design; the verifier, not the parser, turns it into "could not verify".)
BAD_VALUE_JSON = {"value": "five crore", "unit": "₹ crore", "page": 3, "verbatim_quote": "Rs. 5 crore",
                  "not_disclosed_reason": None}


class Issue(BaseModel):
    issue_size: Fact


def completion(message: dict, finish_reason: str = "stop", model: str = "gpt-5.6-luna") -> dict:
    """A chat.completions response body as the provider sends it."""
    return {"id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", **message},
                         "finish_reason": finish_reason, "logprobs": None}],
            "usage": USAGE}


def groq_tool_call(arguments: str) -> dict:
    return completion({"content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "Issue", "arguments": arguments}}]},
        finish_reason="tool_calls", model="openai/gpt-oss-120b")


def real_llm(monkeypatch, body: dict) -> tuple[LangChainLLM, list[dict]]:
    """The real LangChainLLM, its HTTP calls answered with `body`; the request bodies sent are recorded."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("GROQ_API_KEY", "test")
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LangChainLLM(load_config(REPO_ROOT / "config.yaml").models, http_client=client), sent


def call(llm, model="luna", max_tokens=4000):
    return llm.invoke_structured(model=model, system="Extract.", user="[page 1]\nNothing.", schema=Issue,
                                 max_tokens=max_tokens, task="offer_objects")


def test_a_valid_openai_reply_is_parsed_with_its_usage(monkeypatch):
    llm, sent = real_llm(monkeypatch, completion({"content": json.dumps({"issue_size": NOT_DISCLOSED_JSON})}))

    result = call(llm)

    assert result.parsed.issue_size.value is None
    assert (result.input_tokens, result.cached_tokens, result.output_tokens) == (1000, 100, 300)
    assert sent[0]["response_format"]["type"] == "json_schema"  # strict structured output, as live


@pytest.mark.parametrize("content", ["{not json", json.dumps({"issue_size": BAD_VALUE_JSON})],
                         ids=["invalid-json", "schema-invalid"])
def test_an_openai_reply_that_does_not_match_the_schema_is_a_parse_error(monkeypatch, content):
    # The SDK's .parse() raises pydantic's ValidationError inside chain.invoke and the response (with its
    # usage) is lost, so the error carries unknown usage and the meter charges the call's worst case.
    llm, _ = real_llm(monkeypatch, completion({"content": content}))

    with pytest.raises(LLMParseError) as err:
        call(llm)

    assert (err.value.input_tokens, err.value.cached_tokens, err.value.output_tokens) == (None, None, None)


def test_an_openai_reply_cut_off_at_max_tokens_is_a_parse_error_with_its_real_usage(monkeypatch):
    llm, _ = real_llm(monkeypatch, completion({"content": '{"issue_size": {"val'}, finish_reason="length"))

    with pytest.raises(LLMParseError) as err:
        call(llm)

    assert (err.value.input_tokens, err.value.cached_tokens, err.value.output_tokens) == (1000, 100, 300)


def test_an_openai_refusal_is_a_parse_error_with_its_usage(monkeypatch):
    llm, _ = real_llm(monkeypatch, completion({"content": None, "refusal": "I can't help with that."}))

    with pytest.raises(LLMParseError) as err:
        call(llm)

    assert (err.value.input_tokens, err.value.cached_tokens, err.value.output_tokens) == (1000, 100, 300)


@pytest.mark.parametrize("body", [
    groq_tool_call(json.dumps({"issue_size": BAD_VALUE_JSON})),
    groq_tool_call("{not json"),
    # No tool call at all: LangChain returns parsed=None and no parsing error.
    completion({"content": "Here is the data you asked for."}, model="openai/gpt-oss-120b"),
], ids=["schema-invalid", "invalid-json", "no-tool-call"])
def test_a_groq_reply_that_does_not_match_the_schema_is_a_parse_error_with_its_usage(monkeypatch, body):
    llm, _ = real_llm(monkeypatch, body)

    with pytest.raises(LLMParseError) as err:
        call(llm, model="groq", max_tokens=1500)

    assert (err.value.input_tokens, err.value.cached_tokens, err.value.output_tokens) == (1000, 100, 300)


def test_a_valid_groq_reply_is_parsed(monkeypatch):
    llm, _ = real_llm(monkeypatch, groq_tool_call(json.dumps({"issue_size": NOT_DISCLOSED_JSON})))

    assert call(llm, model="groq", max_tokens=1500).parsed.issue_size.value is None


@pytest.mark.parametrize("model, effort", [("luna", "low"), ("terra", "low"), ("groq", None)])
def test_reasoning_effort_is_sent_only_where_configured(monkeypatch, model, effort):
    body = (groq_tool_call(json.dumps({"issue_size": NOT_DISCLOSED_JSON})) if model == "groq"
            else completion({"content": json.dumps({"issue_size": NOT_DISCLOSED_JSON})}))
    llm, sent = real_llm(monkeypatch, body)

    call(llm, model=model, max_tokens=1500)

    assert sent[0].get("reasoning_effort") == effort
