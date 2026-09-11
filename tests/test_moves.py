"""Transactions read from one player's point of view.

Fixtures are copied from real league_transactions_by_player responses, not
imagined — including the row where the player asked about is the corresponding
DROP and someone else is the point of the move. That row is the whole reason
this module exists, and it is the one a hand-written fixture leaves out.
"""

from sleeper_mcp.moves import (ADDED, DRAFTED, DROPPED, TRADED, churn,
                               classify, history, when)

# Real shapes. 9754 is the player asked about throughout.
CLAIMED = {"type": "waiver", "status": "complete", "leg": 2,
           "created": 1726520816107, "roster_ids": [5],
           "adds": {"9754": 5}, "drops": {"11625": 5}, "waiver_budget": None}
CUT = {"type": "free_agent", "status": "complete", "leg": 4,
       "created": 1727418823825, "roster_ids": [5],
       "adds": {"10226": 5}, "drops": {"9754": 5}, "waiver_budget": None}
DRAFT = {"type": "draft_pick", "status": "complete", "leg": 1,
         "created": 1725253340760, "roster_ids": [3],
         "adds": {"9754": 3}, "drops": None, "waiver_budget": None}
TRADE = {"type": "trade", "status": "complete", "leg": 3,
         "created": 1758644097060, "roster_ids": [5, 9],
         "adds": {"9754": 5, "12530": 9}, "drops": {"9754": 9, "12530": 5},
         "consenter_ids": [5, 9], "waiver_budget": None}
UNRELATED = {"type": "waiver", "status": "complete", "leg": 6,
             "created": 1730000000000, "roster_ids": [2],
             "adds": {"1111": 2}, "drops": {"2222": 2}}


def test_an_add_is_an_add():
    c = classify(CLAIMED, "9754")
    assert c["kind"] == ADDED and c["how"] == "waiver"
    assert c["to_roster"] == 5 and c["from_roster"] is None
    assert c["others"] == ["11625"]          # who he was picked up over


def test_the_row_where_he_is_merely_the_drop():
    """THE TRAP. This row's point is player 10226, not this one.

    Read as an acquisition it produces a history where a player is signed
    repeatedly and never released.
    """
    c = classify(CUT, "9754")
    assert c["kind"] == DROPPED
    assert c["from_roster"] == 5 and c["to_roster"] is None


def test_the_same_row_means_the_opposite_for_the_other_player():
    assert classify(CUT, "10226")["kind"] == ADDED
    assert classify(CUT, "9754")["kind"] == DROPPED


def test_a_draft_is_not_a_signing():
    assert classify(DRAFT, "9754")["kind"] == DRAFTED


def test_a_trade_is_both_directions_at_once():
    c = classify(TRADE, "9754")
    assert c["kind"] == TRADED
    assert c["from_roster"] == 9 and c["to_roster"] == 5
    assert c["others"] == ["12530"]          # what came back


def test_a_transaction_that_does_not_touch_him_is_dropped():
    assert classify(UNRELATED, "9754") is None


def test_history_is_newest_first_and_excludes_the_unrelated():
    h = history([DRAFT, CLAIMED, CUT, TRADE, UNRELATED], "9754")
    assert [m["kind"] for m in h] == [TRADED, DROPPED, ADDED, DRAFTED]


def test_timestamps_are_milliseconds_not_seconds():
    """Reading these as seconds puts every transaction in 1970."""
    assert when(1726520816107).startswith("2024-")
    assert when(None) == "?"


def test_churn_counts_teams_not_just_moves():
    """One manager churning a player differs from four teams trying him."""
    h = history([DRAFT, CLAIMED, CUT, TRADE], "9754")
    c = churn(h)
    assert c["moves"] == 4
    assert c["adds"] == 2 and c["drops"] == 1 and c["trades"] == 1
    assert c["rosters"] == 3                 # rosters 3, 5 and 9


def test_empty_history_is_not_a_crash():
    assert history([], "9754") == []
    assert history(None, "9754") == []
    assert churn([])["rosters"] == 0


# --- the season rollover ----------------------------------------------------
# Copied verbatim from a real response. At the start of a new season Sleeper
# releases the previous year's entire roster in ONE transaction typed
# `draft_pick`, with no adds and a dozen drops. Nobody writing a fixture by
# hand invents this, and read as an ordinary cut it renders as "dropped him for
# <three arbitrary team-mates>" under the heading "draft" — wrong about the
# reason, wrong about the other names, and incoherent about the type.

ROLLOVER = {
    "type": "draft_pick", "status": "complete", "leg": 1,
    "created": 1756875956415, "roster_ids": [2], "adds": None,
    "drops": {"11624": 2, "4046": 2, "4892": 2, "7553": 2, "8148": 2,
              "8151": 2, "8205": 2, "8220": 2, "8259": 2, "9226": 2,
              "9488": 2, "9754": 2, "SF": 2},
    "waiver_budget": None,
}


def test_a_roster_reset_is_not_a_considered_drop():
    c = classify(ROLLOVER, "9754")
    assert c["kind"] == DROPPED
    assert c["how"] == "roster reset"        # not "draft"
    assert c["bulk"] is True


def test_an_ordinary_cut_is_not_flagged_bulk():
    assert classify(CUT, "9754")["bulk"] is False
    assert classify(CLAIMED, "9754")["bulk"] is False


def test_a_team_defence_in_the_drop_list_does_not_break_anything():
    """Defences are keyed by team abbreviation, not a numeric id."""
    assert classify(ROLLOVER, "SF")["kind"] == DROPPED


def test_a_normal_draft_is_still_a_draft():
    c = classify(DRAFT, "9754")
    assert c["kind"] == DRAFTED and c["how"] == "draft"
