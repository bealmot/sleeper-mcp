"""Playoff odds, matchup odds and remaining-schedule difficulty.

The statistics live in season.py, which is pure and tested. This module only
fetches, assembles and formats — the split exists so the maths can be checked
without a league and the fetching can be wrong in obvious ways rather than
subtle ones.
"""

from __future__ import annotations

import asyncio

from .client import (current_week, gql, league, league_id, mcp, owners,
                     rest)
from .season import (bracket_champion, bracket_rounds, ordinal,
                     simulate, split_games, team_strength,
                     win_probability)


def _pairs(entries: list[dict]) -> list[tuple[dict, dict]]:
    """Group one week's roster entries into head-to-head pairs."""
    by_id: dict = {}
    for e in entries or []:
        if e.get("matchup_id") is not None:
            by_id.setdefault(e["matchup_id"], []).append(e)
    return [(v[0], v[1]) for v in by_id.values() if len(v) == 2]


async def _season(lg: str):
    """Assemble everything the simulation needs, in one concurrent pass.

    Returns (scores, remaining, records, playoff_teams, owners).

    WHAT COUNTS AS PLAYED IS A CALENDAR QUESTION, not a data question — see
    season.split_games. Mid-week a team shows a partial score that looks like
    a real one, so this passes Sleeper's own week counter and lets the tested
    pure function decide.
    """
    lg_cfg, rosters, wk = await asyncio.gather(
        league(lg), rest(f"/league/{lg}/rosters"), current_week())
    settings = lg_cfg.get("settings") or {}
    playoff_start = int(settings.get("playoff_week_start") or 15)
    playoff_teams = int(settings.get("playoff_teams") or 6)

    weeks = list(range(1, playoff_start))
    fetched = await asyncio.gather(
        *(rest(f"/league/{lg}/matchups/{w}") for w in weeks),
        return_exceptions=True)

    shaped = []
    for w, entries in zip(weeks, fetched):
        if isinstance(entries, BaseException):
            continue
        shaped.append((w, [(a["roster_id"], float(a.get("points") or 0),
                            b["roster_id"], float(b.get("points") or 0))
                           for a, b in _pairs(entries)]))
    scores, remaining = split_games(shaped, wk)

    records = {}
    for r in rosters or []:
        s = r.get("settings") or {}
        pf = float(s.get("fpts", 0)) + float(s.get("fpts_decimal", 0)) / 100
        records[r["roster_id"]] = (int(s.get("wins", 0)), int(s.get("losses", 0)),
                                   int(s.get("ties", 0)), pf)
        scores.setdefault(r["roster_id"], [])

    return scores, remaining, records, playoff_teams, await owners(lg), wk


def _confidence(strength: dict) -> tuple[float, int, str]:
    """How much of this forecast is evidence, and a plain warning if little."""
    evs = [v[3] for v in strength.values()]
    games = max((v[2] for v in strength.values()), default=0)
    ev = sum(evs) / len(evs) if evs else 0.0
    if games == 0:
        note = ("NO COMPLETED GAMES. Every team sits at the league prior, so "
                "these odds are schedule and nothing else.")
    elif ev < 0.35:
        note = (f"LOW CONFIDENCE — {games} game(s) played, so roughly "
                f"{(1 - ev) * 100:.0f}% of each strength estimate is still the "
                f"league prior. Read the ORDER, not the numbers.")
    elif ev < 0.6:
        note = (f"MODERATE — {games} games played, about {ev * 100:.0f}% "
                f"evidence. Gaps under ~10 points are not yet real.")
    else:
        note = f"{games} games played — estimates are mostly evidence now."
    return ev, games, note


