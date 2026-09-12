"""Usage shares: rates, trends and ranking.

Shares are ratios, and ratios fail quietly — a wrong denominator gives a
plausible number, never an exception. Most of these check the denominator.
"""

from sleeper_mcp.shares import (collect, is_team_row, opportunity, rank,
                                team_opportunity, team_totals, trend,
                                week_usage)


def row(pid, team, week, **stats):
    return {"player_id": pid, "team": team, "week": week, "stats": stats}


# --- denominators -----------------------------------------------------------

def test_team_opportunity_sums_targets_and_carries():
    rows = [row("1", "BAL", 1, rec_tgt=9, rush_att=2),
            row("2", "BAL", 1, rec_tgt=5),
            row("3", "BAL", 1, rush_att=14)]
    assert team_opportunity(rows)[("BAL", 1)] == 30.0


def test_teams_and_weeks_do_not_bleed_into_each_other():
    rows = [row("1", "BAL", 1, rec_tgt=10), row("2", "BAL", 2, rec_tgt=4),
            row("3", "KC", 1, rec_tgt=7)]
    t = team_opportunity(rows)
    assert t[("BAL", 1)] == 10.0 and t[("BAL", 2)] == 4.0 and t[("KC", 1)] == 7.0


def test_a_share_cannot_exceed_one_when_every_position_is_present():
    """The trap: filtering the query to WR and then dividing by 'team' totals.

    That produces shares above 1.0, which reads as a maths bug and is really a
    query bug. With a full slate no player can exceed the whole.
    """
    rows = [row("1", "BAL", 1, rec_tgt=9), row("2", "BAL", 1, rec_tgt=5),
            row("3", "BAL", 1, rush_att=14)]
    got = collect(rows)
    assert all(w["opp_share"] <= 1.0 for weeks in got.values() for w in weeks)
    assert abs(sum(weeks[0]["opp_share"] for weeks in got.values()) - 1.0) < 1e-9


def test_missing_team_snaps_gives_none_not_zero():
    """A share of 0.0 claims he did not play. None says we do not know."""
    u = week_usage(row("1", "BAL", 1, off_snp=40), {})
    assert u["snap_share"] is None
    assert u["opp_share"] is None


def test_absent_and_unparseable_stats_are_zero_not_a_crash():
    assert opportunity({}) == 0.0
    assert opportunity({"rec_tgt": None, "rush_att": "x"}) == 0.0


# --- trend ------------------------------------------------------------------

def _weeks(shares):
    return [{"week": i + 1, "team": "LAR", "snaps": 50, "snap_share": s,
             "opp_share": s, "target_share": s, "opportunity": s * 30,
             "red_zone": 0, "points": 10.0}
            for i, s in enumerate(shares)]


def test_no_baseline_gives_none_not_zero():
    """THE ONE THAT MATTERS EARLY. Zero would claim the role is unchanged."""
    t = trend(_weeks([0.20, 0.25]), recent=2)
    assert t["base_opp_share"] is None
    assert t["delta"] is None
    assert t["games"] == 2


def test_a_rising_role_shows_a_positive_delta():
    t = trend(_weeks([0.10, 0.12, 0.30, 0.34]), recent=2)
    assert t["delta"] > 0.15
    assert t["base_games"] == 2 and t["recent_games"] == 2


def test_a_shrinking_role_shows_a_negative_delta():
    t = trend(_weeks([0.40, 0.44, 0.10, 0.12]), recent=2)
    assert t["delta"] < -0.25


def test_weeks_he_did_not_play_are_not_counted_as_zero_usage():
    """A bye or an inactive week must not read as a collapse in role."""
    weeks = _weeks([0.30, 0.32])
    weeks.insert(1, {"week": 99, "team": "LAR", "snaps": 0, "snap_share": None,
                     "opp_share": None, "target_share": None, "opportunity": 0,
                     "red_zone": 0, "points": 0.0})
    assert trend(weeks, recent=1)["games"] == 2


# --- ranking ----------------------------------------------------------------

def test_rising_roles_outrank_bigger_static_ones():
    """A steady 70% is priced in; the claim is the role that is growing."""
    trends = {
        "riser": trend(_weeks([0.10, 0.10, 0.35, 0.38]), recent=2),
        "steady": trend(_weeks([0.70, 0.70, 0.70, 0.70]), recent=2),
    }
    assert [pid for _d, _s, pid, _t in rank(trends)][0] == "riser"


def test_players_without_a_baseline_sort_after_those_with_one():
    trends = {"known": trend(_weeks([0.10, 0.10, 0.30, 0.30]), recent=2),
              "new": trend(_weeks([0.60, 0.62]), recent=2)}
    assert [pid for _d, _s, pid, _t in rank(trends)] == ["known", "new"]


