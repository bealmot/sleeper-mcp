"""waiver_targets and bye_outlook against the fake league."""

import pytest

pytest.importorskip("httpx")

from tests import fake                                        # noqa: E402


@pytest.fixture
def sleeper(monkeypatch):
    fake.install(monkeypatch)
    return fake


async def call(tool, **kw):
    return await getattr(tool, "fn", tool)(**kw)


async def test_waiver_targets_prices_every_free_agent_and_says_how_many(sleeper):
    from sleeper_mcp.lineups import waiver_targets
    out = await call(waiver_targets)
    # free and signed: qb2, wr3, BBB. wr_fa has no team; fb1/wr2 are owned.
    assert "3 of 3 free agents have a projection" in out


async def test_waiver_targets_finds_the_quarterback_upgrade_and_labels_status(sleeper):
    """qb2 projects 27.5 over qb1's 21.0: +6.5, and he is FREE. wr3 (11.0)
    beats rb3 in the FLEX (4.5) and is ON WAIVERS."""
    from sleeper_mcp.lineups import waiver_targets
    out = await call(waiver_targets)
    assert "+  6.5  QB   Owl Quarterback" in out and "FREE" in out
    assert "Fox Catcher" in out and "ON WAIVERS until" in out
    assert "FAAB: $89 of $100 left" in out


async def test_waiver_targets_no_cap(sleeper, monkeypatch):
    """700 free agents must ALL be priced — the old loop stopped at 600."""
    from sleeper_mcp.lineups import waiver_targets
    extra = {f"x{i}": {"full_name": f"Extra {i}", "position": "K",
                       "fantasy_positions": ["K"], "team": "CCC"} for i in range(700)}
    monkeypatch.setattr(fake, "PLAYERS", {**fake.PLAYERS, **extra})
    for k in extra:
        fake.PROJ[k] = {"fgm": 1}
    try:
        out = await call(waiver_targets)
    finally:
        for k in extra:
            fake.PROJ.pop(k, None)
    assert "of 703 free agents" in out and "703 of 703" in out


async def test_waiver_targets_validates_the_position(sleeper):
    from sleeper_mcp.lineups import waiver_targets
    assert "position must be one of" in await call(waiver_targets, position="FLEX")


async def test_a_taxi_player_is_not_priced_as_startable(sleeper, monkeypatch):
    """With qb1 on the taxi squad the base lineup has no QB, so qb2 is worth
    his whole projection, not the 6.5 difference."""
    from sleeper_mcp.lineups import waiver_targets
    monkeypatch.setitem(fake.ROSTERS[0], "taxi", ["qb1"])
    out = await call(waiver_targets, position="QB")
    assert "+ 27.5  QB   Owl Quarterback" in out


async def test_bye_outlook_distinguishes_byes_from_unknowns(sleeper):
    """Week 6: CCC on bye (Jay Runner, Kit Twoway). Week 7: AAA plays but
    Doe Catcher has no projection -> UNKNOWN, not a bye."""
    from sleeper_mcp.lineups import bye_outlook
    out = await call(bye_outlook, through_week=8)
    wk6 = next(l for l in out.splitlines() if l.strip().startswith("6 "))
    wk7 = next(l for l in out.splitlines() if l.strip().startswith("7 "))
    assert "Runner" in wk6 and "Twoway" in wk6 and "UNKNOWN" not in wk6
    assert "UNKNOWN: Catcher" in wk7
    assert "UNKNOWN in week(s) [7]" in out
    assert "none did here" not in out


async def test_bye_outlook_names_an_unfillable_week(sleeper, monkeypatch):
    """Take away the kicker: no week can fill K."""
    from sleeper_mcp.lineups import bye_outlook
    monkeypatch.setitem(fake.ROSTERS[0], "players",
                        [p for p in fake.ROSTERS[0]["players"] if p != "k1"])
    out = await call(bye_outlook, through_week=6)
    assert "cannot fill a legal lineup: [5, 6]" in out and "K " in out


async def test_a_zero_projection_defence_still_fills_its_slot(sleeper, monkeypatch):
    from sleeper_mcp.lineups import bye_outlook
    monkeypatch.setitem(fake.PROJ, "AAA", {"sack": 0})
    out = await call(bye_outlook, through_week=5)
    assert "Every projected week can field a legal lineup" in out
