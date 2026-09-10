"""Starting slots must come from the league, never be assumed."""


def _starting(roster_positions):
    return [p for p in roster_positions if p not in ("BN", "IR", "TAXI")]


def test_standard():
    assert _starting(["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX",
                      "K", "DEF", "BN", "BN"]) == \
        ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"]


def test_superflex_no_kicker():
    """A layout that a hardcoded 10-slot array would silently mangle."""
    assert _starting(["QB", "SUPER_FLEX", "RB", "RB", "WR", "WR", "WR", "TE",
                      "FLEX", "BN", "BN", "IR"]) == \
        ["QB", "SUPER_FLEX", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX"]


def test_reserve_excluded():
    assert "IR" not in _starting(["QB", "RB", "IR", "TAXI", "BN"])


def test_flex_eligibility_is_not_assumed():
    """A superflex slot must accept a QB; a plain FLEX must not."""
    from sleeper_mcp.optimizer import eligible
    assert eligible("SUPER_FLEX", "QB")
    assert not eligible("FLEX", "QB")
    assert eligible("FLEX", "RB") and eligible("FLEX", "TE")
    assert eligible("REC_FLEX", "WR") and not eligible("REC_FLEX", "RB")
    # An unknown slot falls back to its own name, which under-counts rather
    # than over-counts — it fails visibly instead of mispricing quietly.
    assert eligible("QB", "QB") and not eligible("QB", "RB")


def test_gain_is_zero_for_a_player_who_cannot_start():
    """The whole point: depth at a position you are already deep in is worth 0."""
    from sleeper_mcp.optimizer import best_lineup
    slots = ["QB", "RB", "WR"]
    roster = [{"pos": "QB", "pts": 20.0, "name": "qb"},
              {"pos": "RB", "pts": 15.0, "name": "rb"},
              {"pos": "WR", "pts": 14.0, "name": "wr"}]
    base, _ = best_lineup(roster, slots)
    weak_wr = roster + [{"pos": "WR", "pts": 9.0, "name": "spare"}]
    assert best_lineup(weak_wr, slots)[0] == base          # adds nothing
    strong_wr = roster + [{"pos": "WR", "pts": 25.0, "name": "star"}]
    assert best_lineup(strong_wr, slots)[0] > base         # genuinely upgrades
