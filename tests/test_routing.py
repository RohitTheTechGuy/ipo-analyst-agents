"""Model routing at seam B (spec: Budget rules; plan 6.3). Prices from config.yaml, USD per 1M tokens:
terra in 2.00 / cached 0.20 / out 12.00; luna 0.20 / 0.02 / 1.20; groq 0 / 0 / 0.
`offer_objects` routes to terra. Cap $1.50, degrade at 70% = $1.05."""
import fitz
import pytest

from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded
from ipo_agents.config import RunEstimate
from ipo_agents.contracts import Fact, OfferObjects
from tests.fakes import ProviderError, ScriptedLLM, make_ipo

OFFER = OfferObjects(issue_size_cr=Fact(
    value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))


def offer_llm(config, **kw):
    return ScriptedLLM({OfferObjects: OFFER}, models=config.models, **kw)


@pytest.mark.parametrize("input_tokens, second_model", [
    # 525,000 in x $2/1M = $1.05 spent = exactly 70% of $1.50: degraded, terra -> luna.
    (525_000, "luna"),
    # 524,000 in x $2/1M = $1.048 spent, just under 70%: still terra.
    (524_000, "terra"),
])
def test_spend_at_70_percent_routes_terra_tasks_to_luna(tiny_rhp, adapters, config, input_tokens, second_model):
    adapters.llm = offer_llm(config, usage=[(input_tokens, 0, 0)])

    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)
    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == ["terra", second_model]


def rhp_with_pages(tmp_path, n):
    path = tmp_path / f"rhp_{n}.pdf"
    doc = fitz.open()
    for i in range(n):
        doc.new_page().insert_text((72, 72), "THE OFFER\nTotal issue size of up to Rs. 1,250.00 crore" if i == 1 else "")
    doc.save(path)
    return path


@pytest.mark.parametrize("pages, model", [
    # Pre-run estimate (config.yaml budget.estimate): per page 1,300 in x $0.20/1M + 150 out x $1.20/1M
    # = $0.00044 on luna, plus a fixed $0.47 for the Terra and other Luna steps.
    # 1,400 pages: $0.616 + $0.47 = $1.086 > $1.05 (70% of $1.50): starts degraded.
    (1_400, "luna"),
    # 1,300 pages: $0.572 + $0.47 = $1.042 <= $1.05: normal routing.
    (1_300, "terra"),
])
def test_large_rhp_starts_in_degraded_mode(tmp_path, adapters, config, pages, model):
    adapters.llm = offer_llm(config)

    analyse_rhp(make_ipo(), rhp_with_pages(tmp_path, pages), adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == [model]


def on_groq(config, **update):
    return config.model_copy(update={"routing": {**config.routing, "offer_objects": "groq"}, **update})


@pytest.mark.parametrize("max_tokens, model", [
    # 4,000 out + a prompt (short system rules, 3 tiny pages, schema, framing) well under 2,000
    # tokens stays within groq_limits.max_call_tokens = 6,000.
    (4_000, "groq"),
    # 6,000 out alone fills the limit; any prompt pushes it over: luna.
    (6_000, "luna"),
])
def test_groq_task_goes_to_luna_when_the_call_is_too_big(tiny_rhp, adapters, config, max_tokens, model):
    config = on_groq(config, default_max_tokens=max_tokens)
    adapters.llm = offer_llm(config)

    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == [model]


@pytest.mark.parametrize("output_tokens, second_model", [
    # Daily Groq cap 200,000 tokens x 80% = 160,000. 159,000 in + 999 out = 159,999 used today: still groq.
    (999, "groq"),
    # 159,000 + 1,000 = 160,000 used: at 80%, so luna.
    (1_000, "luna"),
])
def test_groq_task_goes_to_luna_past_80_percent_of_the_daily_groq_cap(
        tiny_rhp, adapters, config, output_tokens, second_model):
    config = on_groq(config)
    adapters.llm = offer_llm(config, usage=[(159_000, 0, output_tokens)])
    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    # The Groq cap is per day across all IPOs, not per IPO.
    beta = make_ipo().model_copy(update={"ipo_id": "BETA-2026-10-13"})
    analyse_rhp(beta, tiny_rhp, adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == ["groq", second_model]


@pytest.fixture
def sleeps(monkeypatch):
    """Records waits instead of waiting."""
    waited = []
    monkeypatch.setattr("time.sleep", waited.append)
    return waited


@pytest.mark.parametrize("error", [ProviderError(429), ProviderError(503), TimeoutError("read timed out")])
def test_groq_failure_falls_back_to_luna_without_waiting(tiny_rhp, adapters, config, sleeps, error):
    config = on_groq(config)
    adapters.llm = offer_llm(config, errors=[error])

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.offer.issue_size_cr.value == 1250.0
    assert [c["model"] for c in adapters.llm.calls] == ["groq", "luna"]
    assert sleeps == []


def test_luna_fallback_after_groq_failure_is_still_metered(tiny_rhp, adapters, config, sleeps):
    # Cap $0.001: the groq call reserves $0 and goes out; luna's worst case is at least
    # 4,000 out x $1.20/1M = $0.0048 > $0.001, so the fallback is stopped before it is sent.
    config = on_groq(config, budget=config.budget.model_copy(update={"cap_usd": 0.001}))
    adapters.llm = offer_llm(config, errors=[ProviderError(429)])

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == ["groq"]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_openai_error_is_retried_with_backoff(tiny_rhp, adapters, config, sleeps, status):
    adapters.llm = offer_llm(config, errors=[ProviderError(status), ProviderError(status)])

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.offer.issue_size_cr.value == 1250.0
    assert [c["model"] for c in adapters.llm.calls] == ["terra"] * 3
    assert sleeps == [0.5, 1.0]


def test_transient_openai_error_gives_up_after_two_retries(tiny_rhp, adapters, config, sleeps):
    adapters.llm = offer_llm(config, errors=[ProviderError(429)] * 3)

    with pytest.raises(ProviderError):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert len(adapters.llm.calls) == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize("error", [ProviderError(400), TimeoutError("read timed out")])
def test_non_transient_openai_error_is_not_retried(tiny_rhp, adapters, config, sleeps, error):
    adapters.llm = offer_llm(config, errors=[error])

    with pytest.raises(type(error)):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert len(adapters.llm.calls) == 1
    assert sleeps == []


def test_each_retry_is_metered(tiny_rhp, adapters, config, sleeps):
    # One terra attempt's worst case: 4,000 out x $12/1M = $0.048 plus a small prompt, ~$0.0485.
    # Cap $0.09 fits one attempt but not two: the failed attempt is charged its worst case
    # (usage unknown), so the retry is stopped before it is sent.
    no_estimate = RunEstimate(tokens_per_page=0, output_tokens_per_page=0, fixed_usd=0)
    config = config.model_copy(update={"budget": config.budget.model_copy(
        update={"cap_usd": 0.09, "estimate": no_estimate})})
    adapters.llm = offer_llm(config, errors=[ProviderError(429)])

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == ["terra"]
