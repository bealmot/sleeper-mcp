"""Every write tool: refusals, the dry run, the send, and the verification.

The riskiest module in the package. These run against the stateful fake, so
the send+verify paths execute and the exact variables each mutation carries
are pinned — the part that reaches a real person's league and cannot be tried
live.
"""

import pytest

pytest.importorskip("httpx")

from tests import fake                                        # noqa: E402

LINEUP = ["Ant Quarterback", "Bee Runner", "Doe Catcher", "Gnu Tight",
          "Jay Runner", "Hen Kicker", "Varks"]


@pytest.fixture
def sleeper(monkeypatch):
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def writes(monkeypatch):
    fake.install(monkeypatch)
    fake.writes_on(monkeypatch)
    return fake


async def call(tool, **kw):
    return await getattr(tool, "fn", tool)(**kw)


def sent(op):
    return [v for o, v in fake.SENT if o == op]


# --- set_lineup ----------------------------------------------------------------

async def test_set_lineup_dry_runs_by_default(sleeper):
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=LINEUP)
    assert "DRY RUN" in out and "Ant Quarterback" in out and not sent("update_matchup_leg")


async def test_the_def_slot_resolves_to_the_rosters_defence_by_any_name(sleeper):
    """Real defences have no full_name. 'Varks', 'Aard' and 'AAA' must all
    resolve to the roster's DEF — and NOTHING is passed through as an id."""
    from sleeper_mcp.writes import set_lineup
    for spelling in ("Varks", "Aard", "AAA", "aaa", "Aard Varks"):
        out = await call(set_lineup, players_in_slot_order=LINEUP[:-1] + [spelling])
        assert "DRY RUN" in out and "Aard Varks" in out, spelling


async def test_a_defence_you_do_not_own_is_refused(sleeper):
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=LINEUP[:-1] + ["Bees"])
    assert "Refused" in out and "not on your active roster" in out


async def test_a_short_uppercase_string_is_not_passed_through_as_an_id(sleeper):
    """The old heuristic sent any <=4-char uppercase token verbatim."""
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=["XYZ"] + LINEUP[1:])
    assert "Refused" in out


async def test_a_player_on_ir_is_refused_with_the_reason(sleeper):
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=
                     ["Ant Quarterback", "Bee Runner", "Moth Catcher", "Gnu Tight",
                      "Jay Runner", "Hen Kicker", "Varks"])
    assert "Refused" in out and "on IR" in out and "set_ir" in out


async def test_a_player_in_an_ineligible_slot_is_refused(sleeper):
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=
                     ["Bee Runner", "Ant Quarterback", "Doe Catcher", "Gnu Tight",
                      "Jay Runner", "Hen Kicker", "Varks"])
    assert "Refused" in out and "cannot fill the QB slot" in out


async def test_a_two_way_player_may_fill_the_slot_his_fantasy_positions_allow(sleeper):
    """Kit Twoway is listed at DB, eligible at WR and therefore FLEX."""
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=
                     ["Ant Quarterback", "Bee Runner", "Kit Twoway", "Gnu Tight",
                      "Doe Catcher", "Hen Kicker", "Varks"])
    assert "DRY RUN" in out


async def test_the_same_player_in_two_slots_is_refused(sleeper):
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=
                     ["Ant Quarterback", "Bee Runner", "Doe Catcher", "Gnu Tight",
                      "Bee Runner", "Hen Kicker", "Varks"])
    assert "Refused" in out and "two slots" in out


async def test_the_dry_run_marks_which_slots_change(sleeper):
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=
                     ["Ant Quarterback", "Bee Runner", "Doe Catcher", "Gnu Tight",
                      "Kit Twoway", "Hen Kicker", "Varks"])
    assert "1 slot(s) change" in out and "Kit Twoway   *" in out


async def test_confirming_still_refuses_while_writes_are_off(sleeper):
    """TWO gates, not one. confirm=True is the user's intent; the environment
    switch is the account holder's, and passing one must not pass the other."""
    from sleeper_mcp.client import WritesDisabled
    from sleeper_mcp.writes import set_lineup
    with pytest.raises(WritesDisabled):
        await call(set_lineup, players_in_slot_order=LINEUP, confirm=True)
    assert not fake.SENT


