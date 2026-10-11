"""run_daily.py CLI and adapter wiring (ticket 23). Offline: adapters are fakes."""
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import httpx
import openai
import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from ipo_agents import cli
from ipo_agents.analysis import CHECKPOINT_DB
from ipo_agents.budget import BudgetMeter
from ipo_agents.contracts import RiskFactors, Synthesis, ValuationPlan
from ipo_agents.maintenance import smoke_schemas
from ipo_agents.sentiment import SentimentPoints, StanceTag
from ipo_agents.wiring import MissingSecrets, build_adapters
from tests.fakes import FakeExchange, ScriptedLLM, make_ipo


@pytest.fixture
def config_file(tmp_path, config):
    path = tmp_path / "config.yaml"
    path.write_text(config.model_dump_json())  # JSON is valid YAML
    return path


def run(argv, adapters, config_file, tmp_path):
    seen = {}

    def build(cfg, *, discovery_only=False):
        seen["discovery_only"] = discovery_only
        return adapters

    try:
        code = cli.main([*argv, "--config", str(config_file)], build=build,
                        log_dir=tmp_path / "logs", env_file=tmp_path / "no.env")
    finally:
        cli.remove_logging()
    return code, seen


def test_dry_run_lists_the_plan_without_model_or_telegram_calls(adapters, config_file, tmp_path, capsys):
    held = make_ipo().model_copy(update={"ipo_id": "HOLD-2026-10-13", "name": "Hold Ltd", "symbol": "HOLD",
                                         "rhp_url": None})
    adapters.exchange = FakeExchange([make_ipo(), held])

    code, seen = run(["--dry-run", "--date", "2026-10-13"], adapters, config_file, tmp_path)

    assert code == 0
    assert seen["discovery_only"] is True
    assert adapters.llm.calls == []
    assert adapters.notifier.messages == [] and adapters.notifier.documents == []
    out = capsys.readouterr().out
    assert "analyse: ACME-2026-10-13" in out
    assert "hold (waiting for RHP): HOLD-2026-10-13" in out


def test_single_ipo_by_symbol_analyses_only_that_ipo(adapters, config_file, tmp_path, monkeypatch):
    other = make_ipo().model_copy(update={"ipo_id": "ZETA-2026-10-13", "name": "Zeta Ltd", "symbol": "ZETA"})
    adapters.exchange = FakeExchange([make_ipo(), other])
    seen_ipos = []
    monkeypatch.setattr(cli, "run_daily", lambda today, a, c, **kw: seen_ipos.extend(a.exchange.open_ipos(today)) or _summary())

    code, _ = run(["--ipo", "zeta", "--date", "2026-10-13"], adapters, config_file, tmp_path)

    assert code == 0
    assert [i.ipo_id for i in seen_ipos] == ["ZETA-2026-10-13"]


def test_single_ipo_not_open_is_a_clear_error(adapters, config_file, tmp_path, capsys):
    adapters.exchange = FakeExchange([make_ipo()])

    code, _ = run(["--ipo", "NOPE", "--date", "2026-10-13"], adapters, config_file, tmp_path)

    assert code != 0
    err = capsys.readouterr().err
    assert "NOPE is not among today's open IPOs" in err
    assert "ACME-2026-10-13" in err


def test_a_raising_run_exits_non_zero_and_is_logged(adapters, config_file, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_daily", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    code, _ = run(["--date", "2026-10-13"], adapters, config_file, tmp_path)

    assert code == 1
    assert "boom" in (tmp_path / "logs" / "ipo-agents.log").read_text()


def test_default_date_is_today_in_india():
    from datetime import datetime, timezone

    # 20:00 UTC on 12 Oct is 01:30 on 13 Oct in India.
    assert cli.today_in_india(datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)) == date(2026, 10, 13)