@mcp.tool()
async def playoff_odds(trials: int = 10000, league_id_: str = "") -> str:
    """Playoff probability for every team, by simulating the rest of the season.

    Runs the remaining schedule many times, drawing each team's weekly score
    from its estimated strength, then counts how often each team finishes in a
    playoff seed. Wins carry forward; points for break ties, which is Sleeper's
    default.

    REPORTS ITS OWN CONFIDENCE. Early in a season a team's strength estimate is
    mostly a league-average prior rather than anything it has done, and the
    output says so instead of printing a number that looks equally solid in
    week 2 and week 12.

    Args:
        trials: simulations to run. 10000 keeps the error near half a point.
        league_id_: override the configured league.
    """
    lg = league_id(league_id_ or None)
    scores, remaining, records, n_playoff, owner, wk = await _season(lg)
    if not remaining:
        return "  The regular season is complete — nothing left to simulate."

    strength = team_strength(scores)
    ev, games, note = _confidence(strength)
    res = simulate(records, remaining,
                   {t: (v[0], v[1]) for t, v in strength.items()},
                   n_playoff, trials=trials, seed=1)

    rows = sorted(res.items(), key=lambda kv: -kv[1]["playoff"])
    out = [f"  Week {wk} — top {n_playoff} make the playoffs, "
           f"{len(remaining)} games left",
           f"  {note}", "",
           f"  {'team':20} {'now':8} {'playoff':>8} {'1 seed':>8} "
           f"{'proj W':>7} {'str':>7}"]
    for rid, r in rows:
        w, l, t, _pf = records[rid]
        rec = f"{w}-{l}" + (f"-{t}" if t else "")
        out.append(f"  {owner.get(rid, '?')[:20]:20} {rec:8} "
                   f"{r['playoff'] * 100:7.1f}% {r['top_seed'] * 100:7.1f}% "
                   f"{r['mean_wins']:7.1f} {strength[rid][0]:7.1f}")
    out += ["",
            f"  strength = expected points per week, shrunk toward the league "
            f"mean by games played ({ev * 100:.0f}% evidence)."]
    return "\n".join(out)


@mcp.tool()
async def matchup_odds(week: int = 0, league_id_: str = "") -> str:
    """Win probability for every head-to-head matchup in a week.

    Treats both team scores as normal around their estimated strength, so the
    answer accounts for how noisy a fantasy week is: a 15-point edge is much
    less decisive than it sounds when weekly swings are 25 points.

    Args:
        week: which week. Defaults to the current one.
        league_id_: override the configured league.
    """
    lg = league_id(league_id_ or None)
    scores, _rem, _rec, _n, owner, wk = await _season(lg)
    w = week or wk
    strength = team_strength(scores)
    _ev, _games, note = _confidence(strength)

    entries = await rest(f"/league/{lg}/matchups/{w}")
    pairs = _pairs(entries)
    if not pairs:
        return f"  No matchups found for week {w}."

    out = [f"  Week {w} win probability", f"  {note}", ""]
    for a, b in pairs:
        ra, rb = a["roster_id"], b["roster_id"]
        mu_a, sd_a = strength[ra][0], strength[ra][1]
        mu_b, sd_b = strength[rb][0], strength[rb][1]
        p = win_probability(mu_a, sd_a, mu_b, sd_b)
        fav, dog = (ra, rb) if p >= 0.5 else (rb, ra)
        out.append(f"  {owner.get(fav, '?')[:18]:18} {max(p, 1 - p) * 100:5.1f}%"
                   f"   over  {owner.get(dog, '?')[:18]:18}"
                   f"  ({strength[fav][0]:.0f} vs {strength[dog][0]:.0f})")
    return "\n".join(out)


