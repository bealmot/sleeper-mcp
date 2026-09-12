"""Draft value — measured WITHIN position, against the players a pick was
actually chosen ahead of.

The first version pooled every position and ranked by raw points. Quarterbacks
outscore everyone in most formats, so the "best picks" list came back as
nothing but quarterbacks and defences — an artifact of the scoring system
rather than a fact about anyone's drafting. Several tests here exist only to
keep that from coming back.
"""

from sleeper_mcp.picks import by_roster, finish_ranks, value


def pick(no, pid, rid, rnd=1, keeper=False):
    return {"pick_no": no, "round": rnd, "roster_id": rid,
            "player_id": pid, "is_keeper": keeper}


def test_ranks_are_by_points_descending():
    assert finish_ranks({"a": 100.0, "b": 300.0, "c": 200.0}) == \
        {"b": 1, "c": 2, "a": 3}


def test_ties_share_a_rank():
    r = finish_ranks({"a": 100.0, "b": 100.0, "c": 50.0})
    assert r["a"] == r["b"] == 1 and r["c"] == 3


# --- the cross-position distortion ------------------------------------------

QB_HEAVY = {"qb1": 350.0, "qb2": 300.0, "wr1": 200.0, "wr2": 120.0}
POS = {"qb1": "QB", "qb2": "QB", "wr1": "WR", "wr2": "WR"}


def test_a_late_quarterback_is_not_a_steal_just_for_being_a_quarterback():
    """THE REGRESSION. Pooled by raw points, qb2 taken last scores enormous.

    Measured against other quarterbacks he is the second taken who finished
    second: worth nothing at all.
    """
    picks = [pick(1, "wr1", 1), pick(2, "wr2", 2),
             pick(3, "qb1", 1), pick(4, "qb2", 2)]
    rows = {r["player_id"]: r for r in value(picks, QB_HEAVY, POS)}
    assert rows["qb2"]["value"] == 0
    assert rows["qb2"]["pos_taken"] == 2 and rows["qb2"]["finish"] == 2


def test_position_is_carried_so_the_pools_are_visible():
    rows = {r["player_id"]: r for r in value([pick(1, "qb1", 1)],
                                             QB_HEAVY, POS)}
    assert rows["qb1"]["position"] == "QB"


def test_the_best_list_is_not_all_quarterbacks():
    """A receiver who beat the receivers taken before him must be able to win.

    Pooling by points made this impossible: no receiver outscores a starting
    quarterback, so none could ever top the list.
    """
    picks = [pick(1, "wr2", 1), pick(2, "wr1", 2),
             pick(3, "qb1", 1), pick(4, "qb2", 2)]
    best = value(picks, QB_HEAVY, POS)[0]
    assert best["player_id"] == "wr1" and best["position"] == "WR"


def test_without_positions_everyone_shares_one_pool():
    """Documented behaviour, and exactly the distortion to avoid."""
    rows = value([pick(1, "a", 1), pick(2, "b", 2)], {"a": 1.0, "b": 9.0})
    assert {r["position"] for r in rows} == {"?"}


# --- value ------------------------------------------------------------------

def test_beating_the_players_taken_before_you_is_positive():
    picks = [pick(10, "wr2", 1), pick(20, "wr1", 2)]
    rows = {r["player_id"]: r for r in value(picks, QB_HEAVY, POS)}
    assert rows["wr1"]["value"] == 1          # 2nd WR taken, finished WR1
    assert rows["wr2"]["value"] == -1


def test_a_player_who_scored_nothing_is_kept_not_dropped():
    picks = [pick(5, "wr1", 1), pick(6, "wr2", 2)]
    rows = {r["player_id"]: r for r in value(picks, {"wr1": 200.0}, POS)}
    assert rows["wr2"]["points"] == 0.0 and rows["wr2"]["finish"] == 2


def test_ranking_is_over_drafted_players_only():
    """Nobody had the chance to take an undrafted player."""
    rows = value([pick(1, "wr1", 1)], {"wr1": 100.0, "undrafted": 999.0}, POS)
    assert rows[0]["finish"] == 1


def test_keeper_flag_survives():
    rows = value([pick(5, "qb1", 1, keeper=True)], QB_HEAVY, POS)
    assert rows[0]["is_keeper"] is True


# --- managers ---------------------------------------------------------------

def test_managers_are_ranked_by_mean_not_by_volume():
    """More picks accumulate more total value without drafting better."""
    picks = [pick(1, "wr2", 1), pick(2, "qb2", 1), pick(3, "wr1", 2),
             pick(4, "qb1", 2)]
    assert by_roster(value(picks, QB_HEAVY, POS))[0][0] == 2


def test_manager_summary_returns_count_total_and_mean():
    rows = value([pick(1, "wr1", 1), pick(2, "wr2", 1)], QB_HEAVY, POS)
    rid, n, total, mean = by_roster(rows)[0]
    assert rid == 1 and n == 2 and mean == total / 2


def test_empty_input_is_not_a_crash():
    assert value([], {}) == [] and by_roster([]) == []
    assert finish_ranks({}) == {}