def test_missing_secrets_are_named_and_values_are_never_shown(config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecret-value")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    with pytest.raises(MissingSecrets) as exc:
        build_adapters(config)

    msg = str(exc.value)
    assert "TELEGRAM_BOT_TOKEN" in msg and "TELEGRAM_CHAT_ID" in msg
    assert "OPENAI_API_KEY" not in msg
    assert "sk-supersecret-value" not in msg


def test_real_market_data_is_wired_without_any_secret_of_its_own(config, monkeypatch):
    from ipo_agents.market import HttpMarketData

    for key in ("OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setenv(key, "x")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    assert isinstance(build_adapters(config).market, HttpMarketData)


def _summary():
    from ipo_agents.contracts import RunSummary

    return RunSummary()


def offer_llm(config, **kw):
    from ipo_agents.contracts import Fact, OfferObjects
    from tests.fakes import ScriptedLLM, offer_objects

    return ScriptedLLM({OfferObjects: offer_objects(issue_size=Fact(
        value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))},
        models=config.models, **kw)


def test_dry_run_shows_done_ipos_as_skipped_with_their_reminders_and_force_as_analysed(
        adapters, config, config_file, tmp_path, capsys):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm(config)
    run(["--date", "2026-10-13"], adapters, config_file, tmp_path)
    capsys.readouterr()

    run(["--dry-run", "--date", "2026-10-14"], adapters, config_file, tmp_path)
    plain = capsys.readouterr().out
    run(["--dry-run", "--ipo", "acme", "--force", "--date", "2026-10-14"], adapters, config_file, tmp_path)
    forced = capsys.readouterr().out

    assert "skip (done): ACME-2026-10-13  Acme Ltd" in plain
    assert "update (GMP + subscription, closes 15 Oct): ACME-2026-10-13  Acme Ltd" in plain
    assert "analyse: ACME-2026-10-13  Acme Ltd" in forced
    assert len(adapters.llm.calls) == 1 and len(adapters.notifier.messages) == 1


def test_forced_rerun_prints_the_budget_left_and_analyses_again(adapters, config, config_file, tmp_path, capsys):
    # The first read is billed 50,000 out x $12/1M = $0.60 of the $1.50 cap, leaving $0.90.
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = offer_llm(config, usage=[(0, 0, 50_000)])

    run(["--ipo", "ACME", "--date", "2026-10-13"], adapters, config_file, tmp_path)
    code, _ = run(["--ipo", "ACME", "--force", "--date", "2026-10-13"], adapters, config_file, tmp_path)

    assert code == 0
    assert ("Forced re-run of ACME-2026-10-13: $0.60 of its $1.50 budget already spent, $0.90 left"
            in capsys.readouterr().out)
    assert len(adapters.llm.calls) == 2
    assert len(adapters.notifier.messages) == 2


def test_force_without_ipo_is_refused(adapters, config_file, tmp_path):
    with pytest.raises(SystemExit) as exit_:
        run(["--force"], adapters, config_file, tmp_path)

    assert exit_.value.code == 2
    assert adapters.llm.calls == []


class Exec:
    def __init__(self):
        self.calls = []

    def __call__(self, path, argv, env):
        self.calls.append((path, argv, env))


def test_manual_run_on_a_mac_re_execs_itself_with_the_renderer_library_path(tmp_path):
    execve = Exec()

    cli.reexec_with_renderer_env(environ={"HOME": "/Users/me"}, platform="darwin", lib_dir=tmp_path,
                                 argv=["/usr/bin/python3", "run_daily.py", "--ipo", "ACME"], execve=execve)

    [(path, argv, env)] = execve.calls
    assert argv == [path, "run_daily.py", "--ipo", "ACME"]
    assert env == {"HOME": "/Users/me", "DYLD_FALLBACK_LIBRARY_PATH": str(tmp_path)}


@pytest.mark.parametrize("environ, platform, lib_exists", [
    ({"DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib"}, "darwin", True),  # set by launchd, or already re-exec'd
    ({}, "linux", True),
    ({}, "darwin", False),  # no Homebrew: nothing to point at
])
def test_no_re_exec_when_not_needed_or_not_possible(tmp_path, environ, platform, lib_exists):
    execve = Exec()

    cli.reexec_with_renderer_env(environ=environ, platform=platform,
                                 lib_dir=tmp_path if lib_exists else tmp_path / "missing",
                                 argv=["python", "run_daily.py"], execve=execve)

    assert execve.calls == []


# --- --smoke: one tiny call per schema on Luna (offline here: a scripted LLM) -------------------------

def provider_error(cls, status, message):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    return cls(message, response=response, body={"message": message})


def smoke(config_file, tmp_path, llm):
    try:
        return cli.main(["--smoke", "--date", "2026-10-13", "--config", str(config_file)],
                        build=lambda *a, **k: pytest.fail("--smoke must not build the daily adapters"),
                        smoke_llm=lambda cfg: llm, log_dir=tmp_path / "logs", env_file=tmp_path / "no.env")
    finally:
        cli.remove_logging()


def test_smoke_calls_every_schema_once_on_luna_and_reports_each(config, config_file, tmp_path, capsys):
    schemas = smoke_schemas()
    responses = {s: "parsed" for s in schemas}
    responses[Synthesis] = provider_error(openai.BadRequestError, 400,
                                          "Invalid schema for response_format 'Synthesis': 'additionalProperties'")
    responses[StanceTag] = RuntimeError("luna: provider response carried no token usage")
    llm = ScriptedLLM(responses, models=config.models)

    code = smoke(config_file, tmp_path, llm)

    out = capsys.readouterr().out
    assert code == 1
    assert [c["schema"] for c in llm.calls] == schemas  # every LLM-facing schema, once
    assert {Synthesis, StanceTag, ValuationPlan, SentimentPoints, RiskFactors} <= set(schemas)
    assert {(c["model"], c["max_tokens"]) for c in llm.calls} == {("luna", 500)}
    assert "Synthesis              SCHEMA REJECTED" in out
    assert "StanceTag              NO USAGE" in out
    assert "RiskFactors            OK (1000 in / 200 out)" in out
    assert f"{len(schemas) - 2}/{len(schemas)} OK" in out
    # Each OK call: 1,000 in x $0.20/1M + 200 out x $1.20/1M = $0.00044; the 400 costs nothing, the
    # missing-usage call its worst case. All logged under SMOKE-<date>, never under an IPO.
    assert BudgetMeter("SMOKE-2026-10-13", config).spent > (len(schemas) - 2) * 0.00044


def test_smoke_names_a_wrong_model_id(config, config_file, tmp_path, capsys):
    gone = provider_error(openai.NotFoundError, 404, "The model `gpt-5.6-luna` does not exist")
    llm = ScriptedLLM({s: gone for s in smoke_schemas()}, models=config.models)

    code = smoke(config_file, tmp_path, llm)

    assert code == 1
    assert "MODEL ID" in capsys.readouterr().out


def test_smoke_passes_when_every_schema_answers(config, config_file, tmp_path):
    llm = ScriptedLLM({s: "parsed" for s in smoke_schemas()}, models=config.models)

    assert smoke(config_file, tmp_path, llm) == 0


# --- --reset-budget: dev reset of one IPO's spend and checkpoints ------------------------------------

def seed_spend_and_checkpoints(config):
    """ACME spends 100,000 in x $2/1M = $0.20 on terra, BETA $0.10; ACME has two checkpoint threads."""
    for ipo_id, tokens in (("ACME-2026-10-13", 100_000), ("BETA-2026-10-13", 50_000)):
        meter = BudgetMeter(ipo_id, config)
        meter.settle(meter.reserve("terra", 0, 1), task="offer_objects", model="terra",
                     input_tokens=tokens, cached_tokens=0, output_tokens=0)
    path = Path(config.db_path).with_name(CHECKPOINT_DB)
    with closing(sqlite3.connect(path, check_same_thread=False)) as conn:
        SqliteSaver(conn).setup()
        for thread in ("ACME-2026-10-13:aaa", "ACME-2026-10-13:bbb", "BETA-2026-10-13:ccc"):
            conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id) VALUES (?, '', '1')",
                         (thread,))
        conn.commit()
    return path


