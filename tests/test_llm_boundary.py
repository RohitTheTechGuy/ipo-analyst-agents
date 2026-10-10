"""Chat models are built only in ipo_agents/llm.py, so every call passes the budget meter."""
import re

from tests.conftest import REPO_ROOT

CHAT_MODEL = re.compile(r"\bChat(OpenAI|Groq)\b")
PACKAGE = REPO_ROOT / "ipo_agents"


def test_no_module_but_llm_py_references_a_chat_model():
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in PACKAGE.rglob("*.py")
        if p.name != "llm.py" and CHAT_MODEL.search(p.read_text())
    ]

    assert CHAT_MODEL.search((PACKAGE / "llm.py").read_text()), "scan is not seeing the package"
    assert offenders == []
