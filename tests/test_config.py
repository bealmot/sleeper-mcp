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


# --- file and directory permissions -----------------------------------------
# Both of these pin real defects found by review, not hypotheticals.

def test_the_token_file_is_never_created_world_readable(tmp_path, monkeypatch):
    """THE REGRESSION. The temp file used to be written and chmod'd AFTER.

    Measured at 0644 with the token already in it. A window that short still
    looks like zero in the code and is not zero on disk, so the mode has to be
    set at creation. This captures the actual mode passed to os.open.
    """
    monkeypatch.setenv("SLEEPER_MCP_CONFIG", str(tmp_path / "d" / "c.json"))
    modes = []
    real_open = os.open

    def spy(path, flags, mode=0o777, *a, **kw):
        if flags & os.O_CREAT:
            modes.append(mode)
        return real_open(path, flags, mode, *a, **kw)

    monkeypatch.setattr(os, "open", spy)
    config.save({"token": "aaa.bbb.ccc"})
    assert modes, "the config file was not created through os.open"
    assert all(m & 0o077 == 0 for m in modes), f"created group/other-readable: {modes}"


def test_save_does_not_tighten_a_directory_it_did_not_create(tmp_path, monkeypatch):
    """THE REGRESSION. SLEEPER_MCP_CONFIG=~/.sleeperrc chmod'd $HOME to 0700.

    A tool may secure a directory it made. It must not silently restrict one
    it was merely pointed at.
    """
    existing = tmp_path / "shared"
    existing.mkdir()
    os.chmod(existing, 0o755)
    monkeypatch.setenv("SLEEPER_MCP_CONFIG", str(existing / "c.json"))
    config.save({"token": "aaa.bbb.ccc"})
    assert stat.S_IMODE(existing.stat().st_mode) == 0o755
    assert stat.S_IMODE((existing / "c.json").stat().st_mode) == 0o600


def test_save_does_secure_a_directory_it_creates(tmp_path, monkeypatch):
    monkeypatch.setenv("SLEEPER_MCP_CONFIG", str(tmp_path / "mine" / "c.json"))
    config.save({"token": "aaa.bbb.ccc"})
    assert stat.S_IMODE((tmp_path / "mine").stat().st_mode) == 0o700
