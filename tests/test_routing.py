"""Model routing at seam B (spec: Budget rules; plan 6.3). Prices from config.yaml, USD per 1M tokens:
terra in 2.00 / cached 0.20 / out 12.00; luna 0.20 / 0.02 / 1.20; groq 0 / 0 / 0.
`offer_objects` routes to terra. Cap $1.50, degrade at 70% = $1.05."""
import sqlite3
from contextlib import closing
from dataclasses import dataclass

import pymupdf as fitz
import httpx
import openai
import pytest

from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded
from ipo_agents.config import RunEstimate
from ipo_agents.contracts import Fact, OfferObjects
from tests.fakes import ProviderError, ScriptedLLM, make_ipo, offer_objects

OFFER = offer_objects(issue_size=Fact(
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


def test_spend_summing_to_exactly_70_percent_degrades_despite_float_rounding(tiny_rhp, adapters, config):
    # 350,000 in x $2/1M = $0.70, then 175,000 in = $0.35: $1.05 spent, exactly 70% of $1.50.
    # (In floating point 0.70 + 0.35 = 1.0499999999999998, a hair under 1.05.) Third call: luna.
    adapters.llm = offer_llm(config, usage=[(350_000, 0, 0), (175_000, 0, 0)])

    for _ in range(3):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == ["terra", "terra", "luna"]


def rhp_with_pages(tmp_path, tiny_rhp, n):
    """tiny_rhp's 7 sections padded to n pages with short text pages (blank pages would be OCR'd)."""
    path = tmp_path / f"rhp_{n}.pdf"
    doc = fitz.open(tiny_rhp)
    for _ in range(n - doc.page_count):
        doc.new_page().insert_text((72, 72), "Continued disclosures.")
    doc.save(path)
    return path


@pytest.mark.parametrize("pages, model", [
    # Pre-run estimate (config.yaml budget.estimate): per page 1,600 in x $0.20/1M + 350 out x $1.20/1M
    # = $0.00032 + $0.00042 = $0.00074 on luna, plus a fixed $0.55 for the Terra and other Luna steps.
    # 700 pages: $0.518 + $0.55 = $1.068 > $1.05 (70% of $1.50): starts degraded.
    (700, "luna"),
    # 650 pages: $0.481 + $0.55 = $1.031 <= $1.05: normal routing.
    (650, "terra"),
])
def test_large_rhp_starts_in_degraded_mode(tmp_path, tiny_rhp, adapters, config, pages, model):
    adapters.llm = offer_llm(config)

    analyse_rhp(make_ipo(), rhp_with_pages(tmp_path, tiny_rhp, pages), adapters, config)

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

    assert report.offer.issue_size.value == 1250.0
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

    assert report.offer.issue_size.value == 1250.0
    assert [c["model"] for c in adapters.llm.calls] == ["terra"] * 3
    assert sleeps == [0.5, 1.0]


def test_transient_openai_error_gives_up_after_five_attempts(tiny_rhp, adapters, config, sleeps):
    adapters.llm = offer_llm(config, errors=[ProviderError(429)] * 5)

    with pytest.raises(ProviderError):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert len(adapters.llm.calls) == 5
    assert sleeps == [0.5, 1.0, 2.0, 4.0]


@pytest.mark.parametrize("error", [ProviderError(400), TimeoutError("read timed out")])
def test_non_transient_openai_error_is_not_retried(tiny_rhp, adapters, config, sleeps, error):
    adapters.llm = offer_llm(config, errors=[error])

    with pytest.raises(type(error)):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert len(adapters.llm.calls) == 1
    assert sleeps == []


def test_budget_stop_on_a_groq_task_is_not_treated_as_a_groq_failure(tiny_rhp, adapters, config, caplog):
    # Run 1 on terra bills 800,000 in x $2/1M = $1.60, over the $1.50 cap (actual usage can exceed
    # the reservation). Run 2 is on groq: even its $0 worst case doesn't fit, so it stops there,
    # with no fallback to luna.
    adapters.llm = offer_llm(config, usage=[(800_000, 0, 0)])
    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, on_groq(config))

    assert [c["model"] for c in adapters.llm.calls] == ["terra"]
    assert "falling back" not in caplog.text


def test_missing_max_tokens_on_a_groq_task_is_not_treated_as_a_groq_failure(tiny_rhp, adapters, config, caplog):
    adapters.llm = offer_llm(config)

    with pytest.raises(ValueError, match="max_tokens"):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, on_groq(config, default_max_tokens=0))

    assert adapters.llm.calls == []
    assert "falling back" not in caplog.text


@dataclass
class LogLostProvider(ScriptedLLM):
    """While the call is out, the call log becomes unwritable (here: its table is gone)."""

    db_path: str = ""

    def invoke_structured(self, **kwargs):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("DROP TABLE IF EXISTS llm_calls")
        return super().invoke_structured(**kwargs)


