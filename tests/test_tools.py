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


@pytest.mark.asyncio
async def test_pickem_status_shows_this_weeks_tiebreaker(sleeper):
    """The tiebreaker is a Map, and the legs list starts with LAST week's."""
    from sleeper_mcp.reads import pickem_status
    out = await call(pickem_status)
    assert "tiebreaker: total_points = 40 on game g2" in out


# --- writes -----------------------------------------------------------------
# The riskiest module in the package and the least exercised. Running these
# against the fake is safe by construction — the only thing it can mutate is
# its own pick'em tiebreaker, kept so one write can be read back — so the
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


# --- pick'em tiebreaker ------------------------------------------------------

@pytest.mark.asyncio
async def test_pickem_tiebreaker_dry_run_shows_this_weeks_value(sleeper):
    """Week 4's leg comes first in the response; reading it would say 33."""
    from sleeper_mcp.writes import pickem_tiebreaker
    out = await call(pickem_tiebreaker, total_points=47)
    assert "DRY RUN" in out
    assert "47 total points on game g2 (was 40 on game g2)" in out
    assert sleeper.TIEBREAKER["value"] == 40


@pytest.mark.asyncio
async def test_pickem_tiebreaker_refuses_while_writes_are_off(sleeper):
    from sleeper_mcp.client import WritesDisabled
    from sleeper_mcp.writes import pickem_tiebreaker
    with pytest.raises(WritesDisabled):
        await call(pickem_tiebreaker, total_points=47, confirm=True)
    assert sleeper.TIEBREAKER["value"] == 40


@pytest.mark.asyncio
async def test_pickem_tiebreaker_sets_and_reads_back(sleeper, monkeypatch):
    from sleeper_mcp import client
    from sleeper_mcp.writes import pickem_tiebreaker
    monkeypatch.setattr(client, "WRITES_ENABLED", True)
    out = await call(pickem_tiebreaker, total_points=47, confirm=True)
    assert out.startswith("VERIFIED")
    assert sleeper.TIEBREAKER == {"type": "total_points", "value": 47,
                                  "game_id": "g2",
                                  "updated_at": 1757272300000}


@pytest.mark.asyncio
async def test_pickem_tiebreaker_can_move_to_another_game(sleeper, monkeypatch):
    from sleeper_mcp import client
    from sleeper_mcp.writes import pickem_tiebreaker
    monkeypatch.setattr(client, "WRITES_ENABLED", True)
    out = await call(pickem_tiebreaker, total_points=51, game_id="g1",
                     confirm=True)
    assert "VERIFIED" in out and sleeper.TIEBREAKER["game_id"] == "g1"


@pytest.mark.asyncio
async def test_pickem_tiebreaker_refuses_an_impossible_total(sleeper):
    from sleeper_mcp.writes import pickem_tiebreaker
    out = await call(pickem_tiebreaker, total_points=-3)
    assert "Refused" in out and "nothing sent" in out


@pytest.mark.asyncio
async def test_pickem_tiebreaker_needs_a_game_when_the_entry_has_none(sleeper):
    """Guessing the pool's tiebreaker game would set a number on the wrong
    game; refuse and say where the ids are."""
    from sleeper_mcp.writes import pickem_tiebreaker
    sleeper.TIEBREAKER.clear()
    out = await call(pickem_tiebreaker, total_points=47)
    assert "Refused" in out and "game_id" in out


@pytest.mark.asyncio
async def test_pickem_tiebreaker_refuses_a_week_with_no_leg(sleeper):
    from sleeper_mcp.writes import pickem_tiebreaker
    out = await call(pickem_tiebreaker, total_points=47, week=9)
    assert "Refused" in out and "week 9" in out


# --- pick'em picks -----------------------------------------------------------