def checkpoint_threads(path):
    with closing(sqlite3.connect(path)) as conn:
        return sorted(t for (t,) in conn.execute("SELECT thread_id FROM checkpoints"))


def reset(config_file, tmp_path, argv, answer):
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return answer

    try:
        code = cli.main([*argv, "--config", str(config_file)], build=lambda *a, **k: pytest.fail("no adapters"),
                        ask=ask, log_dir=tmp_path / "logs", env_file=tmp_path / "no.env")
    finally:
        cli.remove_logging()
    return code, asked


def test_reset_budget_shows_the_spend_and_after_typed_confirmation_deletes_only_that_ipo(
        config, config_file, tmp_path, capsys):
    checkpoints = seed_spend_and_checkpoints(config)

    code, asked = reset(config_file, tmp_path, ["--ipo", "ACME-2026-10-13", "--reset-budget"], "reset")

    assert code == 0 and len(asked) == 1
    assert ("ACME-2026-10-13: $0.2000 spent of $1.50 in 1 logged call(s); 2 saved checkpoint thread(s)"
            in capsys.readouterr().out)
    assert BudgetMeter("ACME-2026-10-13", config).spent == 0
    assert BudgetMeter("BETA-2026-10-13", config).spent == pytest.approx(0.10)
    assert checkpoint_threads(checkpoints) == ["BETA-2026-10-13:ccc"]


def test_reset_budget_without_the_typed_word_changes_nothing(config, config_file, tmp_path):
    checkpoints = seed_spend_and_checkpoints(config)

    code, _ = reset(config_file, tmp_path, ["--ipo", "ACME-2026-10-13", "--reset-budget"], "yes")

    assert code == 1
    assert BudgetMeter("ACME-2026-10-13", config).spent == pytest.approx(0.20)
    assert len(checkpoint_threads(checkpoints)) == 3


def test_reset_budget_without_ipo_is_refused(config, config_file, tmp_path):
    with pytest.raises(SystemExit) as exit_:
        reset(config_file, tmp_path, ["--reset-budget"], "reset")

    assert exit_.value.code == 2
