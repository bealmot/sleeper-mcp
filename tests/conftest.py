"""Suite-wide guards.

THE DISK CACHE MUST NEVER SEE A TEST. tests/fake.py replaces client.rest, so
players() would save the fake's dozen players to the real cache folder, and
the next real server start would load them as the NFL. Every test gets its own
empty cache folder instead.
"""

import pytest


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SLEEPER_MCP_CACHE", str(tmp_path / "cache"))