async def test_set_lineup_sends_update_matchup_leg_and_verifies(writes):
    """The RIGHT mutation, with ids not names, verified through matchup_legs."""
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=LINEUP, confirm=True)
    assert out.startswith("VERIFIED")
    (v,) = sent("update_matchup_leg")
    assert v["s"] == ["qb1", "rb1", "wr1", "te1", "rb3", "k1", "AAA"]
    assert v["r"] == v["leg"] == fake.WEEK and v["rid"] == 1
    assert not sent("roster_update_starters")


async def test_a_failed_verification_says_sent_not_failed(writes):
    from sleeper_mcp.writes import set_lineup
    fake.fail_next("matchup_legs")
    out = await call(set_lineup, players_in_slot_order=LINEUP, confirm=True)
    assert "WRITE SENT, VERIFICATION FAILED" in out and sent("update_matchup_leg")


async def test_a_timeout_on_send_reports_unknown_not_failed(writes, monkeypatch):
    from sleeper_mcp import writes as w
    from sleeper_mcp.client import TransportFailure
    import httpx

    async def boom(*a, **k):
        raise TransportFailure("update_matchup_leg", httpx.ReadTimeout("x"))
    monkeypatch.setattr(w, "gql", boom)
    from sleeper_mcp.writes import set_lineup
    out = await call(set_lineup, players_in_slot_order=LINEUP, confirm=True)
    assert out.startswith("SENT? UNKNOWN") and "Do NOT simply retry" in out


# --- waiver_claim -------------------------------------------------------------

async def test_waiver_claim_dry_runs_and_shows_waiver_status(sleeper):
    from sleeper_mcp.writes import waiver_claim
    out = await call(waiver_claim, add_player="Fox Catcher", drop_player="Jay Runner",
                     bid=5)
    assert "DRY RUN" in out and "ON WAIVERS until" in out and "$89 of $100" in out


async def test_a_free_agent_is_labelled_as_one(sleeper):
    from sleeper_mcp.writes import waiver_claim
    out = await call(waiver_claim, add_player="Owl Quarterback", drop_player="Jay Runner")
    assert "(free agent)" in out


async def test_a_claim_can_be_a_defence_by_nickname(sleeper):
    from sleeper_mcp.writes import waiver_claim
    out = await call(waiver_claim, add_player="Bees", drop_player="Varks")
    assert "DRY RUN" in out and "ADD   Bumble Bees" in out


async def test_a_claim_with_an_open_spot_needs_no_drop(sleeper, monkeypatch):
    from sleeper_mcp.writes import waiver_claim
    monkeypatch.setitem(fake.LEAGUE_CFG, "roster_positions",
                        fake.LEAGUE_CFG["roster_positions"] + ["BN", "BN"])
    from sleeper_mcp import client
    client.cache_clear()
    out = await call(waiver_claim, add_player="Fox Catcher")
    assert "DRY RUN" in out and "open roster spot" in out


async def test_a_full_roster_refuses_a_claim_without_a_drop(sleeper):
    from sleeper_mcp.writes import waiver_claim
    out = await call(waiver_claim, add_player="Fox Catcher")
    assert "Refused" in out and "roster is full" in out


async def test_a_bid_over_the_remaining_budget_is_refused(sleeper):
    from sleeper_mcp.writes import waiver_claim
    out = await call(waiver_claim, add_player="Fox Catcher", drop_player="Jay Runner",
                     bid=95)
    assert "Refused" in out and "exceeds your remaining FAAB" in out


async def test_an_existing_claim_on_the_same_player_is_flagged(sleeper):
    """The fake's pending list already holds a claim on Fox Catcher (t3)."""
    from sleeper_mcp.writes import waiver_claim
    out = await call(waiver_claim, add_player="Fox Catcher", drop_player="Jay Runner")
    assert "already have a claim" in out and "id=t3" in out and "$12" in out


