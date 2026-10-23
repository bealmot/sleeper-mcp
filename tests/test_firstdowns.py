"""Sleeper's projected first downs are yards / 10. These tests pin the repair.

Measured 2026-10-23: every projected rec_fd, rush_fd and pass_fd from 2022 to
2026 equals yards / 10 to two decimals, about 2.4x the real conversion rate.
Actuals carry real counts and are never passed through this module.
"""

import pytest

from sleeper_mcp.firstdowns import fd_from_yards, fd_note


def test_a_stubbed_receiving_count_is_rebuilt_from_yards():
    # George Pickens, week 7 2026: 4.15 catches, 62.75 yards, "6.28 first
    # downs" — more first downs than catches.
    out = fd_from_yards({"rec": 4.15, "rec_yd": 62.75, "rec_fd": 6.28}, "WR")
    assert out["rec_fd"] == pytest.approx(2.604, abs=0.001)


def test_a_quarterback_converts_carries_more_often_than_a_back():
    stub = {"rush_yd": 40.0, "rush_fd": 4.0}
    qb = fd_from_yards(stub, "QB")["rush_fd"]
    rb = fd_from_yards(stub, "RB")["rush_fd"]
    assert rb < qb < 4.0


def test_a_real_projection_is_left_alone():
    # 3.0 is not 62.75 / 10, so it did not come from the stub. If Sleeper
    # ever ships real first-down projections, this is what keeps them.
    out = fd_from_yards({"rec_yd": 62.75, "rec_fd": 3.0}, "WR")
    assert out["rec_fd"] == 3.0


def test_a_value_only_near_the_stub_is_left_alone():
    # Sleeper rounds to two decimals, so a stub is never more than 0.005 off
    # (the largest measured was 0.005 plus float noise). 0.009 off is a
    # real number, not a rounded stub.
    out = fd_from_yards({"rec_yd": 40.09, "rec_fd": 4.0}, "WR")
    assert out["rec_fd"] == 4.0


def test_negative_yards_never_make_negative_first_downs():
    # Week 1 2026: a quarterback projected -1.3 rushing yards, so the stub
    # projected -0.13 first downs.
    out = fd_from_yards({"rush_yd": -1.3, "rush_fd": -0.13}, "QB")
    assert out["rush_fd"] == 0.0


def test_an_unknown_position_still_loses_the_stub():
    out = fd_from_yards({"rec_yd": 10.0, "rec_fd": 1.0}, None)
    assert 0 < out["rec_fd"] < 0.5


def test_other_components_pass_through():
    out = fd_from_yards({"rec": 4.15, "rec_yd": 62.75, "rec_fd": 6.28,
                         "rec_td": 0.4}, "WR")
    assert (out["rec"], out["rec_yd"], out["rec_td"]) == (4.15, 62.75, 0.4)


def test_the_note_appears_only_in_a_league_that_pays_first_downs():
    assert fd_note({"rec_fd": 0.5})
    assert fd_note({"rec": 1.0, "rec_fd": 0}) == ""