@mcp.tool()
async def schedule_strength(league_id_: str = "") -> str:
    """How hard each team's REMAINING schedule is.

    Averages the strength of every opponent a team has left. This is the part
    of a playoff race nobody tracks by eye, and it decides bubble seeds: two
    teams with identical records can face schedules a touch-down apart per week.

    Args:
        league_id_: override the configured league.
    """
    lg = league_id(league_id_ or None)
    scores, remaining, records, _n, owner, wk = await _season(lg)
    if not remaining:
        return "  The regular season is complete — no schedule left."

    strength = team_strength(scores)
    _ev, _games, note = _confidence(strength)

    opps: dict[int, list[float]] = {}
    for _w, a, b in remaining:
        opps.setdefault(a, []).append(strength[b][0])
        opps.setdefault(b, []).append(strength[a][0])

    rows = sorted(((sum(v) / len(v), t) for t, v in opps.items()), reverse=True)
    lg_mean = sum(strength[t][0] for t in strength) / len(strength)

    out = [f"  Remaining schedule difficulty after week {wk}",
           f"  {note}", "",
           f"  {'team':20} {'record':8} {'opp/wk':>8} {'vs lg':>8} {'games':>6}"]
    for avg, t in rows:
        w, l, ties, _pf = records[t]
        rec = f"{w}-{l}" + (f"-{ties}" if ties else "")
        out.append(f"  {owner.get(t, '?')[:20]:20} {rec:8} {avg:8.1f} "
                   f"{avg - lg_mean:+8.1f} {len(opps[t]):6}")
    out += ["", "  opp/wk = mean strength of remaining opponents. "
                "Positive 'vs lg' is a harder road."]
    return "\n".join(out)


@mcp.tool()
async def playoff_bracket(consolation: bool = False, previous: bool = False,
                          league_id_: str = "") -> str:
    """The actual playoff bracket — who plays whom, and who has won.

    This is the real thing rather than a simulation. From the first playoff
    week it replaces `playoff_odds` entirely: once the field is set there is
    nothing left to estimate, only games to play.

    SEEDS ARE PROVISIONAL UNTIL THE REGULAR SEASON ENDS. Sleeper publishes a
    bracket from day one and re-seeds it as the standings move, so it renders
    perfectly in week 2 while meaning nothing. The output says which of the two
    it is looking at.

    Args:
        consolation: Show the losers' bracket instead of the championship one.
        previous: Follow this league's previous season, to see how it ended.
        league_id_: Override the configured league.
    """
    lg = league_id(league_id_ or None)
    lg_cfg = await league(lg)
    if previous:
        prev = lg_cfg.get("previous_league_id")
        if not prev or prev in ("0", 0):
            return "  This league has no previous season on Sleeper."
        lg = str(prev)
        lg_cfg = await league(lg)

    settings = lg_cfg.get("settings") or {}
    start = int(settings.get("playoff_week_start") or 15)
    path = "losers_bracket" if consolation else "winners_bracket"
    rows, owner, wk = await asyncio.gather(
        rest(f"/league/{lg}/{path}"), owners(lg), current_week())

    rounds = bracket_rounds(rows, owner)
    if not rounds:
        return (f"  No {'consolation' if consolation else 'championship'} "
                f"bracket published for {lg_cfg.get('name')} yet.")

    season = str(lg_cfg.get("season") or "")
    kind = "consolation" if consolation else "championship"
    out = [f"  {lg_cfg.get('name')} {season} — {kind} bracket"]

    anything_played = any(m["decided"] for _r, ms in rounds for m in ms)
    if previous or anything_played:
        pass
    elif wk < start:
        out.append(f"  PROVISIONAL — the regular season runs through week "
                   f"{start - 1}. Sleeper re-seeds this as the standings move, "
                   f"so the pairings below are today's, not the field.")
    out.append("")

    for rnd, matches in rounds:
        out.append(f"  Round {rnd} — week {start + rnd - 1}")
        for m in matches:
            p = m.get("placement")
            tag = ("  [championship]" if p == 1
                   else f"  [{ordinal(p)} place]" if p else "")
            if m["decided"]:
                beaten = m["t2"] if m["winner"] == m["t1"] else m["t1"]
                out.append(f"    m{m['m']:<3} {m['winner'][:20]:20} def. "
                           f"{beaten[:20]}{tag}")
            else:
                out.append(f"    m{m['m']:<3} {m['t1'][:20]:20} vs   "
                           f"{m['t2'][:20]}{tag}")
        out.append("")

    champ = bracket_champion(rows, owner)
    if champ and not consolation:
        out.append(f"  Champion: {champ}")
    elif not anything_played:
        out.append("  Nothing played yet.")
    return "\n".join(out).rstrip()


