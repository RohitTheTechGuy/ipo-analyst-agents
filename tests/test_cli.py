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
    monkeypatch.setattr(cli, "run_daily", lambda today, a, c: seen_ipos.extend(a.exchange.open_ipos(today)) or _summary())

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
    monkeypatch.setattr(cli, "run_daily", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))

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


def _summary():
    from ipo_agents.contracts import RunSummary

    return RunSummary()
