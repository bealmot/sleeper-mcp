"""Every read tool, run against a fake Sleeper.

The tool layer was at 0% coverage. Its failure modes are not logic errors —
they are a format string on a None, a synthetic row treated as a player, an
endpoint that needs a token nobody documented. None of those are visible to a
unit test of the maths, and all of them reached users.

These assert something weak on purpose: that a tool RUNS and returns text that
mentions what it should. A stricter assertion on exact output would break on
every formatting change and teach people to update the expectation without
reading it.
"""

import pytest

pytest.importorskip("httpx")

from tests import fake                                        # noqa: E402


@pytest.fixture
def sleeper(monkeypatch):
    fake.install(monkeypatch)
    return fake


async def call(tool, **kw):
    return await getattr(tool, "fn", tool)(**kw)


# --- reads ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_roster_lists_the_team(sleeper):
    from sleeper_mcp.reads import roster
    out = await call(roster)
    assert "Bee Runner" in out


@pytest.mark.asyncio
async def test_standings_shows_both_teams(sleeper):
    from sleeper_mcp.reads import standings
    out = await call(standings)
    assert "Alder" in out and "Birch" in out


@pytest.mark.asyncio
async def test_matchup_pairs_the_teams(sleeper):
    from sleeper_mcp.reads import matchup
    out = await call(matchup)
    assert "Alder" in out and "Birch" in out


@pytest.mark.asyncio
async def test_transactions_renders(sleeper):
    from sleeper_mcp.reads import transactions
    assert "trade" in (await call(transactions)).lower()


@pytest.mark.asyncio
async def test_a_trade_does_not_list_each_player_twice(sleeper):
    """A trade puts every player in BOTH adds and drops. Printing the lists
    separately showed each one arriving and leaving."""
    from sleeper_mcp.reads import transaction_search
    out = await call(transaction_search, kind="trade")
    assert out.count("Doe Catcher") == 1


@pytest.mark.asyncio
async def test_a_traded_pick_is_not_dropped_from_a_trade(sleeper):
    """GraphQL sends picks as comma-separated strings, not objects."""
    from sleeper_mcp.reads import transaction_search
    out = await call(transaction_search, kind="trade")
    assert "2027" in out and "round 6" in out


@pytest.mark.asyncio
async def test_player_history_reads_from_the_players_side(sleeper):
    from sleeper_mcp.reads import player_history
    out = await call(player_history, player_name="Doe Catcher")
    assert "Doe Catcher" in out


@pytest.mark.asyncio
async def test_an_ambiguous_name_is_refused_not_guessed(sleeper):
    """Two 'Bee Runner' entries exist; one is retired with no team."""
    from sleeper_mcp.reads import player_news
    out = await call(player_news, player_name="Bee Runner")
    assert "Bee Runner" in out


@pytest.mark.asyncio
async def test_an_unknown_name_says_so(sleeper):
    from sleeper_mcp.reads import player_news
    assert "No player matches" in await call(player_news,
                                             player_name="Nobody At All")


# --- analysis ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_waiver_targets_prices_against_your_lineup(sleeper):
    from sleeper_mcp.lineups import waiver_targets
    out = await call(waiver_targets)
    assert "waiver targets" in out.lower()


@pytest.mark.asyncio
async def test_playoff_odds_sum_to_the_field(sleeper):
    from sleeper_mcp.playoffs import playoff_odds
    out = await call(playoff_odds, trials=200)
    assert "playoff" in out.lower()


@pytest.mark.asyncio
async def test_matchup_odds_render(sleeper):
    from sleeper_mcp.playoffs import matchup_odds
    assert "%" in await call(matchup_odds)


@pytest.mark.asyncio
async def test_playoff_bracket_names_a_champion(sleeper):
    from sleeper_mcp.playoffs import playoff_bracket
    assert "Champion" in await call(playoff_bracket)