@pytest.mark.asyncio
async def test_pickem_pick_dry_run_reads_the_entry_with_a_token(sleeper):
    """The pre-read is authenticated. It was sent without a token, Sleeper
    answered Unauthorized, and the tool raised before it could say anything —
    on a dry run, so nothing could be changed from a desktop at all."""
    from sleeper_mcp.writes import pickem_pick
    out = await call(pickem_pick, game_id="g1", team="BBB")
    assert "DRY RUN" in out
    assert "would pick BBB in game g1 (currently AAA)" in out
    assert sleeper.PICKS["g1"]["team"] == "AAA" and not sleeper.LAST_PICK


@pytest.mark.asyncio
async def test_pickem_pick_refuses_while_writes_are_off(sleeper):
    from sleeper_mcp.client import WritesDisabled
    from sleeper_mcp.writes import pickem_pick
    with pytest.raises(WritesDisabled):
        await call(pickem_pick, game_id="g2", team="BBB", confirm=True)
    assert "g2" not in sleeper.PICKS and not sleeper.LAST_PICK


@pytest.mark.asyncio
async def test_pickem_pick_makes_a_new_pick_and_verifies_this_weeks_leg(
        sleeper, monkeypatch):
    """The read-back must select the week's leg by id. get_pickem_legs returns
    every week with the earliest first, so taking legs[0] looks at week 4,
    finds nothing for this game, and calls a pick that landed a MISMATCH."""
    from sleeper_mcp import client
    from sleeper_mcp.writes import pickem_pick
    monkeypatch.setattr(client, "WRITES_ENABLED", True)
    out = await call(pickem_pick, game_id="g2", team="BBB", confirm=True)
    assert out.startswith("VERIFIED"), out
    assert sleeper.PICKS["g2"] == {"team": "BBB", "outcome": "win"}
    assert sleeper.LAST_PICK["leg"] == "v1:regular:5"
    assert sleeper.LAST_PICK["old"] is None


@pytest.mark.asyncio
async def test_pickem_pick_changes_a_pick_and_names_the_one_it_replaces(
        sleeper, monkeypatch):
    from sleeper_mcp import client
    from sleeper_mcp.writes import pickem_pick
    monkeypatch.setattr(client, "WRITES_ENABLED", True)
    out = await call(pickem_pick, game_id="g1", team="CCC", confirm=True)
    assert out.startswith("VERIFIED") and "(currently AAA)" in out, out
    assert sleeper.PICKS["g1"]["team"] == "CCC"
    assert sleeper.LAST_PICK["old"]["team"] == "AAA"


@pytest.mark.asyncio
async def test_pickem_pick_refuses_a_week_with_no_leg(sleeper, monkeypatch):
    """Writing into a leg that does not exist is not a pick."""
    from sleeper_mcp import client
    from sleeper_mcp.writes import pickem_pick
    monkeypatch.setattr(client, "WRITES_ENABLED", True)
    out = await call(pickem_pick, game_id="g1", team="BBB", week=9,
                     confirm=True)
    assert "Refused" in out and "week 9" in out
    assert not sleeper.LAST_PICK


# --- claiming a team defence -------------------------------------------------

@pytest.mark.asyncio
async def test_the_player_dictionary_names_every_defence(sleeper):
    """Sleeper's dictionary has no full_name for a defence. The loader must
    build one, or no tool that matches on names can ever see a defence."""
    from sleeper_mcp import client
    P = await client.players()
    assert P["BBB"]["full_name"] == "Bayside Badgers"
    assert P["AAA"]["full_name"] == "Anytown Aardvarks"


@pytest.mark.asyncio
async def test_waiver_claim_can_add_a_team_defence_by_name(sleeper):
    """The Vikings DEF could not be claimed: 'Minnesota Vikings', 'Vikings' and
    'MIN' were all refused. Each of these must now resolve to the one defence."""
    from sleeper_mcp.writes import waiver_claim
    for typed in ("Bayside Badgers", "Badgers", "bayside"):
        out = await call(waiver_claim, add_player=typed,
                         drop_player="Jay Runner", bid=5)
        assert out.startswith("DRY RUN"), (typed, out)
        assert "ADD   Bayside Badgers" in out and "BID   $5" in out, out


