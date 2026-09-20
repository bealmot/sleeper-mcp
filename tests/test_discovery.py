"""find_my_leagues, league_info, auth_status, setup_token, and the pick'em pair."""

import json

import pytest

pytest.importorskip("httpx")

from tests import fake                                        # noqa: E402


@pytest.fixture
def sleeper(monkeypatch):
    fake.install(monkeypatch)
    return fake


async def call(tool, **kw):
    return await getattr(tool, "fn", tool)(**kw)


async def test_find_my_leagues_lists_the_original_before_its_clone_and_marks_it(sleeper):
    from sleeper_mcp.discovery import find_my_leagues
    out = await call(find_my_leagues, username="alder")
    first, second = out.index("SLEEPER_LEAGUE_ID  111"), out.index("SLEEPER_LEAGUE_ID  113")
    assert first < second
    assert "CLONE of 111" in out and "guillotine" in out and "keeper, 1 kept" in out
    assert "continues 110" in out and "SLEEPER_ROSTER_ID  1" in out


async def test_find_my_leagues_does_not_blame_the_name_for_a_network_error(sleeper, monkeypatch):
    from sleeper_mcp import discovery as d
    from sleeper_mcp.client import TransportFailure
    from sleeper_mcp.discovery import find_my_leagues
    import httpx

    async def down(path):
        raise TransportFailure(path, httpx.ConnectError("x"))
    monkeypatch.setattr(d, "rest", down)
    out = await call(find_my_leagues, username="alder")
    assert "could not be reached" in out and "nothing is wrong with the name" in out


async def test_find_my_leagues_accepts_a_username_with_a_money_word(sleeper, monkeypatch):
    """'BengalStripes' used to be refused by the guard and reported as a typo."""
    from sleeper_mcp.discovery import find_my_leagues
    out = await call(find_my_leagues, username="BengalStripes")
    assert "No Sleeper user called" in out and "Refused" not in out


async def test_league_info_reads_per_position_reception_bonuses(sleeper):
    """rec=0 with bonus_rec_rb/wr/te=0.5 is a HALF-PPR league, not zero-PPR,
    and 0.5 at every position is not a TE premium."""
    from sleeper_mcp.discovery import league_info
    out = await call(league_info)
    assert "half-PPR" in out and "zero-PPR" not in out and "TE premium" not in out
    assert "per reception, WR" in out and "FIRST DOWNS" in out
    assert "format         keeper, 1 kept" in out
    assert "playoffs       week 15, 2 teams" in out
    assert "waiver day     Wednesday" in out and "allowed: IR, OUT" in out


async def test_league_info_explains_a_guillotine_clone(sleeper):
    from sleeper_mcp.discovery import league_info
    out = await call(league_info, league_id_="113")
    assert "GUILLOTINE" in out and "trades         DISABLED" in out
    assert "playoffs       none" in out and "CLONE of 111" in out


async def test_league_info_reports_a_real_te_premium(sleeper, monkeypatch):
    from sleeper_mcp.discovery import league_info
    from sleeper_mcp import client
    monkeypatch.setitem(fake.LEAGUE_CFG["scoring_settings"], "bonus_rec_te", 1.0)
    client.cache_clear()
    out = await call(league_info)
    assert "TE premium: +0.5" in out and "0.5 at RB, 0.5 at WR, 1 at TE" in out


async def test_auth_status_sees_a_token_installed_after_import(sleeper, monkeypatch):
    """The setup page swaps client.TOKEN in place; the confirming tool must
    read the live value rather than a copy taken at import."""
    from sleeper_mcp import client
    from sleeper_mcp.discovery import auth_status
    monkeypatch.setattr(client, "TOKEN", "")
    assert "skipped — no token" in await call(auth_status)
    monkeypatch.setattr(client, "TOKEN", "eyJab.cdefg.hijkl")
    monkeypatch.setattr(client, "WRITES_ENABLED", True)
    out = await call(auth_status)
    assert "live check     OK" in out and "Alder" in out and "ENABLED" in out


async def test_setup_token_says_when_it_will_enable_writes_and_about_env_shadowing(sleeper, monkeypatch):
    from sleeper_mcp import config, webauth
    from sleeper_mcp.discovery import setup_token
    monkeypatch.setattr(webauth, "start", lambda enable_writes=False, **k: type(
        "S", (), {"url": "http://127.0.0.1:1/setup/x", "enable_writes": enable_writes})())
    monkeypatch.setitem(config._SOURCE, "SLEEPER_TOKEN", "environment")
    out = await call(setup_token, enable_writes=True)
    assert "ALSO ENABLE WRITES" in out and "environment one wins" in out
    out = await call(setup_token)
    assert "ALSO ENABLE WRITES" not in out


async def test_find_my_pools_discovers_the_lobby_and_entry(sleeper):
    from sleeper_mcp.pickem import find_my_pools
    out = await call(find_my_pools)
    assert "Fake Pool" in out and "SLEEPER_PICKEM_LEAGUE  222" in out
    assert "SLEEPER_PICKEM_ROSTER  1" in out


async def test_pickem_standings_uses_sleepers_points_and_marks_you(sleeper):
    from sleeper_mcp.pickem import pickem_standings
    out = await call(pickem_standings)
    assert "3 entries, 2 with points" in out
    lines = out.splitlines()
    assert any("Birch" in l and "2.0" in l for l in lines)
    assert "<- you" in out and "You are 2 of 3, 1 behind the lead" in out


def test_no_write_tool_is_marked_read_only():
    import asyncio
    import sleeper_mcp.server as s
    tools = asyncio.run(s.mcp.list_tools())
    writes = {t.name for t in tools if t.annotations and not t.annotations.readOnlyHint}
    assert {"set_lineup", "waiver_claim", "propose_trade", "respond_trade",
            "pickem_pick", "set_ir", "set_keepers", "trade_block", "watch_player",
            "cancel_claim", "change_bid"} <= writes
    assert "standings" not in writes and "roster" not in writes
    assert all(t.annotations is not None for t in tools)
