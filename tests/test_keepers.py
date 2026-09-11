"""Keeper parsing and the timing rule.

Sleeper hands `keepers` BACK as a list and takes it IN as a String. That
asymmetry is undocumented and fails silently in both directions.
"""

import pytest

pytest.importorskip("httpx")            # keepers imports client

from sleeper_mcp.keepers import (encode_keepers, parse_keepers,  # noqa: E402
                                 timing_note)


def test_a_list_passes_through():
    assert parse_keepers(["11584"]) == ["11584"]


def test_a_json_string_is_parsed_not_iterated():
    """THE TRAP. Iterating the string yields a keeper roster of punctuation."""
    assert parse_keepers('["11584", "4034"]') == ["11584", "4034"]


def test_absent_keepers_are_an_empty_list():
    for empty in (None, "", [], 0):
        assert parse_keepers(empty) == []


def test_ids_are_normalised_to_strings():
    """Rosters carry ids as strings; some responses use ints."""
    assert parse_keepers([11584, 4034]) == ["11584", "4034"]


def test_a_bare_id_is_not_split_into_characters():
    assert parse_keepers("11584") == ["11584"]


def test_nulls_inside_the_list_are_dropped():
    assert parse_keepers(["11584", None]) == ["11584"]


def test_encoding_produces_a_json_array_string():
    """The mutation takes String. A real list is accepted and stores nothing."""
    out = encode_keepers(["11584"])
    assert isinstance(out, str)
    assert parse_keepers(out) == ["11584"]


def test_encoding_an_empty_selection_clears_rather_than_omits():
    assert encode_keepers([]) == "[]"


def test_a_round_trip_survives():
    ids = ["11584", "4034", "9754"]
    assert parse_keepers(encode_keepers(ids)) == ids


# --- timing -----------------------------------------------------------------

def test_pre_draft_is_the_only_silent_window():
    assert timing_note("pre_draft", "2027") == ""


def test_in_season_states_facts_not_a_mechanism():
    """An earlier version asserted a write "does nothing" outside pre-draft.

    That was never verified, and it is contradicted by managers in a live
    league carrying designations mid-season. The note now says what is known:
    the draft that has run cannot be changed, and what happens at the next one
    is not established.
    """
    note = timing_note("in_season", "2026")
    assert "2026" in note and "not established" in note
    assert "nothing uses it" not in note


def test_a_draft_in_progress_is_flagged_differently_from_a_finished_one():
    assert "UNDER WAY" in timing_note("drafting", "2027")
    assert "UNDER WAY" not in timing_note("complete", "2026")