def test_bit_part_players_are_filtered_out():
    """A fifth of the downs is garbage time, not a role change."""
    trends = {"bench": trend(_weeks([0.02, 0.02, 0.12, 0.14]), recent=2)}
    assert rank(trends, min_snap_share=0.25) == []


def test_everyone_ranks_by_level_when_nobody_has_a_baseline():
    """Week 2: no deltas exist at all, and the list must still be useful."""
    trends = {"a": trend(_weeks([0.30, 0.30]), recent=2),
              "b": trend(_weeks([0.60, 0.60]), recent=2)}
    assert [pid for _d, _s, pid, _t in rank(trends)] == ["b", "a"]


# --- the team aggregate row -------------------------------------------------
# Sleeper mixes a synthetic "TEAM_LAR" row in with the players, carrying the
# whole offence's totals. Summing every row counts the offence twice and halves
# every share. The original fixtures here contained only player rows, so they
# passed while live data was wrong by a factor of two — the defect surfaced
# because a 16-target receiver showed a 9% share, not because a test failed.

def team_row(team, week, tgt, att):
    return {"player_id": f"TEAM_{team}", "team": team, "week": week,
            "stats": {"rec_tgt": tgt, "rush_att": att}}


def test_the_team_row_is_recognised():
    assert is_team_row(team_row("LAR", 1, 48, 39))
    assert not is_team_row(row("9493", "LAR", 1, rec_tgt=16))


def test_the_aggregate_is_never_treated_as_a_player():
    rows = [team_row("LAR", 1, 48, 39), row("9493", "LAR", 1, rec_tgt=16)]
    got = collect(rows)
    assert "TEAM_LAR" not in got
    assert set(got) == {"9493"}


def test_the_team_row_is_the_denominator_not_extra_supply():
    """THE REGRESSION. Double counting halved every share in production."""
    rows = [team_row("LAR", 1, 48, 39),          # 87 team opportunities
            row("9493", "LAR", 1, rec_tgt=16)]
    assert team_totals(rows)[("LAR", 1)]["opp"] == 87.0
    u = collect(rows)["9493"][0]
    assert abs(u["opp_share"] - 16 / 87) < 1e-9      # not 16/174
    assert abs(u["target_share"] - 16 / 48) < 1e-9


def test_a_position_filtered_query_still_gets_correct_shares():
    """The reason to PREFER the team row rather than merely exclude it.

    Summing players is only right when every position came back. The aggregate
    is right either way, so a WR-only query no longer inflates shares past 1.0.
    """
    rows = [team_row("LAR", 1, 48, 39), row("9493", "LAR", 1, rec_tgt=16)]
    u = collect(rows)["9493"][0]
    assert u["opp_share"] < 0.25


def test_summing_players_is_the_fallback_when_no_team_row_exists():
    rows = [row("1", "BAL", 1, rec_tgt=9, rush_att=2),
            row("2", "BAL", 1, rec_tgt=5)]
    assert team_totals(rows)[("BAL", 1)]["opp"] == 16.0


def test_team_comes_from_the_stat_row_not_todays_roster():
    """A past season's breakout was listed under the club he signed with LATER.

    The player dictionary holds where someone plays now; the stat row holds
    where he earned those snaps. For a season= query those differ, and the
    dictionary is the wrong one.
    """
    weeks = _weeks([0.2, 0.3])
    weeks[0]["team"] = "KC"
    weeks[1]["team"] = "KC"
    assert trend(weeks)["team"] == "KC"


# --- season aggregates ------------------------------------------------------
# season_stats returns one row per player for the WHOLE season, with `week`
# None. team_totals buckets by (team, week) and skips rows with no week, so
# season rows are stamped week 0 before they reach it — one bucket per team,
# which is exactly right for a season.

def test_season_rows_need_a_week_stamp_to_bucket_at_all():
    """Unstamped season rows are skipped entirely and every share is None."""
    unstamped = [{"player_id": "TEAM_LAR", "team": "LAR", "week": None,
                  "stats": {"rec_tgt": 581, "rush_att": 465}},
                 {"player_id": "9493", "team": "LAR", "week": None,
                  "stats": {"rec_tgt": 166}}]
    assert team_totals(unstamped) == {}
    assert collect(unstamped)["9493"][0]["target_share"] is None


def test_stamped_season_rows_give_correct_full_season_shares():
    stamped = [{"player_id": "TEAM_LAR", "team": "LAR", "week": 0,
                "stats": {"rec_tgt": 581, "rush_att": 465}},
               {"player_id": "9493", "team": "LAR", "week": 0,
                "stats": {"rec_tgt": 166, "off_snp": 675, "tm_off_snp": 990}}]
    u = collect(stamped)["9493"][0]
    assert abs(u["target_share"] - 166 / 581) < 1e-9        # 28.6%
    assert abs(u["opp_share"] - 166 / 1046) < 1e-9
    assert abs(u["snap_share"] - 675 / 990) < 1e-9
