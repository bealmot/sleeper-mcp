"""The player dictionary on disk, and what happens when Sleeper says 429.

Pure — no network. The disk tests replace client.rest; the 429 tests give
httpx a MockTransport, so the real rest() and gql() code runs against canned
responses.
"""

import json
import os
import time

import pytest

pytest.importorskip("httpx")

import httpx  # noqa: E402

from sleeper_mcp import client  # noqa: E402

DUMP = {"4046": {"full_name": "Patrick Mahomes", "position": "QB"}}


@pytest.fixture(autouse=True)
def _fresh_memory():
    client.cache_clear()
    yield
    client.cache_clear()


def counting_rest(monkeypatch, result=DUMP):
    calls = []

    async def rest(path):
        calls.append(path)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(client, "rest", rest)
    return calls


def write_disk(value, age_s):
    path = client.cache_dir() / "players.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


# --- disk cache -------------------------------------------------------------

def test_the_cache_folder_is_the_test_one():
    """The guard in conftest.py, checked: a test never touches ~/.cache."""
    assert os.environ["SLEEPER_MCP_CACHE"] in str(client.cache_dir())


async def test_a_fetch_is_saved_to_disk(monkeypatch):
    counting_rest(monkeypatch)
    await client.players()
    saved = json.loads((client.cache_dir() / "players.json").read_text())
    assert saved == DUMP


async def test_a_fresh_disk_copy_means_no_download(monkeypatch):
    write_disk(DUMP, age_s=60)
    calls = counting_rest(monkeypatch)
    out = await client.players()
    assert calls == [] and out["4046"]["full_name"] == "Patrick Mahomes"


async def test_an_expired_disk_copy_is_refreshed(monkeypatch):
    write_disk({"old": {"full_name": "Old Player"}}, age_s=3600)
    calls = counting_rest(monkeypatch)
    out = await client.players()
    assert calls == ["/players/nfl"] and "4046" in out
    saved = json.loads((client.cache_dir() / "players.json").read_text())
    assert saved == DUMP


async def test_sleeper_down_falls_back_to_a_stale_copy_and_says_so(
        monkeypatch, caplog):
    write_disk(DUMP, age_s=2 * 86400)
    counting_rest(monkeypatch, httpx.ConnectError("down"))
    out = await client.players()
    assert "4046" in out
    assert any("cached" in r.message and "48" in r.message
               for r in caplog.records), caplog.text


async def test_sleeper_down_with_no_copy_raises(monkeypatch):
    counting_rest(monkeypatch, httpx.ConnectError("down"))
    with pytest.raises(httpx.ConnectError):
        await client.players()


async def test_a_copy_older_than_the_limit_is_not_used(monkeypatch):
    write_disk(DUMP, age_s=8 * 86400)
    counting_rest(monkeypatch, httpx.ConnectError("down"))
    with pytest.raises(httpx.ConnectError):
        await client.players()


async def test_a_corrupt_copy_is_refetched(monkeypatch):
    path = client.cache_dir() / "players.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    calls = counting_rest(monkeypatch)
    assert "4046" in await client.players()
    assert calls == ["/players/nfl"]


async def test_defences_get_names_from_a_disk_copy_too(monkeypatch):
    write_disk({"MIN": {"first_name": "Minnesota", "last_name": "Vikings",
                        "position": "DEF"}}, age_s=60)
    counting_rest(monkeypatch)
    out = await client.players()
    assert out["MIN"].get("full_name")


# --- 429 --------------------------------------------------------------------

def mock_sleeper(monkeypatch, responses):
    """Serve `responses` in order; record each request and each wait."""
    seen, waits = [], []
    queue = list(responses)

    def handler(request):
        seen.append(request)
        return queue.pop(0)

    real = httpx.AsyncClient
    monkeypatch.setattr(client, "_http", lambda timeout: real(
        timeout=timeout, transport=httpx.MockTransport(handler)))

    async def sleep(s):
        waits.append(s)

    monkeypatch.setattr(client, "_sleep", sleep)
    return seen, waits


def r429(after=None):
    return httpx.Response(429, headers={"retry-after": after} if after else {})


async def test_rest_retries_a_429_after_the_time_sleeper_asks(monkeypatch):
    seen, waits = mock_sleeper(monkeypatch, [
        r429("5"), httpx.Response(200, json={"ok": 1})])
    assert await client.rest("/state/nfl") == {"ok": 1}
    assert len(seen) == 2 and waits == [5.0]


async def test_without_retry_after_the_waits_double(monkeypatch):
    seen, waits = mock_sleeper(monkeypatch, [
        r429(), r429(), httpx.Response(200, json={})])
    await client.rest("/state/nfl")
    assert waits == [2.0, 4.0]


async def test_a_long_retry_after_is_capped(monkeypatch):
    _, waits = mock_sleeper(monkeypatch, [
        r429("600"), httpx.Response(200, json={})])
    await client.rest("/state/nfl")
    assert waits == [client._MAX_WAIT]


async def test_retries_are_bounded_then_the_429_is_raised(monkeypatch):
    seen, waits = mock_sleeper(monkeypatch, [r429("1")] * 10)
    with pytest.raises(httpx.HTTPStatusError) as e:
        await client.rest("/state/nfl")
    assert e.value.response.status_code == 429
    assert len(seen) == client._RETRIES + 1 and len(waits) == client._RETRIES


async def test_other_errors_are_not_retried(monkeypatch):
    seen, waits = mock_sleeper(monkeypatch, [httpx.Response(500)] * 3)
    with pytest.raises(httpx.HTTPStatusError):
        await client.rest("/state/nfl")
    assert len(seen) == 1 and waits == []


async def test_graphql_retries_a_429_too(monkeypatch):
    seen, waits = mock_sleeper(monkeypatch, [
        r429("3"), httpx.Response(200, json={"data": {"x": 1}})])
    assert await client.gql("query { x }") == {"x": 1}
    assert len(seen) == 2 and waits == [3.0]
