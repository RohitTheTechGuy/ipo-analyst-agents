"""run_daily.py CLI and adapter wiring (ticket 23). Offline: adapters are fakes."""
from datetime import date

import pytest

from ipo_agents import cli
from ipo_agents.wiring import MissingSecrets, build_adapters
from tests.fakes import FakeExchange, make_ipo


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
    assert "remind (closes 15 Oct): ACME-2026-10-13  Acme Ltd" in plain
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
