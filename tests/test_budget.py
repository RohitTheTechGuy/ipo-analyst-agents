"""Budget rules at seam B (spec: Budget rules). Prices from config.yaml, USD per 1M tokens:
terra in 2.00 / cached 0.20 / out 12.00; groq 0 / 0 / 0. `offer_objects` routes to terra."""
import pytest

from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetExceeded
from ipo_agents.contracts import Fact, OfferObjects
from ipo_agents.llm import LLMParseError
from tests.fakes import ScriptedLLM, make_ipo

OFFER = OfferObjects(issue_size_cr=Fact(
    value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))


def offer_llm(config, **kw):
    return ScriptedLLM({OfferObjects: OFFER}, models=config.models, **kw)


def test_call_whose_max_tokens_alone_would_cross_the_cap_is_never_sent(tiny_rhp, adapters, config):
    # 130,000 output tokens x $12/1M = $1.56 > $1.50
    adapters.llm = offer_llm(config)
    config = config.model_copy(update={"default_max_tokens": 130_000})

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert adapters.llm.calls == []


def test_worst_case_also_counts_the_prompt_tokens(tiny_rhp, adapters, config):
    # 124,995 x $12/1M = $1.49994, leaving $0.00006 = 30 prompt tokens at $2/1M.
    # The system prompt alone is well over 30 tokens, so the call must not be sent.
    adapters.llm = offer_llm(config)
    config = config.model_copy(update={"default_max_tokens": 124_995})

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert adapters.llm.calls == []


def test_call_under_the_cap_goes_through(tiny_rhp, adapters, config):
    # 120,000 x $12/1M = $1.44; the tiny RHP prompt (< 1,000 tokens) adds < $0.002.
    adapters.llm = offer_llm(config)
    config = config.model_copy(update={"default_max_tokens": 120_000})

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.offer.issue_size_cr.value == 1250.0
    assert len(adapters.llm.calls) == 1


def test_call_without_a_positive_max_tokens_is_rejected(tiny_rhp, adapters, config):
    adapters.llm = offer_llm(config)
    config = config.model_copy(update={"default_max_tokens": 0})

    with pytest.raises(ValueError, match="max_tokens"):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert adapters.llm.calls == []


def test_call_to_an_unconfigured_model_never_reaches_the_provider(tiny_rhp, adapters, config):
    adapters.llm = offer_llm(config)
    config = config.model_copy(update={"routing": {**config.routing, "offer_objects": "sol"}})

    with pytest.raises(ValueError, match="'sol'"):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert adapters.llm.calls == []


@pytest.mark.parametrize("input_tokens, cached_tokens, stops", [
    # 730,000 x $2/1M = $1.46 + 200 out x $12/1M = $0.0024 -> $1.4624 spent.
    # Next call's worst case is at least 4,000 x $12/1M = $0.048 -> $1.5104 > $1.50: stop.
    (730_000, 0, True),
    # 715,000 uncached x $2 = $1.43 + 150,000 cached x $0.20 = $0.03 + $0.0024 -> $1.4624: stop.
    # (Treating cached input as free would give $1.4324 and let the call through.)
    (865_000, 150_000, True),
    # 600,000 uncached x $2 = $1.20 + 500,000 cached x $0.20 = $0.10 + $0.0024 -> $1.3024.
    # Worst case ~$0.048 more -> ~$1.35 < $1.50: proceeds.
    # (Charging cached input at the full $2 would give $2.2024 and stop.)
    (1_100_000, 500_000, False),
])
def test_spend_from_an_earlier_run_counts_against_the_cap(tiny_rhp, adapters, config, input_tokens, cached_tokens, stops):
    adapters.llm = offer_llm(config, usage=[(input_tokens, cached_tokens, 200)])
    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    if stops:
        with pytest.raises(BudgetExceeded):
            analyse_rhp(make_ipo(), tiny_rhp, adapters, config)
        assert len(adapters.llm.calls) == 1
    else:
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)
        assert len(adapters.llm.calls) == 2


def test_a_failed_parse_still_counts_its_spend(tiny_rhp, adapters, config):
    # The unparseable reply still billed 730,000 in + 200 out on terra = $1.4624 (see above),
    # so the retry's worst case (>= $0.048) would cross $1.50 and is never sent.
    bad = LLMParseError("reply did not match OfferObjects", input_tokens=730_000, cached_tokens=0, output_tokens=200)
    adapters.llm = ScriptedLLM({OfferObjects: bad}, models=config.models)

    with pytest.raises(LLMParseError):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)
    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert len(adapters.llm.calls) == 1


def test_groq_calls_cost_nothing(tiny_rhp, adapters, config):
    # Groq prices are 0/0/0: 10M tokens in and 100k out still spend $0, so nothing stops.
    config = config.model_copy(update={"routing": {**config.routing, "offer_objects": "groq"}})
    adapters.llm = offer_llm(config, usage=[(10_000_000, 0, 100_000)] * 3)

    for _ in range(3):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [c["model"] for c in adapters.llm.calls] == ["groq"] * 3


def test_spend_is_tracked_per_ipo(tiny_rhp, adapters, config):
    # Acme spends $1.4624 (see above); Beta starts from $0 and is not stopped.
    adapters.llm = offer_llm(config, usage=[(730_000, 0, 200)])
    analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    beta = make_ipo().model_copy(update={"ipo_id": "BETA-2026-10-13"})
    analyse_rhp(beta, tiny_rhp, adapters, config)

    assert len(adapters.llm.calls) == 2


class FailingProvider(ScriptedLLM):
    """The provider errors after the request went out (e.g. a timeout): it may still bill."""

    def invoke_structured(self, **kwargs):
        self.calls.append(kwargs)
        raise TimeoutError("read timed out")


def test_provider_failure_counts_its_worst_case_against_the_cap(tiny_rhp, adapters, config):
    # One terra call: 4,000 output tokens x $12/1M = $0.048 plus a small prompt, so ~$0.0485 worst case.
    # Cap $0.09 fits one call but not two. If the failed call cost nothing, the retry would be sent.
    config = config.model_copy(update={"budget": config.budget.model_copy(update={"cap_usd": 0.09})})
    adapters.llm = FailingProvider({}, models=config.models)
    with pytest.raises(TimeoutError):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    adapters.llm = offer_llm(config)
    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)
    assert adapters.llm.calls == []


def test_worst_case_includes_request_overhead_beyond_the_prompt_text(tiny_rhp, adapters, config):
    # 4,000 output tokens x $12/1M = $0.048. The prompt text (system rules + 3 tiny pages) is
    # under ~150 tokens = $0.0003, so text alone fits a $0.0485 cap. The output schema and
    # per-request framing (hundreds of tokens) don't, so the call must not be sent.
    config = config.model_copy(update={"budget": config.budget.model_copy(update={"cap_usd": 0.0485})})
    adapters.llm = offer_llm(config)

    with pytest.raises(BudgetExceeded):
        analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert adapters.llm.calls == []
