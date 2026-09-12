"""Resolving a typed name to a player.

Everything that writes passes a human's typing through find_player first, so a
wrong match here submits the wrong player to somebody's real league. It has
happened: "Kenneth Walker" matches two entries in Sleeper's dictionary, one
retired since 2019, and taking the first hit sent an ineligible player. The
error came back as a lineup complaint and cost hours before anyone suspected
the name.

The function lived unexported and untested inside reads.py until it was moved
here.
"""

from sleeper_mcp.lookup import ambiguous, find_player

# Shaped like Sleeper's dictionary, including the parts that cause trouble:
# a retired duplicate, a coach, a player with no team, a team defence.
P = {
    "4634": {"full_name": "Kenneth Walker", "position": "RB", "team": None},
    "8151": {"full_name": "Kenneth Walker III", "position": "RB",
             "team": "SEA"},
    "9493": {"full_name": "Puka Nacua", "position": "WR", "team": "LAR"},
    "1234": {"full_name": "Puka Nacua", "position": "WR", "team": "NYJ"},
    "c001": {"full_name": "Andy Reid", "position": "HC", "team": "KC"},
    "SF": {"full_name": "San Francisco 49ers", "position": "DEF",
           "team": "SF"},
    "none": None,
}


def test_a_retired_duplicate_is_excluded_by_having_no_team():
    """THE ONE THAT COST HOURS. Both are 'Kenneth Walker'; one is retired."""
    hits = find_player(P, "Kenneth Walker")
    assert [pid for pid, _v in hits] == ["8151"]


def test_a_genuinely_ambiguous_name_returns_every_match():
    """Returning a list rather than a guess is the design.

    A function that picked one would hide exactly the case where picking wrong
    submits somebody else's player.
    """
    assert len(find_player(P, "Puka Nacua")) == 2


def test_coaches_are_never_matchable():
    assert find_player(P, "Andy Reid") == []


def test_team_defences_are_matchable():
    assert [pid for pid, _v in find_player(P, "San Francisco")] == ["SF"]


def test_a_null_entry_does_not_crash_the_search():
    assert find_player(P, "Nacua")


def test_matching_is_case_and_space_insensitive():
    assert find_player(P, "  puka NACUA  ")


def test_a_partial_name_matches():
    assert [pid for pid, _v in find_player(P, "Nacua")] == ["9493", "1234"]


def test_an_empty_name_matches_nothing_rather_than_everything():
    """A blank search that returned the whole dictionary would be a disaster
    behind a `len(hits) != 1` guard: it reads as merely ambiguous."""
    assert find_player(P, "") == []
    assert find_player(P, "   ") == []
    assert find_player(P, None) == []


def test_a_pool_restricts_the_search():
    """Always pass one. 11,000 players are searchable; a roster holds fifteen."""
    assert [pid for pid, _v in find_player(P, "Nacua", pool={"9493"})] == ["9493"]


def test_a_pool_holding_unknown_ids_does_not_crash():
    """Rosters can carry ids the dictionary has dropped."""
    assert find_player(P, "Nacua", pool={"9493", "gone"})


def test_a_pool_that_excludes_everyone_matches_nothing():
    assert find_player(P, "Nacua", pool={"8151"}) == []


# --- the message ------------------------------------------------------------

def test_no_match_says_so_plainly():
    assert "No player matches" in ambiguous("Nobody", [])


def test_ambiguity_names_the_alternatives():
    """'Ambiguous' alone leaves the reader guessing what to type instead."""
    msg = ambiguous("Puka Nacua", find_player(P, "Puka Nacua"))
    assert "2 match" in msg
    assert "LAR" in msg and "NYJ" in msg


def test_the_list_is_capped_so_a_broad_search_stays_readable():
    many = [(str(i), {"full_name": f"Player {i}", "position": "WR",
                      "team": "KC"}) for i in range(30)]
    assert ambiguous("Player", many).count("(WR-KC)") == 8
