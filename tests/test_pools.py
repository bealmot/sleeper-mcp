"""Pick'em consensus.

The book's shape is copied from a live pool: keyed by roster id as a STRING,
each entry holding `picks` (game_id -> {team, outcome, ...}) and a
`tiebreaker`. Two things in it mislead — `outcome` is not a result, and most
entries are empty.
"""

from sleeper_mcp.pools import (consensus, entries, exposure, field_splits,
                               my_picks)


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
