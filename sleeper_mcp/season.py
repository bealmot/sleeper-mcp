"""Season-level inference: team strength, win probability, playoff odds.

PURE — no MCP import, no network, no configuration. Everything takes numbers
and returns numbers, which is what makes it testable without a league. Same
reason optimizer.py is separate.

THE PROBLEM THIS MODULE EXISTS TO HANDLE HONESTLY. Fantasy seasons are short.
People want playoff odds in week 2, when a league has played ONE game, and one
observation estimates neither a team's strength nor its variance. A number
computed from that and printed as "62.3%" is not a forecast. It is noise with
a decimal point.

So every estimate shrinks toward a league prior, and every caller is told how
much of the answer came from evidence rather than from the prior. An estimate
that is mostly prior gets REPORTED as mostly prior. The alternative — printing
the same confident percentage in week 2 and week 12 — is the failure mode this
module is built to avoid.
"""

from __future__ import annotations

import math
import random

# Virtual games of league-average mixed into every strength estimate. At 4, a
# team with one game played is 20% its own result and 80% league-average, which
# is roughly the right humility: one fantasy week is mostly matchup and variance.
PRIOR_GAMES = 4.0

DEFAULT_TRIALS = 10_000

# Fallbacks used ONLY when the season cannot supply an estimate — no games
# played, or a single score with no spread to measure. The mean is immaterial
# in that case, because every team receives the same one and only differences
# move odds. THE SD IS NOT IMMATERIAL. At sd=0 every simulated game ends level,
# every tiebreak collapses, and the seeding is decided by whatever order the
# teams happened to arrive in — which prints as 100% and 0% certainty derived
# from no information at all. A positive spread is what makes an unknown season
# come out as a coin flip instead.
PRIOR_MEAN = 110.0
PRIOR_SD = 28.0


def evidence_weight(n: int, prior_games: float = PRIOR_GAMES) -> float:
    """Fraction of a shrunk estimate that came from observation, not prior.

    This is the number that keeps the caller honest. Report it next to any
    forecast so a reader can tell a week-2 guess from a week-12 projection.
    """
    return 0.0 if n <= 0 else n / (n + prior_games)


def shrink(observed: float, n: int, prior: float,
           prior_games: float = PRIOR_GAMES) -> float:
    """Blend an observed mean toward a prior by how much data supports it.

    Standard empirical-Bayes shrinkage. With n=0 the answer is the prior; as n
    grows the prior washes out. The point is that it degrades GRACEFULLY: there
    is no game count below which this returns something wild.
    """
    w = evidence_weight(n, prior_games)
    return w * observed + (1.0 - w) * prior


def league_scoring(scores: dict[int, list[float]]) -> tuple[float, float]:
    """League-wide (mean, sd) for one team-week, from every score on record.

    The sd is what a single team's week-to-week noise looks like, and how it is
    estimated depends on how much season exists:

    TWO OR MORE WEEKS -> pooled WITHIN-team deviation. Each team is centred on
    its own mean first, so real quality differences do not inflate the noise
    estimate.

    ONE WEEK -> the cross-sectional spread of that week, which conflates team
    quality with weekly noise and therefore OVERSTATES the noise. That bias is
    deliberate and in the safe direction: overstated noise produces flatter,
    more uncertain odds early, which is the correct posture in week 2.
    """
    flat = [s for v in scores.values() for s in v]
    if not flat:
        return PRIOR_MEAN, PRIOR_SD
    mean = sum(flat) / len(flat)

    dev, dof = 0.0, 0
    for team_scores in scores.values():
        if len(team_scores) < 2:
            continue
        m = sum(team_scores) / len(team_scores)
        dev += sum((s - m) ** 2 for s in team_scores)
        dof += len(team_scores) - 1

    if dof > 0:                                   # pooled within-team
        sd = math.sqrt(dev / dof)
    elif len(flat) > 1:                           # one week — cross-sectional
        sd = math.sqrt(sum((s - mean) ** 2 for s in flat) / (len(flat) - 1))
    else:                                         # one score — nothing to measure
        sd = PRIOR_SD
    return mean, sd or PRIOR_SD


def team_strength(scores: dict[int, list[float]],
                  prior_games: float = PRIOR_GAMES,
                  ) -> dict[int, tuple[float, float, int, float]]:
    """-> {team: (mu, sd, games, evidence)}.

    mu is the team's shrunk expected score. sd is the league-wide weekly noise,
    used for every team rather than each team's own: separating a team that is
    genuinely volatile from one that had two odd weeks needs far more data than
    a fantasy season contains, and a per-team sd fitted to three games mostly
    fits noise.
    """
    lg_mean, lg_sd = league_scoring(scores)
    out = {}
    for team, team_scores in scores.items():
        n = len(team_scores)
        obs = sum(team_scores) / n if n else lg_mean
        out[team] = (shrink(obs, n, lg_mean, prior_games), lg_sd, n,
                     evidence_weight(n, prior_games))
    return out