async def test_waiver_claim_sends_parallel_arrays_and_verifies_via_graphql(writes):
    from sleeper_mcp.writes import waiver_claim
    out = await call(waiver_claim, add_player="Owl Quarterback",
                     drop_player="Jay Runner", bid=3, confirm=True)
    assert out.startswith("VERIFIED — queued") and "cancel_claim" in out
    (v,) = sent("submit_waiver_claim")
    assert v["ka"] == ["qb2"] and v["va"] == [1]
    assert v["kd"] == ["rb3"] and v["vd"] == [1]
    assert v["ks"] == ["waiver_bid"] and v["vs"] == [3]


async def test_a_no_drop_claim_sends_empty_drop_arrays(writes, monkeypatch):
    from sleeper_mcp.writes import waiver_claim
    monkeypatch.setitem(fake.LEAGUE_CFG, "roster_positions",
                        fake.LEAGUE_CFG["roster_positions"] + ["BN", "BN"])
    from sleeper_mcp import client
    client.cache_clear()
    await call(waiver_claim, add_player="Owl Quarterback", confirm=True)
    (v,) = sent("submit_waiver_claim")
    assert v["kd"] == [] and v["vd"] == []


# --- cancel_claim / change_bid -----------------------------------------------

async def test_cancel_claim_dry_run_shows_the_claim(sleeper):
    from sleeper_mcp.writes import cancel_claim
    out = await call(cancel_claim, transaction_id="t3")
    assert "DRY RUN" in out and "Fox Catcher" in out and "bid $12" in out


async def test_cancel_claim_refuses_an_unknown_or_wrong_kind_of_id(sleeper):
    from sleeper_mcp.writes import cancel_claim
    assert "no pending transaction" in await call(cancel_claim, transaction_id="zzz")
    # t1 is a completed trade, not pending
    assert "no pending transaction" in await call(cancel_claim, transaction_id="t1")


async def test_cancel_claim_sends_and_verifies(writes):
    from sleeper_mcp.writes import cancel_claim
    out = await call(cancel_claim, transaction_id="t3", confirm=True)
    assert out.startswith("VERIFIED — cancelled")
    (v,) = sent("cancel_waiver_claim")
    assert v["tx"] == "t3" and v["leg"] == fake.WEEK


async def test_change_bid_adjusts_in_place(writes):
    from sleeper_mcp.writes import change_bid
    out = await call(change_bid, transaction_id="t3", bid=20)
    assert "DRY RUN" in out and "$12 -> $20" in out
    # the fake only mutates claims it created, so submit one and change it
    from sleeper_mcp.writes import waiver_claim
    await call(waiver_claim, add_player="Owl Quarterback", drop_player="Jay Runner",
               bid=3, confirm=True)
    tid = sent("submit_waiver_claim") and fake.STATE["claims"][-1]["transaction_id"]
    out = await call(change_bid, transaction_id=tid, bid=9, confirm=True)
    assert out.startswith("VERIFIED") and "$9" in out


# --- set_ir ------------------------------------------------------------------------

async def test_set_ir_dry_runs(sleeper):
    from sleeper_mcp.writes import set_ir
    assert "DRY RUN" in await call(set_ir, player_names=["Moth Catcher"])


async def test_clearing_ir_is_possible_and_still_dry_runs(sleeper):
    """An empty list must mean 'clear', not 'no argument given'."""
    from sleeper_mcp.writes import set_ir
    out = await call(set_ir, player_names=[])
    assert "DRY RUN" in out and "IR after: (empty)" in out


async def test_set_ir_refuses_more_than_the_league_allows(sleeper):
    from sleeper_mcp.writes import set_ir
    out = await call(set_ir, player_names=["Moth Catcher", "Bee Runner"])
    assert "Refused" in out and "1 IR slot" in out


async def test_set_ir_warns_about_a_healthy_starter(sleeper):
    from sleeper_mcp.writes import set_ir
    out = await call(set_ir, player_names=["Bee Runner"])
    assert "WARN" in out and "healthy" in out and "current starter" in out


