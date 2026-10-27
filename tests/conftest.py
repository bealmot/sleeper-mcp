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


@pytest.fixture(autouse=True)
def _no_value_in_query_text():
    """NO VALUE GOES IN A GRAPHQL QUERY'S TEXT; it goes in the variables.
    tests/fake.py `problems` says why, and what else it refuses.

    Checked here, after the test, and not raised by the fake: several tools
    catch every exception from gql and print it as a reason, and a weak
    assertion on that output still passes.
    """
    from tests import fake
    fake.SENT.clear()
    yield
    bad = [f"{p}  in  {q[:100]}" for q, v in fake.SENT
           for p in fake.problems(q, v)]
    assert not bad, "\n".join(bad)