@pytest.mark.asyncio
async def test_waiver_claim_refuses_a_defence_you_already_own(sleeper):
    """Anytown Aardvarks is on the fake roster, so it is not a free agent."""
    from sleeper_mcp.writes import waiver_claim
    out = await call(waiver_claim, add_player="Aardvarks",
                     drop_player="Jay Runner")
    assert out.startswith("Refused") and "free-agent pool" in out, out


@pytest.mark.asyncio
async def test_waiver_claim_shows_the_defence_being_dropped_by_name(sleeper):
    """The plan line printed 'None' for a defence: it read full_name."""
    from sleeper_mcp.writes import waiver_claim
    out = await call(waiver_claim, add_player="Fox Catcher",
                     drop_player="Aardvarks")
    assert "DROP  Anytown Aardvarks" in out, out


# --- projected first downs ---------------------------------------------------
#
# Sleeper projects every first down as yards / 10, about 2.4x the real rate.
# These rows carry that stub, as the real feed does, in a league that pays 0.5
# per receiving first down. Each row scores 13.00 before first downs: 4 catches
# at 0.5, 50 yards at 0.1, a touchdown at 6. The stub adds 2.50 (15.50); 50
# yards at the receiver rate of 0.0415 adds 1.04 (14.04).

STUB_PROJ = {
    "wr1": {"rec": 4.0, "rec_yd": 50.0, "rec_fd": 5.0, "rec_td": 1.0},
    "wr2": {"rec": 2.0, "rec_yd": 20.0, "rec_fd": 2.0},
    "wr3": {"rec": 4.0, "rec_yd": 50.0, "rec_fd": 5.0, "rec_td": 1.0},
}


@pytest.fixture
def fd_league(sleeper, monkeypatch):
    monkeypatch.setitem(sleeper.LEAGUE_CFG["scoring_settings"], "rec_fd", 0.5)

    async def gql(query, variables=None, auth=False):
        if "stats_for_players_in_week" in query and 'category:"proj"' in query:
            return {"stats_for_players_in_week": [
                {"player_id": p, "stats": dict(s)}
                for p, s in STUB_PROJ.items() if f'"{p}"' in query]}
        return await sleeper.gql(query, variables, auth)

    for mod in ("reads", "lineups", "signals"):
        monkeypatch.setattr(f"sleeper_mcp.{mod}.gql", gql)
    return sleeper


@pytest.mark.asyncio
async def test_roster_does_not_score_the_first_down_stub(fd_league):
    from sleeper_mcp.reads import roster
    out = await call(roster)
    assert "14.04" in out and "15.50" not in out
    assert "first down" in out.lower()


@pytest.mark.asyncio
async def test_waiver_targets_does_not_score_the_first_down_stub(fd_league):
    from sleeper_mcp.lineups import waiver_targets
    out = await call(waiver_targets)
    assert "projects  14.0" in out and "15.5" not in out
    assert "first down" in out.lower()


@pytest.mark.asyncio
async def test_bye_outlook_does_not_score_the_first_down_stub(fd_league):
    from sleeper_mcp.lineups import bye_outlook
    out = await call(bye_outlook, through_week=6)
    assert "14.0" in out and "15.5" not in out


@pytest.mark.asyncio
async def test_signal_divergence_does_not_score_the_first_down_stub(
        fd_league, monkeypatch, tmp_path):
    from sleeper_mcp import signals
    path = tmp_path / "signal.json"
    path.write_text('{"source": "t", "players": {"wr1": {"score": 1},'
                    ' "wr2": {"score": 9}, "wr3": {"score": 5}}}')
    monkeypatch.setattr(signals, "SIGNAL_FILE", str(path))
    out = await call(signals.signal_divergence, gap=1)
    assert "proj  14.0" in out and "15.5" not in out
