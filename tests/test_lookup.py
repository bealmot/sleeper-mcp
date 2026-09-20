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

from sleeper_mcp.lookup import (ambiguous, display_name, fantasy_position,
                                find_player, positions, resolve_names)

# Shaped like Sleeper's dictionary, including the parts that cause trouble:
# a retired duplicate, a coach, a player with no team, a team defence.
P = {
    "4634": {"full_name": "Kenneth Walker", "position": "RB", "team": None},
    "8151": {"full_name": "Kenneth Walker III", "position": "RB",
             "team": "SEA"},
    "9493": {"full_name": "Puka Nacua", "position": "WR", "team": "LAR"},
    "1234": {"full_name": "Puka Nacua", "position": "WR", "team": "NYJ"},
    "c001": {"full_name": "Andy Reid", "position": "HC", "team": "KC"},
    # A REAL defence entry: no full_name at all. The id is the team code.
    "SF": {"first_name": "San Francisco", "last_name": "49ers",
           "position": "DEF", "fantasy_positions": ["DEF"], "team": "SF"},
    "DET": {"first_name": "Detroit", "last_name": "Lions", "position": "DEF",
            "fantasy_positions": ["DEF"], "team": "DET"},
    # Listed at DB, startable at WR — the Travis Hunter shape.
    "12530": {"full_name": "Travis Hunter", "position": "DB",
              "fantasy_positions": ["DB", "WR"], "team": "JAX"},
    # A fullback: position FB, fantasy_positions RB.
    "7777": {"full_name": "Patrick Ricard", "position": "FB",
             "fantasy_positions": ["RB"], "team": "BAL"},
    # Active and unsigned — cut this week, not retired.
    "5555": {"full_name": "Tyreek Hill", "position": "WR",
             "fantasy_positions": ["WR"], "team": None, "active": True},
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
    assert ambiguous("Player", many).count("WR-KC") == 8


# --- defences, two-way players, the unsigned --------------------------------

def test_a_defence_resolves_by_nickname_city_and_team_code():
    """Real DEF entries have no full_name; every spelling must still work."""
    for spelling in ("Lions", "Detroit", "DET", "det", "Detroit Lions"):
        assert [pid for pid, _v in find_player(P, spelling)] == ["DET"], spelling


def test_a_team_code_is_exact_and_never_ambiguous():
    """'SF' must not also match 'San Francisco' as a mere substring hit."""
    assert [pid for pid, _v in find_player(P, "SF")] == ["SF"]


def test_a_player_id_resolves_directly():
    assert [pid for pid, _v in find_player(P, "9493")] == ["9493"]
    assert [pid for pid, _v in find_player(P, "9493", pool={"9493", "1234"})] == ["9493"]


def test_an_exact_name_beats_partial_matches():
    """'Kenneth Walker' is a substring of 'Kenneth Walker III'; with both
    active, the exact spelling picks the exact one."""
    Q = {**P, "4634": {**P["4634"], "team": "MIA"}}
    assert [pid for pid, _v in find_player(Q, "Kenneth Walker")] == ["4634"]
    assert len(find_player(Q, "Walker")) == 2


def test_a_two_way_player_is_findable_and_displays_at_his_fantasy_position():
    hits = find_player(P, "Travis Hunter")
    assert [pid for pid, _v in hits] == ["12530"]
    assert fantasy_position(hits[0][1]) == "WR"
    assert positions(hits[0][1]) == {"DB", "WR"}


def test_a_fullback_is_findable():
    assert [pid for pid, _v in find_player(P, "Ricard")] == ["7777"]


def test_an_unsigned_player_is_hidden_from_writes_but_reachable_by_reads():
    assert find_player(P, "Tyreek Hill") == []
    hits = find_player(P, "Tyreek Hill", allow_unsigned=True)
    assert [pid for pid, _v in hits] == ["5555"]


def test_the_retired_duplicate_stays_hidden_even_for_reads():
    """allow_unsigned admits ACTIVE team-less players, not retired ones."""
    hits = find_player(P, "Kenneth Walker", allow_unsigned=True)
    assert [pid for pid, _v in hits] == ["8151"]


def test_within_a_pool_a_released_player_still_resolves():
    """A rostered player whose club cut him has team=None but is on the
    roster; a write against the roster must still find him."""
    assert [pid for pid, _v in find_player(P, "Tyreek", pool={"5555"})] == ["5555"]


def test_display_name_handles_every_shape():
    assert display_name(P["DET"], "DET") == "Detroit Lions"
    assert display_name(P["9493"], "9493") == "Puka Nacua"
    assert display_name({}, "1") == "1"
    assert display_name(None, "x") == "x"


# --- resolve_names: the one resolver every write uses ------------------------

def test_resolve_names_returns_ids_and_names_every_problem():
    ids, bad = resolve_names(P, ["Lions", "Nacua", "Nobody"],
                             {"DET", "9493", "1234"}, "your roster")
    assert ids == ["DET"]
    assert len(bad) == 2
    assert "'Nacua' is ambiguous on your roster" in bad[0]
    assert "id 9493" in bad[0] and "id 1234" in bad[0]
    assert "'Nobody' is not on your roster" in bad[1]


def test_ambiguity_message_carries_ids():
    msg = ambiguous("Puka Nacua", find_player(P, "Puka Nacua"))
    assert "id 9493" in msg and "id 1234" in msg