def test_spend_still_counts_when_the_call_log_cannot_be_written(tiny_rhp, adapters, config, sleeps):
    # The 503'd attempt is charged W in memory even though logging it fails, so the retry
    # (needing 2W > $0.09) is stopped; the log failure does not replace the budget stop.
    config = tiny_cap(config, 0.09)
    adapters.llm = LogLostProvider({OfferObjects: OFFER}, models=config.models,
                                   errors=[ProviderError(503)] * 3, db_path=config.db_path)

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert len(adapters.llm.calls) == 1


def test_a_call_log_failure_does_not_mask_the_provider_error(tiny_rhp, adapters, config):
    adapters.llm = LogLostProvider({OfferObjects: OFFER}, models=config.models,
                                   errors=[TimeoutError("read timed out")], db_path=config.db_path)

    with pytest.raises(TimeoutError):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)


def openai_429(code, headers=None):
    """A real openai.RateLimitError, shaped as the client builds it (body = the response's `error` object)."""
    response = httpx.Response(429, headers=headers or {},
                              request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    return openai.RateLimitError("rate limited", response=response, body={"code": code, "message": "rate limited"})


@pytest.mark.parametrize("headers, wait", [
    ({"retry-after-ms": "1500"}, 1.5),
    ({"retry-after": "7"}, 7.0),
    ({"retry-after-ms": "250", "retry-after": "7"}, 0.25),  # the finer one wins
    ({"retry-after": "120"}, 30.0),  # never waits more than 30s
    ({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, 0.5),  # a date is not read: normal backoff
])
def test_a_rate_limit_waits_as_long_as_the_provider_asks(tiny_rhp, adapters, config, sleeps, headers, wait):
    adapters.llm = offer_llm(config, errors=[openai_429("rate_limit_exceeded", headers)])

    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert sleeps == [wait]


def test_real_openai_rate_limit_is_retried(tiny_rhp, adapters, config, sleeps):
    adapters.llm = offer_llm(config, errors=[openai_429("rate_limit_exceeded")])

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.offer.issue_size.value == 1250.0
    assert [c["model"] for c in adapters.llm.calls] == ["terra", "terra"]
    assert sleeps == [0.5]


def test_out_of_quota_429_is_never_retried(tiny_rhp, adapters, config, sleeps):
    # insufficient_quota means the account is out of credit: retrying cannot succeed.
    adapters.llm = offer_llm(config, errors=[openai_429("insufficient_quota")])

    with pytest.raises(openai.RateLimitError):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert len(adapters.llm.calls) == 1
    assert sleeps == []


def tiny_cap(config, cap_usd):
    """A tiny cap, with the pre-run estimate zeroed so the run is not degraded to luna from the start."""
    no_estimate = RunEstimate(tokens_per_page=0, output_tokens_per_page=0, fixed_usd=0)
    return config.model_copy(update={"budget": config.budget.model_copy(
        update={"cap_usd": cap_usd, "estimate": no_estimate})})


# One terra attempt's worst case W = 4,000 out x $12/1M = $0.048 + prompt (< 2,000 tokens x $2/1M
# = < $0.004), so $0.048 <= W < $0.052. A $0.09 cap fits one W (and W + $0.03) but never 2W (>= $0.096).

def test_rejected_429_costs_nothing_and_only_the_successful_retry_is_charged(tiny_rhp, adapters, config, sleeps):
    # Run 1: the 429 settles at $0; the retry succeeds with 15,000 in x $2/1M = $0.03 spent.
    # Run 2: $0.03 + W < $0.082 fits $0.09, so it is sent. (Had the 429 been charged W,
    # run 2 would need W + $0.03 + W >= $0.126 and be stopped.)
    config = tiny_cap(config, 0.09)
    adapters.llm = offer_llm(config, errors=[ProviderError(429)], usage=[(0, 0, 0), (15_000, 0, 0)])

    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)
    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == ["terra"] * 3


def test_a_503_still_charges_the_worst_case(tiny_rhp, adapters, config, sleeps):
    # A 5xx may have been billed: the failed attempt is charged W, so the retry needs 2W > $0.09
    # and is stopped before it is sent.
    config = tiny_cap(config, 0.09)
    adapters.llm = offer_llm(config, errors=[ProviderError(503)] * 3)

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == ["terra"]


def test_a_rejected_groq_call_uses_none_of_the_daily_groq_tokens(tiny_rhp, adapters, config, sleeps):
    # 159,999 Groq tokens used (limit 160,000 = 80% of 200,000). The 429'd groq call then counts 0 tokens,
    # so the third IPO still gets groq. (Counting its worst case, prompt + 4,000, would pass 160,000.)
    config = on_groq(config)
    adapters.llm = offer_llm(config, errors=[None, ProviderError(429)], usage=[(159_000, 0, 999)])

    for ipo_id in ("ACME-2026-10-13", "BETA-2026-10-13", "GAMMA-2026-10-13"):
        analyse_rhp(make_ipo().model_copy(update={"ipo_id": ipo_id}), tiny_rhp, adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == ["groq", "groq", "luna", "groq"]