def win_probability(mu_a: float, sd_a: float, mu_b: float, sd_b: float) -> float:
    """P(A outscores B), treating both scores as independent normals.

    A - B is then normal with mean mu_a - mu_b and variance sd_a^2 + sd_b^2, so
    the answer is one normal CDF. Fantasy scores are not exactly normal — they
    are mildly right-skewed — but the error from that is far smaller than the
    error in mu, so a heavier model would be false precision.
    """
    var = sd_a ** 2 + sd_b ** 2
    if var <= 0:
        return 0.5 if mu_a == mu_b else float(mu_a > mu_b)
    return 0.5 * (1.0 + math.erf((mu_a - mu_b) / math.sqrt(2.0 * var)))


def simulate(records: dict[int, tuple[int, int, int, float]],
             schedule: list[tuple[int, int, int]],
             strength: dict[int, tuple[float, float]],
             playoff_teams: int,
             trials: int = DEFAULT_TRIALS,
             seed: int | None = None) -> dict[int, dict[str, float]]:
    """Monte-Carlo the rest of the season.

    Args:
        records: {team: (wins, losses, ties, points_for)} so far.
        schedule: remaining games as (week, team_a, team_b).
        strength: {team: (mu, sd)} per team-week.
        playoff_teams: how many qualify.
        trials: simulations. 10k puts the standard error near 0.5pp, which is
            below the resolution anyone should read off a fantasy forecast.
        seed: fix it for reproducible output and for tests.

    Returns {team: {playoff, top_seed, mean_wins, mean_points}}.

    TIEBREAK. Seeds are ordered by wins, then points for — Sleeper's default.
    Leagues that use head-to-head or division rules will differ slightly, and
    the effect lands almost entirely on the bubble seed.
    """
    rng = random.Random(seed)
    teams = list(records)
    made = dict.fromkeys(teams, 0)
    top = dict.fromkeys(teams, 0)
    tot_w = dict.fromkeys(teams, 0.0)
    tot_p = dict.fromkeys(teams, 0.0)

    for _ in range(trials):
        wins = {t: float(records[t][0]) + 0.5 * records[t][2] for t in teams}
        pts = {t: records[t][3] for t in teams}
        for _week, a, b in schedule:
            mu_a, sd_a = strength[a]
            mu_b, sd_b = strength[b]
            sa = rng.gauss(mu_a, sd_a) if sd_a > 0 else mu_a
            sb = rng.gauss(mu_b, sd_b) if sd_b > 0 else mu_b
            pts[a] += sa
            pts[b] += sb
            if sa > sb:
                wins[a] += 1
            elif sb > sa:
                wins[b] += 1
            else:
                wins[a] += 0.5
                wins[b] += 0.5
        # Shuffle before the stable sort so EXACT ties resolve at random.
        # Sorting the list as-is breaks them by roster id, which is invisible
        # with real scores and catastrophic without them: identical teams then
        # produce a fixed 100%/0% split presented as certainty.
        order = sorted(rng.sample(teams, len(teams)),
                       key=lambda t: (-wins[t], -pts[t]))
        for t in order[:playoff_teams]:
            made[t] += 1
        top[order[0]] += 1
        for t in teams:
            tot_w[t] += wins[t]
            tot_p[t] += pts[t]

    return {t: {"playoff": made[t] / trials,
                "top_seed": top[t] / trials,
                "mean_wins": tot_w[t] / trials,
                "mean_points": tot_p[t] / trials} for t in teams}


def split_games(weeks: list[tuple[int, list[tuple[int, float, int, float]]]],
                current_week: int,
                ) -> tuple[dict[int, list[float]], list[tuple[int, int, int]]]:
    """Separate finished games from games still to be played.

    Args:
        weeks: [(week, [(roster_a, points_a, roster_b, points_b), ...])].
        current_week: the NFL week in progress, from Sleeper's own state.

    Returns (scores_by_roster, remaining_pairings).

    A WEEK IS FINISHED ONLY IF IT IS BEHIND THE CURRENT ONE. Nothing about the
    data itself can tell you this, and the obvious test is wrong: mid-week, a
    team that has played only its Thursday-night starter shows perhaps 23
    points, which is non-zero, plausible, and a fifth of a real score. Treating
    "has points" as "has finished" writes those fragments into the season
    history, and every estimate built on it is quietly wrong while looking
    completely ordinary. Ask the calendar, not the numbers.

    The points check that remains is a second guard, for a game inside a past
    week that never scored — a postponement, or a roster that was never set.
    """
    scores: dict[int, list[float]] = {}
    remaining: list[tuple[int, int, int]] = []
    for week, pairs in weeks:
        finished = week < current_week
        for ra, pa, rb, pb in pairs:
            if finished and pa > 0 and pb > 0:
                scores.setdefault(ra, []).append(pa)
                scores.setdefault(rb, []).append(pb)
            elif not finished:
                remaining.append((week, ra, rb))
    return scores, remaining


