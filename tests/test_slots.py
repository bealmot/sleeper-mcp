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