async def test_set_ir_verifies_over_graphql_never_rest(writes, monkeypatch):
    from sleeper_mcp import writes as w
    from sleeper_mcp.writes import set_ir
    calls = []
    real = w.rest

    async def spy(path):
        calls.append(path)
        return await real(path)
    monkeypatch.setattr(w, "rest", spy)
    out = await call(set_ir, player_names=[], confirm=True)
    assert out.startswith("VERIFIED") and "via league_rosters" in out
    assert (sent("roster_update_reserve")[0]["r"]) == []
    # the only REST read is the pre-write roster fetch; none after the send
    assert calls == ["/league/111/rosters"]


# --- trade_block ------------------------------------------------------------------

async def test_trade_block_reads_from_league_players(sleeper):
    """The block lives in league_players.settings.otb, not on the roster."""
    from sleeper_mcp.writes import trade_block
    out = await call(trade_block)
    assert "Jay Runner" in out and "listed 20" in out


async def test_trade_block_add_selects_a_field_that_exists_and_verifies(writes):
    from sleeper_mcp.writes import trade_block
    out = await call(trade_block, add=["Doe Catcher"], remove=["Jay Runner"], confirm=True)
    assert out.startswith("VERIFIED")
    assert "Doe Catcher" in out and "Jay Runner" not in out.split("ADD")[0]
    ops = [o for o, _ in fake.SENT]
    assert ops == ["add_league_player_trade_block", "remove_league_player_trade_block"]
    assert "{player_id settings}" in fake.SENT[0][1]


async def test_trade_block_remove_of_a_player_not_on_it_is_refused(sleeper):
    from sleeper_mcp.writes import trade_block
    out = await call(trade_block, remove=["Doe Catcher"])
    assert "Refused" in out and "not on your trade block" in out


# --- propose_trade ------------------------------------------------------------------

async def test_propose_trade_dry_run_names_both_sides(sleeper):
    from sleeper_mcp.writes import propose_trade
    out = await call(propose_trade, give_players=["Jay Runner"],
                     receive_players=["Cat Runner"], with_manager="Birch")
    assert "DRY RUN" in out and "YOU GIVE Jay Runner" in out and "YOU GET  Cat Runner" in out
    assert "roster 2" in out


async def test_propose_trade_matches_a_manager_by_roster_id(sleeper):
    from sleeper_mcp.writes import propose_trade
    out = await call(propose_trade, give_players=["Jay Runner"],
                     receive_players=["Cat Runner"], with_manager="2")
    assert "DRY RUN" in out


async def test_propose_trade_refuses_yourself_and_unknown_managers(sleeper):
    from sleeper_mcp.writes import propose_trade
    assert "that is you" in await call(propose_trade, give_players=["Jay Runner"],
                                       receive_players=["Cat Runner"], with_manager="Alder")
    out = await call(propose_trade, give_players=["Jay Runner"],
                     receive_players=["Cat Runner"], with_manager="Nobody")
    assert "matched 0" in out and "Birch (roster 2)" in out


async def test_propose_trade_encodes_the_whole_transaction_like_sleeper_does(writes):
    """Every player in BOTH maps: adds keyed by the receiving roster, drops by
    the roster that holds him. FAAB as 'sender,receiver,amount'."""
    from sleeper_mcp.writes import propose_trade
    out = await call(propose_trade, give_players=["Jay Runner", "Gnu Tight"],
                     receive_players=["Cat Runner"], with_manager="Birch",
                     faab=4, confirm=True)
    assert out.startswith("VERIFIED — offered")
    (v,) = sent("propose_trade")
    assert v["ka"] == ["rb2", "rb3", "te1"] and v["va"] == [1, 2, 2]
    assert v["kd"] == ["rb2", "rb3", "te1"] and v["vd"] == [2, 1, 1]
    assert v["wb"] == ["1,2,4"]


async def test_propose_trade_refuses_when_trades_are_disabled(sleeper, monkeypatch):
    from sleeper_mcp.writes import propose_trade
    monkeypatch.setitem(fake.LEAGUE_CFG["settings"], "disable_trades", 1)
    from sleeper_mcp import client
    client.cache_clear()
    out = await call(propose_trade, give_players=["Jay Runner"],
                     receive_players=["Cat Runner"], with_manager="Birch")
    assert "trades DISABLED" in out


