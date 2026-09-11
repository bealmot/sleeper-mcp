"""Tests for season.py — the statistics, not the plumbing.

These check PROPERTIES that must hold rather than values that happen to come
out today, because the failure mode for a forecasting module is not a crash.
It is a number that looks reasonable and is wrong.
"""

import math

from sleeper_mcp.season import (  # noqa: E501
    split_games,
    evidence_weight, league_scoring, shrink, simulate, team_strength,
    win_probability,
)


# --- shrinkage --------------------------------------------------------------

def test_no_games_returns_the_prior_exactly():
    assert shrink(999.0, 0, 100.0) == 100.0


def test_shrinkage_converges_to_observation():
    far = shrink(150.0, 1, 100.0)
    near = shrink(150.0, 50, 100.0)
    assert 100.0 < far < near < 150.0


def test_one_game_is_mostly_prior():
    """The whole point: week 2 must not produce a confident number."""
    assert evidence_weight(1) < 0.25


def test_evidence_weight_is_monotonic_and_bounded():
    ws = [evidence_weight(n) for n in range(0, 20)]
    assert ws[0] == 0.0
    assert all(0.0 <= w < 1.0 for w in ws)
    assert all(a < b for a, b in zip(ws, ws[1:]))


# --- league scoring ---------------------------------------------------------

def test_pooled_sd_ignores_quality_differences():
    """Two teams, very different means, IDENTICAL internal spread.

    A cross-sectional sd would be inflated by the 50-point quality gap. The
    pooled within-team estimate must not be.
    """
    mean, sd = league_scoring({1: [100.0, 110.0], 2: [150.0, 160.0]})
    assert mean == 130.0
    assert abs(sd - math.sqrt(50.0)) < 1e-9      # each team deviates +/-5


def test_single_week_falls_back_to_cross_section():
    mean, sd = league_scoring({1: [100.0], 2: [120.0]})
    assert mean == 110.0
    assert sd > 0                                # overstated on purpose


def test_empty_history_is_not_a_crash():
    """This asserted (0.0, 0.0) until a live run showed what that costs.

    A zero spread is not a harmless placeholder. It makes every simulated game
    a tie, collapses the tiebreak, and turns "we know nothing" into a confident
    100%/0% split. The test had locked the bug in as the expected answer.
    """
    mean, sd = league_scoring({})
    assert mean > 0 and sd > 0


# --- win probability --------------------------------------------------------

def test_identical_teams_are_a_coin_flip():
    assert win_probability(100.0, 25.0, 100.0, 25.0) == 0.5


def test_probabilities_are_complementary():
    a = win_probability(120.0, 25.0, 100.0, 30.0)
    b = win_probability(100.0, 30.0, 120.0, 25.0)
    assert abs(a + b - 1.0) < 1e-12


def test_matches_the_normal_cdf():
    """mu gap 1, sds 1 and 1 -> Phi(1/sqrt(2)) = 0.760250."""
    assert abs(win_probability(1.0, 1.0, 0.0, 1.0) - 0.7602499) < 1e-6


def test_more_noise_pulls_toward_even():
    """The same edge is worth less when weeks are wilder."""
    calm = win_probability(120.0, 10.0, 100.0, 10.0)
    wild = win_probability(120.0, 40.0, 100.0, 40.0)
    assert 0.5 < wild < calm


def test_zero_variance_is_deterministic():
    assert win_probability(120.0, 0.0, 100.0, 0.0) == 1.0


# --- simulation -------------------------------------------------------------

