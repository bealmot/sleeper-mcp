"""usage, breakouts and season_leaders against the fake league."""

import re

import pytest

pytest.importorskip("httpx")

from tests import fake                                        # noqa: E402


@pytest.fixture
def sleeper(monkeypatch):
    fake.install(monkeypatch)
    return fake


async def call(tool, **kw):
    return await getattr(tool, "fn", tool)(**kw)


async def test_points_are_scored_under_the_leagues_settings_not_the_preset(sleeper):
    """wr1's season row: 20 rec x 0 + 20 bonus_rec_wr x 0.5 + 250 yd x 0.1
    + 8 first downs x 0.5 = 39.0, not the preset's 60."""
    from sleeper_mcp.usage import season_leaders
    out = await call(season_leaders, position="WR", per_game=False, min_games=1)
    row = next(l for l in out.splitlines() if "Doe Catcher" in l)
    assert "39.0" in row and "60" not in row and "league scoring" in out


async def test_a_row_without_snap_data_does_not_crash_breakouts(sleeper):
    """wr3 carries targets but no snap keys — the shape that raised TypeError."""
    from sleeper_mcp.usage import breakouts
    out = await call(breakouts, position="WR")
    row = next(l for l in out.splitlines() if "Fox Catcher" in l)
    assert "     -" in row and "no snap counts" in out


async def test_usage_shows_the_trend_and_league_points(sleeper):
    from sleeper_mcp.usage import usage
    out = await call(usage, player_name="Doe Catcher", weeks=4)
    assert "weeks 1-4: 4 game(s) played" in out and "id=wr1" in out
    assert "opportunity share" in out and "->" in out
    assert "pts (league scoring)" in out


async def test_a_two_way_player_is_found_by_usage_tools(sleeper):
    from sleeper_mcp.usage import usage
    out = await call(usage, player_name="Kit Twoway")
    assert "No 2026 usage recorded" in out       # findable; simply no rows


async def test_season_shares_are_per_game(sleeper, monkeypatch):
    """A player with half the games must show his real per-game share, not
    half of it: wr1 30 targets in 2 games vs TEAM 100 in 4 -> 60%, not 30%."""
    from sleeper_mcp.usage import season_leaders
    monkeypatch.setitem(fake.STATS["wr1"], "gp", 2.0)
    out = await call(season_leaders, position="WR", metric="target_share", min_games=1)
    row = next(l for l in out.splitlines() if "Doe Catcher" in l)
    assert re.search(r"\b60\.0%", row), row


async def test_an_unknown_metric_is_named_not_ranked_as_zeros(sleeper):
    from sleeper_mcp.usage import season_leaders
    out = await call(season_leaders, metric="pionts", min_games=1)
    assert "Unknown metric 'pionts'" in out and "leaders by" not in out


async def test_the_games_floor_is_diagnosed_not_blamed_on_the_metric(sleeper):
    from sleeper_mcp.usage import season_leaders
    out = await call(season_leaders, min_games=9)
    assert "9+ games" in out and "most seen: 4" in out and "may not exist" not in out


async def test_a_past_season_window_ends_at_week_17(sleeper):
    from sleeper_mcp.usage import usage
    out = await call(usage, player_name="Doe Catcher", season="2025", weeks=3)
    assert "weeks 15-17" in out