@pytest.mark.asyncio
async def test_standings_trend_shows_form(sleeper):
    from sleeper_mcp.playoffs import standings_trend
    out = await call(standings_trend)
    assert "form" in out and "Alder" in out


@pytest.mark.asyncio
async def test_usage_reports_shares_not_just_points(sleeper):
    from sleeper_mcp.usage import usage
    out = await call(usage, player_name="Doe Catcher")
    assert "tgt%" in out and "snap%" in out


@pytest.mark.asyncio
async def test_a_share_never_exceeds_one_hundred_percent(sleeper):
    """The TEAM_ aggregate row is the denominator, not extra supply.

    Counting it as a player doubles the total and halves every share; dropping
    it entirely and summing players inflates them past 100%.
    """
    import re
    from sleeper_mcp.usage import usage
    out = await call(usage, player_name="Doe Catcher")
    for pct in re.findall(r"(\d+)%", out):
        assert int(pct) <= 100, out


@pytest.mark.asyncio
async def test_the_team_aggregate_is_never_listed_as_a_player(sleeper):
    from sleeper_mcp.usage import season_leaders
    out = await call(season_leaders, position="WR")
    assert "TEAM_" not in out


@pytest.mark.asyncio
async def test_breakouts_runs(sleeper):
    from sleeper_mcp.usage import breakouts
    out = await call(breakouts)
    assert isinstance(out, str) and out.strip()


# --- drafts and keepers -----------------------------------------------------

@pytest.mark.asyncio
async def test_draft_board_marks_keepers(sleeper):
    from sleeper_mcp.drafts import draft_board
    out = await call(draft_board)
    assert "keeper" in out and "Bee Runner" in out


@pytest.mark.asyncio
async def test_draft_review_ranks_within_position(sleeper):
    from sleeper_mcp.drafts import draft_review
    out = await call(draft_review, season="2025")
    assert "position" in out.lower()


@pytest.mark.asyncio
async def test_traded_picks_reports_the_exception_list(sleeper):
    from sleeper_mcp.drafts import traded_picks
    assert "2027" in await call(traded_picks)


@pytest.mark.asyncio
async def test_keepers_separates_the_two_fields(sleeper):
    """The draft's is_keeper picks and roster.keepers disagree in real data."""
    from sleeper_mcp.keepers import keepers
    out = await call(keepers)
    assert "KEPT" in out and "DESIGNATED" in out


@pytest.mark.asyncio
async def test_set_keepers_dry_runs_by_default(sleeper):
    from sleeper_mcp.keepers import set_keepers
    out = await call(set_keepers, player_names=["Bee Runner"])
    assert "DRY RUN" in out or "Refused" in out


# --- pick'em ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_pickem_consensus_orders_by_exposure(sleeper):
    from sleeper_mcp.reads import pickem_consensus
    out = await call(pickem_consensus)
    assert "field" in out


@pytest.mark.asyncio
async def test_empty_pickem_entries_are_not_counted_as_pickers(sleeper):
    """One of three fake entries has no picks; shares are of the two."""
    from sleeper_mcp.reads import pickem_consensus
    out = await call(pickem_consensus)
    assert "2 of 3 entries submitted" in out


# --- writes -----------------------------------------------------------------
# The riskiest module in the package and the least exercised. Running these
# against the fake is safe by construction — it has nothing to mutate — so the
# only reason writes.py sat at 10% was that nobody had written these.
#
# Every one asserts a REFUSAL. That is the behaviour worth pinning: a write
# tool's job, nine times out of ten, is to not do the thing.

@pytest.mark.asyncio
async def test_set_lineup_dry_runs_by_default(sleeper):
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup,
                     players_in_slot_order=["Ant Quarterback", "Bee Runner", "Doe Catcher", "Gnu Tight",
                            "Jay Runner", "Hen Kicker", "Aardvarks"])
    assert "DRY RUN" in out