@mcp.tool()
async def standings_trend(league_id_: str = "", previous: bool = False) -> str:
    """How the table has MOVED, week by week, not just where it stands.

    NEEDS A TOKEN.

    `standings` gives today's totals; Sleeper keeps the table as it stood after
    every completed week, which is the only place the shape of a season lives.
    A 7-6 team that has won five straight and a 7-6 team that has lost five are
    the same row in the standings and opposite propositions in a trade.

    Only COMPLETED weeks are recorded, so early in a season this is empty and
    says so.

    Args:
        league_id_: Override the configured league.
        previous: Follow the league back one season.
    """
    from .season import form, movement, streak

    lg = league_id(league_id_ or None)
    lg_cfg = await league(lg)
    if previous:
        prev = lg_cfg.get("previous_league_id")
        if not prev or prev in ("0", 0):
            return "  This league has no previous season on Sleeper."
        lg = str(prev)
        lg_cfg = await league(lg)

    settings = lg_cfg.get("settings") or {}
    last = int(settings.get("playoff_week_start") or 15) - 1
    owner, wk = await asyncio.gather(owners(lg), current_week())
    through = last if previous else min(last, wk - 1)
    if through < 1:
        return (f"  No completed weeks in {lg_cfg.get('season')} yet — "
                f"standings history starts once a week finishes.")

    weeks = list(range(1, through + 1))
    fetched = await asyncio.gather(
        *(gql('{roster_standings(league_id:"%s",round:%d)'
              '{roster_id rank wins losses ties points record}}' % (lg, w),
              auth=True) for w in weeks),
        return_exceptions=True)

    history: dict = {}
    latest: dict = {}
    for w, res in zip(weeks, fetched):
        if isinstance(res, BaseException):
            continue
        for r in (res.get("roster_standings") or []):
            history.setdefault(r["roster_id"], {})[w] = r.get("rank")
            latest[r["roster_id"]] = r
    if not history:
        return f"  Sleeper has published no standings history for {lg}."

    shown = weeks if len(weeks) <= 8 else \
        sorted({weeks[0], *weeks[-6:]})          # first, then the recent run
    out = [f"  {lg_cfg.get('name')} {lg_cfg.get('season')} — "
           f"rank by week, through week {through}", "",
           f"  {'team':20} " + " ".join(f"w{w:<3}" for w in shown)
           + f" {'now':>4} {'form':>7} {'streak':>7}"]
    # By CURRENT RANK, not by movement. A standings table that is not in
    # standings order reads as an error; the movers are named below it.
    for rid, _f, lastrank, _chg in sorted(movement(history),
                                          key=lambda m: m[2]):
        rec = (latest.get(rid) or {}).get("record") or ""
        out.append(f"  {owner.get(rid, f'roster {rid}')[:20]:20} "
                   + " ".join(f"{history[rid].get(w) or '-':<4}" for w in shown)
                   + f" {lastrank:>4} {form(rec):>7} {streak(rec):>7}")

    moves = movement(history)
    climb = [m for m in moves if m[3] > 0]
    fall = [m for m in moves if m[3] < 0]
    out.append("")
    if climb:
        rid, first, now, chg = climb[0]
        out.append(f"  biggest climb: {owner.get(rid, rid)} "
                   f"{first} -> {now} (+{chg})")
    if fall:
        rid, first, now, chg = fall[-1]
        out.append(f"  biggest fall:  {owner.get(rid, rid)} "
                   f"{first} -> {now} ({chg})")
    out.append("  form reads oldest to newest, so 'LWWWW' is four straight "
               "wins after an opening loss.")
    return "\n".join(out)