def _league(n=10):
    records = {t: (0, 0, 0, 0.0) for t in range(1, n + 1)}
    schedule = [(w, a, n + 1 - a)
                for w in range(1, 14) for a in range(1, n // 2 + 1)]
    return records, schedule


def test_exactly_the_playoff_field_qualifies_every_trial():
    """The invariant that catches almost any seeding bug.

    Six teams make it in every single simulation, so the probabilities must
    sum to six. Off-by-one slicing, double-counting and dropped teams all
    break this and almost nothing else.
    """
    records, schedule = _league()
    strength = {t: (110.0, 25.0) for t in records}
    res = simulate(records, schedule, strength, playoff_teams=6,
                   trials=500, seed=1)
    assert abs(sum(r["playoff"] for r in res.values()) - 6.0) < 1e-9
    assert abs(sum(r["top_seed"] for r in res.values()) - 1.0) < 1e-9


def test_equal_teams_get_roughly_equal_odds():
    records, schedule = _league()
    strength = {t: (110.0, 25.0) for t in records}
    res = simulate(records, schedule, strength, playoff_teams=6,
                   trials=2000, seed=7)
    odds = [r["playoff"] for r in res.values()]
    assert all(abs(o - 0.6) < 0.1 for o in odds)


def test_a_dominant_team_is_nearly_certain():
    records, schedule = _league()
    strength = {t: (110.0, 25.0) for t in records}
    strength[3] = (200.0, 25.0)
    res = simulate(records, schedule, strength, playoff_teams=6,
                   trials=500, seed=3)
    assert res[3]["playoff"] > 0.99
    assert res[3]["top_seed"] > 0.95


def test_existing_record_carries_forward():
    """Banked wins must count. A 5-0 team starts ahead of an 0-5 team."""
    records, schedule = _league()
    records[2] = (5, 0, 0, 600.0)
    records[9] = (0, 5, 0, 400.0)
    strength = {t: (110.0, 25.0) for t in records}
    res = simulate(records, schedule, strength, playoff_teams=6,
                   trials=500, seed=5)
    assert res[2]["playoff"] > res[9]["playoff"]


def test_seeded_runs_reproduce():
    records, schedule = _league()
    strength = {t: (110.0, 25.0) for t in records}
    a = simulate(records, schedule, strength, 6, trials=200, seed=42)
    b = simulate(records, schedule, strength, 6, trials=200, seed=42)
    assert a == b


def test_team_strength_reports_its_own_evidence():
    st = team_strength({1: [100.0], 2: [140.0]})
    for _mu, _sd, n, ev in st.values():
        assert n == 1
        assert ev < 0.25
    assert st[2][0] > st[1][0]          # ordering survives shrinkage


# --- split_games ------------------------------------------------------------
# These pin a bug that shipped and looked fine. Mid-week, teams that had played
# only their Thursday-night starter showed ~23 points. The rule at the time was
# "points > 0 means played", so those fragments went into the season history,
# and every downstream estimate was about a fifth of its true value while the
# output still looked completely ordinary.

def test_week_in_progress_never_becomes_history():
    """THE REGRESSION. Partial scores are plausible, non-zero, and wrong."""
    weeks = [(1, [(1, 23.4, 2, 18.9), (3, 11.2, 4, 27.0)])]
    scores, remaining = split_games(weeks, current_week=1)
    assert scores == {}, "partial Thursday-night scores must not count"
    assert sorted(remaining) == [(1, 1, 2), (1, 3, 4)]


def test_finished_weeks_become_history():
    weeks = [(1, [(1, 120.5, 2, 98.2)]), (2, [(1, 101.0, 2, 133.4)])]
    scores, remaining = split_games(weeks, current_week=3)
    assert scores == {1: [120.5, 101.0], 2: [98.2, 133.4]}
    assert remaining == []


def test_past_and_future_split_at_the_current_week():
    weeks = [(1, [(1, 120.0, 2, 98.0)]),
             (2, [(1, 40.0, 2, 0.0)]),          # in progress
             (3, [(1, 0.0, 2, 0.0)])]           # not started
    scores, remaining = split_games(weeks, current_week=2)
    assert scores == {1: [120.0], 2: [98.0]}
    assert sorted(remaining) == [(2, 1, 2), (3, 1, 2)]


def test_a_past_game_that_never_scored_is_dropped_not_simulated():
    """A postponement in a finished week is missing data, not a future game."""
    weeks = [(1, [(1, 0.0, 2, 0.0)])]
    scores, remaining = split_games(weeks, current_week=5)
    assert scores == {}
    assert remaining == []


def test_no_games_yet_yields_nothing_rather_than_zeros():
    scores, remaining = split_games([(1, [(1, 0.0, 2, 0.0)])], current_week=1)
    assert scores == {}
    assert remaining == [(1, 1, 2)]


# --- degenerate input -------------------------------------------------------
# A second bug the live run exposed, which every invariant test above passed
# through happily. With no games played every team had mu=0 and sd=0, so every
# simulated game tied, the tiebreak collapsed, and seeding fell back to roster
# order — printing 100% and 0% certainty built on nothing.

def test_no_data_still_has_spread():
    """sd=0 is what turns an unknown season into false certainty."""
    mean, sd = league_scoring({})
    assert sd > 0
    assert mean > 0


def test_a_single_score_cannot_measure_spread_but_must_not_claim_zero():
    _mean, sd = league_scoring({1: [118.0]})
    assert sd > 0


def test_an_unknown_season_is_a_coin_flip_not_a_certainty():
    """THE REGRESSION. Identical teams must share the odds, not split 100/0."""
    records, schedule = _league()
    strength = {t: league_scoring({}) for t in records}
    res = simulate(records, schedule, strength, playoff_teams=6,
                   trials=2000, seed=11)
    odds = sorted(r["playoff"] for r in res.values())
    assert odds[0] > 0.4, f"a team was near-eliminated on no evidence: {odds}"
    assert odds[-1] < 0.8, f"a team was near-certain on no evidence: {odds}"


def test_ties_do_not_favour_low_roster_ids():
    """Exactly equal teams with no schedule left: pure tiebreak behaviour."""
    records = {t: (5, 5, 0, 1000.0) for t in range(1, 11)}
    res = simulate(records, [], {t: (110.0, 28.0) for t in records},
                   playoff_teams=5, trials=2000, seed=13)
    assert all(0.3 < r["playoff"] < 0.7 for r in res.values())
