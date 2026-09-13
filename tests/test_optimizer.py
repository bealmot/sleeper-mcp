"""The lineup solver.

This had no test file at all, which is how a 24-point bug survived in it: the
only covered path was an exhaustive search for small inputs, and every real
roster is too big to take it. The two paths disagreed, and the one that ran in
production was the wrong one.

Everything here uses rosters big enough to be realistic, for that reason.
"""

import random

from sleeper_mcp import optimizer
from sleeper_mcp.optimizer import (MAX_DP_SLOTS, best_lineup, eligible, gain,
                                   holes)

STANDARD = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]


def p(pos, pts, name=""):
    return {"pos": pos, "pts": pts, "name": name or f"{pos}{pts}"}


def roster(n=15, seed=1):
    rng = random.Random(seed)
    pos = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "K", "DEF"]
    return [p(rng.choice(pos), round(rng.uniform(0, 25), 1)) for _ in range(n)]


# --- eligibility ------------------------------------------------------------

def test_flex_takes_the_three_it_should():
    for pos in ("RB", "WR", "TE"):
        assert eligible("FLEX", pos)
    assert not eligible("FLEX", "QB")


def test_superflex_takes_a_quarterback():
    assert eligible("SUPER_FLEX", "QB")


def test_an_unknown_slot_falls_back_to_its_own_name():
    """Under-counting is the safe direction: an unfamiliar league shows a slot
    it cannot fill rather than silently mispricing somebody into it."""
    assert eligible("WEIRD", "WEIRD") and not eligible("WEIRD", "WR")


def test_a_player_with_no_position_fills_nothing():
    assert not eligible("FLEX", None)


# --- the bug this file exists for -------------------------------------------

def test_overlapping_flex_slots_are_solved_exactly():
    """THE REGRESSION, worth 24 points.

    WRRB_FLEX is {RB, WR}; REC_FLEX is {WR, TE}. They overlap without either
    containing the other, so "most-constrained slot first, best eligible
    player" is not optimal: it gives the 30-point receiver to the first slot
    and leaves the second to a 1-point tight end, scoring 31 where 55 exists.

    Padded past ten players on purpose. With three the old code took an
    exhaustive path and got it right, which is exactly why nobody noticed.
    """
    slots = ["WRRB_FLEX", "REC_FLEX"]
    pool = [p("WR", 30.0), p("RB", 25.0), p("TE", 1.0)] + \
        [p("K", 0.1) for _ in range(9)]
    total, _assign = best_lineup(pool, slots)
    assert total == 55.0


def test_the_same_roster_scores_the_same_at_any_size():
    """Two code paths that disagree is the shape of the original defect.

    Padding with players who cannot fill either slot must not change anything.
    """
    slots = ["WRRB_FLEX", "REC_FLEX"]
    core = [p("WR", 30.0), p("RB", 25.0), p("TE", 1.0)]
    small, _ = best_lineup(core, slots)
    big, _ = best_lineup(core + [p("K", 0.1) for _ in range(12)], slots)
    assert small == big == 55.0


# --- correctness ------------------------------------------------------------

def test_it_fills_every_slot_it_can():
    total, assign = best_lineup(roster(15), STANDARD)
    assert len(assign) == len(STANDARD)
    assert total > 0


def test_no_player_is_used_twice():
    _total, assign = best_lineup(roster(15), STANDARD)
    used = [id(a) for a in assign if a]
    assert len(used) == len(set(used))


def test_every_assignment_is_legal():
    _total, assign = best_lineup(roster(15), STANDARD)
    for slot, player in zip(STANDARD, assign):
        if player:
            assert eligible(slot, player["pos"])


def test_the_total_matches_the_assignment():
    total, assign = best_lineup(roster(15), STANDARD)
    assert abs(total - sum(a["pts"] for a in assign if a)) < 0.01


def test_a_better_player_never_lowers_the_total():
    pool = roster(15)
    before, _ = best_lineup(pool, STANDARD)
    after, _ = best_lineup(pool + [p("WR", 99.0)], STANDARD)
    assert after >= before


def test_players_without_a_projection_are_left_out():
    pool = roster(12) + [{"pos": "WR", "pts": None}]
    _total, assign = best_lineup(pool, STANDARD)
    assert all(a is None or a["pts"] is not None for a in assign)


# --- pruning ----------------------------------------------------------------

def test_pruning_never_changes_an_answer():
    """It drops players who cannot appear in any optimal lineup. If that claim
    is wrong the solver is wrong, so it is checked against the unpruned run."""
    real = optimizer._prune
    try:
        for seed in range(40):
            pool = roster(18, seed)
            pruned, _ = best_lineup(pool, STANDARD)
            optimizer._prune = lambda live, slots: live
            full, _ = best_lineup(pool, STANDARD)
            optimizer._prune = real
            assert abs(pruned - full) < 1e-9, f"seed {seed}"
    finally:
        optimizer._prune = real


def test_pruning_keeps_enough_players_to_fill_the_lineup():
    kept = optimizer._prune(roster(20), STANDARD)
    assert len({k["pos"] for k in kept}) >= 5


# --- unfillable slots -------------------------------------------------------

def test_a_slot_nobody_can_fill_is_reported_not_hidden():
    pool = [p("WR", 10.0), p("WR", 9.0)] * 3
    assert "QB" in holes(pool, STANDARD)


def test_a_full_roster_has_no_holes():
    pool = [p("QB", 20.0), p("RB", 15.0), p("RB", 14.0), p("WR", 13.0),
            p("WR", 12.0), p("TE", 8.0), p("RB", 7.0), p("K", 5.0),
            p("DEF", 6.0)]
    assert holes(pool, STANDARD) == []


def test_an_empty_pool_leaves_every_slot_open():
    assert holes([], STANDARD) == STANDARD


def test_no_slots_scores_nothing():
    assert best_lineup(roster(15), []) == (0.0, [])


# --- gain -------------------------------------------------------------------

def test_a_player_who_cannot_crack_the_lineup_is_worth_nothing():
    """The whole point of pricing against YOUR roster."""
    strong = [p("WR", 25.0) for _ in range(5)] + \
        [p("QB", 25.0), p("RB", 25.0), p("RB", 24.0), p("TE", 20.0),
         p("K", 10.0), p("DEF", 10.0)]
    assert gain(strong, STANDARD, p("WR", 3.0)) == 0.0


def test_a_player_who_improves_it_is_worth_the_difference():
    pool = [p("QB", 20.0), p("RB", 15.0), p("RB", 14.0), p("WR", 13.0),
            p("WR", 12.0), p("TE", 3.0), p("RB", 7.0), p("K", 5.0),
            p("DEF", 6.0)]
    assert gain(pool, STANDARD, p("TE", 13.0)) == 10.0


def test_passing_a_precomputed_base_gives_the_same_answer():
    """The shortcut for pricing hundreds of free agents must not change it."""
    pool = roster(15)
    base, _ = best_lineup(pool, STANDARD)
    cand = p("WR", 18.0)
    assert gain(pool, STANDARD, cand) == gain(pool, STANDARD, cand, base=base)


# --- the wide-lineup fallback ----------------------------------------------

def test_a_lineup_too_wide_to_solve_exactly_still_returns():
    """Beyond MAX_DP_SLOTS the approximation runs rather than hanging."""
    slots = (STANDARD + ["DL", "LB", "DB", "IDP_FLEX", "LB", "DB"]
             )[:MAX_DP_SLOTS + 3]
    total, assign = best_lineup(roster(25), slots)
    assert len(assign) == len(slots)
    assert total > 0
