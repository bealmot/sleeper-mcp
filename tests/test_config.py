"""Settings must resolve env -> file -> nothing, and token diagnostics must
never need the real token. These run offline.

Fake tokens here are deliberately NOT JWT-shaped enough to trip the secrets
scan in scripts/check.py.
"""

import json
import os
import stat

import pytest

from sleeper_mcp import config

ENV = ["SLEEPER_TOKEN", "SLEEPER_LEAGUE_ID", "SLEEPER_ROSTER_ID",
       "SLEEPER_ENABLE_WRITES", "XDG_CONFIG_HOME"]

WELL_FORMED = "eyJab.cdefg.hijkl"          # three parts, tiny — not a real JWT


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / "conf" / "config.json"
    monkeypatch.setenv("SLEEPER_MCP_CONFIG", str(path))
    return path


def test_env_beats_file(cfg, monkeypatch):
    config.save({"league_id": "111"})
    monkeypatch.setenv("SLEEPER_LEAGUE_ID", "222")
    assert config.resolve("SLEEPER_LEAGUE_ID") == "222"
    assert config.source("SLEEPER_LEAGUE_ID") == "environment"


def test_file_used_when_env_empty(cfg, monkeypatch):
    config.save({"league_id": "111"})
    monkeypatch.setenv("SLEEPER_LEAGUE_ID", "   ")      # blank counts as unset
    assert config.resolve("SLEEPER_LEAGUE_ID") == "111"
    assert config.source("SLEEPER_LEAGUE_ID") == "config file"


def test_default_when_nothing_set(cfg):
    assert config.resolve("SLEEPER_LEAGUE_ID", "") == ""
    assert config.source("SLEEPER_LEAGUE_ID") == "unset"


def test_missing_or_corrupt_file_is_empty(cfg):
    assert config.load_file() == {}
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{not json", encoding="utf-8")
    assert config.load_file() == {}
    cfg.write_text(json.dumps(["a list"]), encoding="utf-8")
    assert config.load_file() == {}


def test_save_is_private_and_merges(cfg):
    config.save({"token": WELL_FORMED, "enable_writes": "1"})
    config.save({"league_id": "111"})
    mode = stat.S_IMODE(os.stat(cfg).st_mode)
    assert mode == 0o600
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data == {"token": WELL_FORMED, "enable_writes": "1",
                    "league_id": "111"}
    assert not cfg.with_name(cfg.name + ".tmp").exists()


def test_xdg_default_path(monkeypatch, tmp_path):
    monkeypatch.delenv("SLEEPER_MCP_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_path() == tmp_path / "sleeper-mcp" / "config.json"


@pytest.mark.parametrize("bad, hint", [
    ("", "no token"),
    ("${SLEEPER_TOKEN}", "did not expand"),
    ('"' + WELL_FORMED + '"', "quotes"),
    ("Bearer " + WELL_FORMED, "Bearer"),
    ("eyJab.cdefg hijkl", "whitespace"),
    ("just-one-part", "JWT-shaped"),
])
def test_diagnose_names_the_problem(bad, hint):
    assert any(hint in p for p in config.diagnose_token(bad))


def test_well_formed_has_no_problems():
    assert config.diagnose_token(WELL_FORMED) == []


def test_diagnostics_never_leak_the_token():
    canary = "eyJab.cdefg.hijkl-CANARY"
    text = " ".join(config.diagnose_token(canary)) + config.describe_token(canary) \
        + config.explain_401(canary) + config.explain_401("${SLEEPER_TOKEN}")
    assert "CANARY" not in text and "hijkl" not in text


def test_explain_401_points_at_setup():
    assert "sleeper-mcp setup" in config.explain_401("${SLEEPER_TOKEN}")
    assert "sleeper-mcp setup" in config.explain_401(WELL_FORMED)
    assert "expired" in config.explain_401(WELL_FORMED)


def test_truthy():
    assert config.truthy("1") and config.truthy("True") and config.truthy("yes")
    assert not config.truthy("0") and not config.truthy("") and not config.truthy("no")
