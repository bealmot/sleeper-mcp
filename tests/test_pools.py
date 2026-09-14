"""Pick'em consensus.

The book's shape is copied from a live pool: keyed by roster id as a STRING,
each entry holding `picks` (game_id -> {team, outcome, ...}) and a
`tiebreaker`. Two things in it mislead — `outcome` is not a result, and most
entries are empty.
"""

from sleeper_mcp.pools import (against_the_field, chalk_score, consensus,
                               entries, exposure, field_splits,
                               leaderboard, my_picks, score_entry,
                               winners)


def pick(team):
    # outcome is "win" on EVERY pick in a live pool, winners and losers alike.
    return {"team": team, "outcome": "win", "game_id": "g"}


BOOK = {
    "57": {"picks": {"g1": pick("CHI"), "g2": pick("HOU")}, "tiebreaker": 44},
    "58": {"picks": {"g1": pick("CHI"), "g2": pick("BUF")}, "tiebreaker": 41},
    "59": {"picks": {"g1": pick("CHI"), "g2": pick("BUF")}, "tiebreaker": 38},
    "60": {"picks": {"g1": pick("CAR"), "g2": pick("BUF")}, "tiebreaker": 50},
    "61": {"picks": {}, "tiebreaker": None},
    "62": {"picks": {}, "tiebreaker": None},
}


def test_empty_entries_are_excluded_from_the_denominator():
    """"89% of the pool" means 89% of the people who actually picked."""
    e = entries(BOOK)
    assert e == {"total": 6, "submitted": 4, "empty": 2}


def test_splits_count_each_side():
    s = field_splits(BOOK)
    assert s["g1"] == {"CHI": 3, "CAR": 1}
    assert s["g2"] == {"BUF": 3, "HOU": 1}


def test_my_picks_are_found_by_string_key():
    assert my_picks(BOOK, 57) == {"g1": "CHI", "g2": "HOU"}
    assert my_picks(BOOK, "57") == {"g1": "CHI", "g2": "HOU"}


def test_an_entry_with_no_picks_is_empty_not_an_error():
    assert my_picks(BOOK, 61) == {}
    assert my_picks(BOOK, 999) == {}


def test_consensus_reports_my_side_and_the_field():
    rows = {r["game_id"]: r for r in consensus(BOOK, 57)}
    g1 = rows["g1"]
    assert g1["favourite"] == "CHI" and abs(g1["favourite_share"] - 0.75) < 1e-9
    assert g1["my_pick"] == "CHI" and g1["with_field"] is True
    g2 = rows["g2"]
    assert g2["my_pick"] == "HOU" and g2["with_field"] is False
    assert abs(g2["share"] - 0.25) < 1e-9


def test_the_games_i_stand_alone_on_come_first():
    """Ordered by kickoff, the only games that matter are invisible."""
    assert [r["game_id"] for r in consensus(BOOK, 57)] == ["g2", "g1"]


def test_outcome_is_never_read_as_a_result():
    """Every pick says win. Scoring from it makes the whole pool perfect."""
    rows = consensus(BOOK, 57)
    assert all("correct" not in r and "won" not in r for r in rows)


def test_exposure_counts_where_an_entry_can_separate():
    e = exposure(consensus(BOOK, 57))
    assert e["picked"] == 2 and e["against_field"] == 1
    assert e["contrarian"] == 1              # HOU at 25%


def test_an_entry_that_never_picked_is_reported_not_crashed():
    e = exposure(consensus(BOOK, 61))
    assert e["picked"] == 0 and e["unpicked"] == 2
    assert e["against_field"] == 0


def test_an_empty_book_is_not_a_crash():
    assert consensus({}, 57) == []
    assert entries({}) == {"total": 0, "submitted": 0, "empty": 0}
    assert entries(None)["total"] == 0


# --- scoring ----------------------------------------------------------------
# Shapes copied from GET /scores/nfl/regular/2026/1. Teams and scores live in
# `metadata`; `status` is top level and was observed as "complete" or
# "pre_game". The pick data cannot score anything — every pick says
# outcome "win" — so the scoreboard is the only source.

