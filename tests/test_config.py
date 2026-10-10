"""Startup: a routed model with no price makes the run refuse to start (spec: Budget rules)."""
import pytest
import yaml
from pydantic import ValidationError

from ipo_agents.config import load_config
from tests.conftest import REPO_ROOT


def write_config(tmp_path, edit):
    raw = yaml.safe_load((REPO_ROOT / "config.yaml").read_text())
    edit(raw)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def test_shipped_config_loads_with_groq_priced_at_zero():
    cfg = load_config(REPO_ROOT / "config.yaml")

    assert cfg.models["groq"].price_out == 0.0


def test_routed_model_without_a_price_refuses_to_start(tmp_path):
    path = write_config(tmp_path, lambda raw: raw["models"]["luna"].pop("price_cached"))

    with pytest.raises(ValidationError, match="luna.*price_cached"):
        load_config(path)


def test_routing_to_an_unconfigured_model_refuses_to_start(tmp_path):
    path = write_config(tmp_path, lambda raw: raw["routing"].update(verifier="sol"))

    with pytest.raises(ValidationError, match="verifier.*'sol'"):
        load_config(path)


def test_unpriced_model_that_nothing_routes_to_is_allowed(tmp_path):
    path = write_config(tmp_path, lambda raw: raw["models"].update(spare={"provider": "openai", "id": "x"}))

    assert "spare" in load_config(path).models


@pytest.mark.parametrize("edit, match", [
    (lambda raw: raw["models"]["terra"].update(price_out=0.0), "terra.*free"),
    (lambda raw: raw["models"]["luna"].update(price_in=-1.0), "greater than or equal"),
    (lambda raw: raw["budget"].update(cap_usd=0), "greater than 0"),
    (lambda raw: raw.update(default_max_tokens=0), "greater than 0"),
])
def test_config_that_would_disable_or_break_metering_refuses_to_start(tmp_path, edit, match):
    with pytest.raises(ValidationError, match=match):
        load_config(write_config(tmp_path, edit))