# --- playoff brackets -------------------------------------------------------
# Sleeper returns a bracket as a flat list of matches. Each carries `m` (match
# id), `r` (round), `t1`/`t2` (roster ids), `w`/`l` (winner/loser once played)
# and, where a slot is not yet decided, `t1_from`/`t2_from` pointing at another
# match: {"w": 3} is "the winner of match 3", {"l": 4} is "the loser of match
# 4". A `p` key marks a placement game — `p: 3` is the third-place match.

def slot_label(team: int | None, frm: dict | None, names: dict) -> str:
    """What to print for one side of a matchup.

    A decided slot holds a roster id. An undecided one holds a POINTER to
    another match, and rendering that as "TBD" throws away the only
    interesting thing about an unplayed bracket — which game feeds it.
    """
    if team is not None:
        return names.get(team, f"roster {team}")
    if frm:
        if "w" in frm:
            return f"winner of m{frm['w']}"
        if "l" in frm:
            return f"loser of m{frm['l']}"
    return "TBD"


def bracket_rounds(rows: list[dict], names: dict | None = None
                   ) -> list[tuple[int, list[dict]]]:
    """Group a flat bracket into rounds, resolving each slot to a label.

    Returns [(round, [match, ...]), ...] ordered by round then match id, each
    match carrying both sides' labels, the winner if any, and whether it is a
    placement game.
    """
    names = names or {}
    by_round: dict[int, list[dict]] = {}
    for row in rows or []:
        rnd = row.get("r")
        if rnd is None:
            continue
        w, l = row.get("w"), row.get("l")
        by_round.setdefault(rnd, []).append({
            "m": row.get("m"),
            "round": rnd,
            "placement": row.get("p"),
            "t1": slot_label(row.get("t1"), row.get("t1_from"), names),
            "t2": slot_label(row.get("t2"), row.get("t2_from"), names),
            "winner": names.get(w, f"roster {w}") if w is not None else None,
            "loser": names.get(l, f"roster {l}") if l is not None else None,
            "decided": w is not None,
        })
    for matches in by_round.values():
        matches.sort(key=lambda x: (x["m"] is None, x["m"]))
    return sorted(by_round.items())


def ordinal(n: int) -> str:
    """1 -> 1st, 3 -> 3rd, 11 -> 11th."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }".replace(" ", "")


def bracket_champion(rows: list[dict], names: dict | None = None) -> str | None:
    """Who won it, or None while the final is unplayed.

    SLEEPER MARKS THE CHAMPIONSHIP GAME `p: 1`. The `p` key means "this decides
    a placing", and first place is a placing — so treating every match with a
    `p` as a consolation game skips the final itself. An earlier version did
    exactly that, fell back to the previous round, and returned the right name
    purely because that league's champion had also won his semi-final. It would
    have named the losing finalist in any bracket where those differ.

    So: the final is the `p: 1` match when one exists, and otherwise the last
    non-placement match of the last round.
    """
    names = names or {}
    rows = [r for r in (rows or []) if r.get("r") is not None]
    if not rows:
        return None

    final = next((r for r in rows if r.get("p") == 1), None)
    if final is None:
        real = [r for r in rows if not r.get("p")]
        if not real:
            return None
        last = max(r["r"] for r in real)
        final = max((r for r in real if r["r"] == last),
                    key=lambda r: r.get("m") or 0)

    w = final.get("w")
    return names.get(w, f"roster {w}") if w is not None else None


# --- standings over time ----------------------------------------------------
# roster_standings(league_id, round) returns the table AS OF a week — `round`
# is the week — and only for weeks that have finished. REST gives the current
# totals and nothing else, so the PATH a season took is only available here.
#
# `record` is a string of results in order: "LWWWW" is an opening loss then
# four wins. Its length is the games played, which is how a bye or a
# postponement shows up.

def parse_record(record: str) -> list[str]:
    """"LWWWW" -> ['L','W','W','W','W']. Anything unexpected is dropped."""
    return [c for c in (record or "").upper() if c in ("W", "L", "T")]


def streak(record: str) -> str:
    """The current run: 'W4' for four straight wins. '-' if none."""
    games = parse_record(record)
    if not games:
        return "-"
    last = games[-1]
    n = 0
    for c in reversed(games):
        if c != last:
            break
        n += 1
    return f"{last}{n}"


def form(record: str, n: int = 5) -> str:
    """The last n results, oldest first — 'LWWWW' reads left to right."""
    games = parse_record(record)
    return "".join(games[-n:]) if games else "-"


def movement(history: dict[int, dict[int, int]]) -> list[tuple]:
    """Rank movement per team, from the first recorded week to the last.

    Args:
        history: {roster_id: {week: rank}}.

    Returns (roster_id, first_rank, last_rank, change) sorted by the biggest
    CLIMB first. Change is positive for moving up the table, because rank 1 is
    the top and a falling number is a rising team — the sign flip that makes
    this worth a named function rather than a subtraction at the call site.
    """
    out = []
    for rid, by_week in (history or {}).items():
        weeks = sorted(w for w, r in by_week.items() if r is not None)
        if not weeks:
            continue
        first, last = by_week[weeks[0]], by_week[weeks[-1]]
        out.append((rid, first, last, first - last))
    return sorted(out, key=lambda t: -t[3])