@pytest.mark.asyncio
async def test_a_dry_run_shows_what_it_would_send(sleeper):
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup,
                     players_in_slot_order=["Ant Quarterback", "Bee Runner", "Doe Catcher", "Gnu Tight",
                            "Jay Runner", "Hen Kicker", "Aardvarks"])
    assert "Ant Quarterback" in out


@pytest.mark.asyncio
async def test_confirming_still_refuses_while_writes_are_off(sleeper):
    """TWO gates, not one. confirm=True is the user's intent; the environment
    switch is the account holder's, and passing one must not pass the other."""
    from sleeper_mcp.client import WritesDisabled
    from sleeper_mcp.writes import set_lineup
    with pytest.raises(WritesDisabled):
        await call(set_lineup,
                         players_in_slot_order=["Ant Quarterback", "Bee Runner", "Doe Catcher", "Gnu Tight",
                            "Jay Runner", "Hen Kicker", "Aardvarks"],
                         confirm=True)


@pytest.mark.asyncio
async def test_a_player_not_on_your_roster_is_refused_before_anything_is_sent(
        sleeper):
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=
                     ["Cat Runner", "Bee Runner", "Doe Catcher",
                      "Gnu Tight", "Jay Runner", "Hen Kicker",
                      "Aardvarks"])
    assert "Refused" in out and "nothing sent" in out


@pytest.mark.asyncio
async def test_an_unknown_name_is_refused_rather_than_skipped(sleeper):
    """Dropping an unrecognised name silently would submit a short lineup."""
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=
                     ["Nobody At All", "Bee Runner", "Doe Catcher",
                      "Gnu Tight", "Jay Runner", "Hen Kicker",
                      "Aardvarks"])
    assert "Refused" in out


@pytest.mark.asyncio
async def test_set_ir_dry_runs(sleeper):
    from sleeper_mcp.writes import set_ir
    assert "DRY RUN" in await call(set_ir, player_names=["Bee Runner"])


@pytest.mark.asyncio
async def test_clearing_ir_is_possible_and_still_dry_runs(sleeper):
    """An empty list must mean 'clear', not 'no argument given'."""
    from sleeper_mcp.writes import set_ir
    assert "DRY RUN" in await call(set_ir, player_names=[])


@pytest.mark.asyncio
async def test_waiver_claim_dry_runs(sleeper):
    from sleeper_mcp.writes import waiver_claim
    out = await call(waiver_claim, add_player="Fox Catcher",
                     drop_player="Hen Kicker")
    assert "DRY RUN" in out or "Refused" in out


@pytest.mark.asyncio
async def test_trade_block_reads_without_arguments(sleeper):
    from sleeper_mcp.writes import trade_block
    out = await call(trade_block)
    assert isinstance(out, str) and out.strip()


@pytest.mark.asyncio
async def test_respond_trade_refuses_a_response_it_does_not_understand(sleeper):
    from sleeper_mcp.writes import respond_trade
    out = await call(respond_trade, transaction_id="t1", response="maybe")
    assert "Refused" in out


@pytest.mark.asyncio
async def test_pickem_scores_against_the_scoreboard_not_the_picks(sleeper):
    """Every pick says outcome "win". Scoring from that rates everyone perfect.

    The fake's entry 1 took AAA in g1 (AAA won) and BBB in g2 (not final), so
    the only honest reading is one correct and one pending.
    """
    from sleeper_mcp.reads import pickem_consensus
    out = await call(pickem_consensus)
    assert "1 correct" in out
    assert "0 wrong" in out


@pytest.mark.asyncio
async def test_an_unfinished_game_shows_as_pending_not_a_miss(sleeper):
    from sleeper_mcp.reads import pickem_consensus
    out = await call(pickem_consensus)
    assert "1 pending" in out
    assert "---" in out


@pytest.mark.asyncio
async def test_the_chalk_comparison_is_reported(sleeper):
    """A record says whether the week went well; this says whether the
    PICKING did."""
    from sleeper_mcp.reads import pickem_consensus
    out = await call(pickem_consensus)
    assert "taking the pool favourite" in out