# --- respond_trade --------------------------------------------------------------------

async def test_respond_trade_refuses_a_response_it_does_not_understand(sleeper):
    from sleeper_mcp.writes import respond_trade
    out = await call(respond_trade, transaction_id="t1", response="maybe")
    assert "Refused" in out


async def test_respond_trade_dry_run_shows_what_changes_hands(writes):
    """Propose first so a pending trade exists, then look at it."""
    from sleeper_mcp.writes import propose_trade, respond_trade
    await call(propose_trade, give_players=["Jay Runner"], receive_players=["Cat Runner"],
               with_manager="Birch", confirm=True)
    tid = fake.STATE["claims"][-1]["transaction_id"]
    out = await call(respond_trade, transaction_id=tid, response="accept")
    assert "DRY RUN" in out and "gets" in out and "Cat Runner" in out and "Jay Runner" in out


async def test_respond_trade_refuses_a_non_pending_id(sleeper):
    from sleeper_mcp.writes import respond_trade
    out = await call(respond_trade, transaction_id="t1", response="accept")
    assert "no pending transaction" in out


async def test_respond_trade_sends_and_verifies(writes):
    from sleeper_mcp.writes import propose_trade, respond_trade
    await call(propose_trade, give_players=["Jay Runner"], receive_players=["Cat Runner"],
               with_manager="Birch", confirm=True)
    tid = fake.STATE["claims"][-1]["transaction_id"]
    out = await call(respond_trade, transaction_id=tid, response="reject", confirm=True)
    assert out.startswith("VERIFIED") and "REJECTED" in out
    assert sent("reject_trade")[0]["tx"] == tid


# --- pickem_pick ------------------------------------------------------------------------

async def test_pickem_pick_dry_run_reads_the_right_weeks_leg_with_auth(sleeper):
    """legs[0] is week 4; the current week is 5. The pre-read must find the
    week-5 leg and it must be sent with a token."""
    from sleeper_mcp.writes import pickem_pick
    out = await call(pickem_pick, game_id="g51", team="BBB")
    assert "DRY RUN" in out and "(currently AAA)" in out


async def test_pickem_pick_refuses_a_week_with_no_leg(sleeper):
    from sleeper_mcp.writes import pickem_pick
    out = await call(pickem_pick, game_id="g91", team="BBB", week=9)
    assert "Refused" in out and "no pick'em leg for week 9" in out


async def test_pickem_pick_sends_pick_to_replace_and_verifies(writes):
    from sleeper_mcp.writes import pickem_pick
    out = await call(pickem_pick, game_id="g51", team="bbb", confirm=True)
    assert out.startswith("VERIFIED") and "is now BBB" in out
    (v,) = sent("make_pickem_pick")
    assert v["leg"] == "v1:regular:5" and v["old"]["team"] == "AAA"
    assert v["p"] == {"game_id": "g51", "team": "BBB", "outcome": "win"}


# --- watch_player -------------------------------------------------------------------------

async def test_watch_player_dry_runs_by_default(sleeper):
    from sleeper_mcp.writes import watch_player
    out = await call(watch_player, player_name="Fox Catcher")
    assert "DRY RUN" in out and not fake.SENT


async def test_watch_player_refuses_while_writes_are_off_only_after_the_dry_run(sleeper):
    from sleeper_mcp.client import WritesDisabled
    from sleeper_mcp.writes import watch_player
    with pytest.raises(WritesDisabled):
        await call(watch_player, player_name="Fox Catcher", confirm=True)


async def test_watch_and_unwatch_use_their_different_return_shapes(writes):
    from sleeper_mcp.writes import watch_player
    out = await call(watch_player, player_name="Fox Catcher", confirm=True)
    assert out.startswith("VERIFIED") and "holds 1" in out
    out = await call(watch_player, player_name="Fox Catcher", unwatch=True, confirm=True)
    assert out.startswith("VERIFIED") and "holds 0" in out
    assert "{player_id}" in fake.SENT[0][1] and "{player_id}" not in fake.SENT[1][1]
