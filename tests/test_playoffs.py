"""playoff_odds, matchup_odds, schedule_strength, playoff_bracket, standings_trend."""

import pytest

pytest.importorskip("httpx")

from tests import fake                                        # noqa: E402


@pytest.fixture
def sleeper(monkeypatch):
    fake.install(monkeypatch)
    return fake


async def call(tool, **kw):
    return await getattr(tool, "fn", tool)(**kw)


async def test_playoff_odds_use_the_leagues_clock_not_the_nfl_week(sleeper):
    """last_scored_leg is 4 in the fixture, so weeks 5-14 remain: 10 x 1 game."""
    from sleeper_mcp.playoffs import playoff_odds
    out = await call(playoff_odds, trials=200)
    assert "Week 5" in out and "10 games left" in out and "draws it from" in out


async def test_a_completed_previous_season_is_not_resimulated(sleeper):
    from sleeper_mcp.playoffs import playoff_odds
    out = await call(playoff_odds, trials=50, league_id_=fake.PREV_LEAGUE)
    assert "is complete" in out and "playoff_bracket" in out


async def test_a_guillotine_league_is_refused_with_the_reason(sleeper):
    from sleeper_mcp.playoffs import playoff_bracket, playoff_odds, standings_trend
    assert "GUILLOTINE" in await call(playoff_odds, trials=50, league_id_="113")
    assert "guillotine" in await call(playoff_bracket, league_id_="113")
    assert "guillotine" in await call(standings_trend, league_id_="113")


async def test_a_week_that_failed_to_fetch_is_named_not_dropped(sleeper, monkeypatch):
    from sleeper_mcp import playoffs as pl
    from sleeper_mcp.playoffs import playoff_odds
    real = pl.rest

    async def flaky(path):
        if path.endswith("/matchups/2"):
            raise RuntimeError("503")
        return await real(path)
    monkeypatch.setattr(pl, "rest", flaky)
    out = await call(playoff_odds, trials=50)
    assert "Week(s) [2] could not be fetched" in out


async def test_matchup_odds_use_this_weeks_projections_when_a_token_exists(sleeper):
    from sleeper_mcp.playoffs import matchup_odds
    out = await call(matchup_odds)
    assert "from this week's projections" in out and "(101 vs 88)" in out


async def test_matchup_odds_fall_back_to_strength_without_a_token(sleeper, monkeypatch):
    fake.no_token(monkeypatch)
    from sleeper_mcp.playoffs import matchup_odds
    out = await call(matchup_odds)
    assert "season strength (projections need SLEEPER_TOKEN)" in out


async def test_schedule_strength_names_the_week_span(sleeper):
    from sleeper_mcp.playoffs import schedule_strength
    out = await call(schedule_strength)
    assert "weeks 5-14" in out


async def test_consolation_bracket_labels_the_real_placing(sleeper):
    """p:1 in the losers' bracket is 3rd place in a 2-team playoff, not the
    championship."""
    from sleeper_mcp.playoffs import playoff_bracket
    out = await call(playoff_bracket, consolation=True)
    assert "[3rd place]" in out and "championship]" not in out


async def test_standings_trend_blames_the_token_not_sleeper(sleeper, monkeypatch):
    fake.no_token(monkeypatch)
    from sleeper_mcp.client import ConfigError
    from sleeper_mcp.playoffs import standings_trend
    with pytest.raises(ConfigError):
        await call(standings_trend)


async def test_standings_trend_reports_a_partial_fetch(sleeper):
    from sleeper_mcp.playoffs import standings_trend
    fake.fail_next("roster_standings")
    out = await call(standings_trend)
    assert "INCOMPLETE" in out and "form" in out
