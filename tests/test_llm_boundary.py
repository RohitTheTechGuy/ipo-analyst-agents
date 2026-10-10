"""Chat models are built only in ipo_agents/llm.py, so every call passes the budget meter."""
import re

import pytest

from ipo_agents.config import load_config
from ipo_agents.llm import LangChainLLM
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