def game(gid, away, away_pts, home, home_pts, status="complete"):
    return {"game_id": gid, "status": status,
            "metadata": {"away_team": away, "home_team": home,
                         "away_score": away_pts, "home_score": home_pts}}


GAMES = [
    game("g1", "CHI", 59, "CAR", 37),          # away wins
    game("g2", "TB", 27, "CIN", 33),           # home wins
    game("g3", "DEN", None, "KC", None, status="pre_game"),
]


def test_only_finished_games_produce_a_winner():
    w = winners(GAMES)
    assert w == {"g1": "CHI", "g2": "CIN"}
    assert "g3" not in w


def test_a_game_in_progress_is_never_scored_from_a_partial_lead():
    """The pick'em version of counting a Thursday player as a whole week."""
    live = [game("g4", "NYJ", 21, "NE", 3, status="in_game")]
    assert winners(live) == {}


def test_a_tie_is_recorded_as_a_tie_not_a_home_win():
    assert winners([game("g5", "AAA", 17, "BBB", 17)])["g5"] == "TIE"


def test_missing_scores_are_skipped_rather_than_guessed():
    assert winners([game("g6", "AAA", None, "BBB", 10)]) == {}
    assert winners([]) == {} and winners(None) == {}


def test_an_entry_splits_into_correct_wrong_and_pending():
    entry = {"picks": {"g1": pick("CHI"), "g2": pick("TB"), "g3": pick("KC")}}
    assert score_entry(entry, winners(GAMES)) == \
        {"correct": 1, "wrong": 1, "pending": 1}


def test_a_pending_game_is_not_counted_as_wrong():
    """It is the difference between a bad week and an unfinished one."""
    entry = {"picks": {"g3": pick("KC")}}
    assert score_entry(entry, winners(GAMES))["wrong"] == 0


def test_an_empty_entry_scores_nothing_without_erroring():
    assert score_entry({}, winners(GAMES))["correct"] == 0
    assert score_entry(None, {})["pending"] == 0


def test_the_leaderboard_orders_by_correct_and_excludes_empty_entries():
    book = {
        "1": {"picks": {"g1": pick("CHI"), "g2": pick("CIN")}},
        "2": {"picks": {"g1": pick("CAR"), "g2": pick("CIN")}},
        "3": {"picks": {}},
    }
    board = leaderboard(book, winners(GAMES))
    assert [r[3] for r in board] == ["1", "2"]
    assert board[0][0] == 2


def test_chalk_is_what_following_the_room_would_have_returned():
    """The number that says whether the PICKING was good, not the week."""
    book = {
        "1": {"picks": {"g1": pick("CHI"), "g2": pick("CIN")}},
        "2": {"picks": {"g1": pick("CHI"), "g2": pick("CIN")}},
        "3": {"picks": {"g1": pick("CAR"), "g2": pick("TB")}},
    }
    assert chalk_score(book, winners(GAMES)) == 2


def test_chalk_can_be_beaten_and_can_be_wrong():
    """A 99% consensus that loses costs everybody equally, including chalk."""
    book = {"1": {"picks": {"g1": pick("CAR")}},
            "2": {"picks": {"g1": pick("CAR")}}}
    assert chalk_score(book, winners(GAMES)) == 0


def test_following_the_field_is_told_apart_from_going_against_it():
    book = {
        "1": {"picks": {"g1": pick("CHI"), "g2": pick("TB")}},
        "2": {"picks": {"g1": pick("CHI"), "g2": pick("CIN")}},
        "3": {"picks": {"g1": pick("CHI"), "g2": pick("CIN")}},
    }
    got = against_the_field(book["1"], book, winners(GAMES))
    assert got["with"] == [1, 0]        # CHI with the field, correct
    assert got["against"] == [0, 1]     # TB against the field, wrong


def test_pending_games_are_left_out_of_the_field_split():
    book = {"1": {"picks": {"g3": pick("KC")}}}
    got = against_the_field(book["1"], book, winners(GAMES))
    assert got["with"] == [0, 0] and got["against"] == [0, 0]
