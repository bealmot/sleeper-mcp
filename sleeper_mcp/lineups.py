"""Optimal-lineup maths, and the two tools built on it.

The idea both tools share: **a player's value is what he adds to YOUR starting
lineup**, not his projection and not his value over a generic replacement.

A sixth receiver projecting 12 points is worth nothing to a lineup already
starting four better ones. A tight end projecting 11 is worth ten points when
your only other one is on a bye. Positional rankings cannot express that
because they do not know your roster; this does.

    gain(player) = best_lineup(roster + player) - best_lineup(roster)

which is zero by construction for anyone who cannot crack your lineup.

SLOT ELIGIBILITY IS READ FROM THE LEAGUE, NOT ASSUMED, and judged on each
player's `fantasy_positions` rather than the single position he is listed at.
"""

from __future__ import annotations

import datetime as dt
import time

from .client import (READ, current_week, gql, league, league_id, players,
                     rest, roster_id, scored, starting_slots, state, tool)
from .lookup import FANTASY_POSITIONS, display_name, fantasy_position, positions
from .optimizer import best_lineup, eligible  # noqa: F401  (re-exported)


def _entry(pid: str, v: dict, pts) -> dict:
    return {"pos": fantasy_position(v), "positions": positions(v),
            "name": display_name(v, pid), "id": pid, "team": v.get("team"),
            "inj": v.get("injury_status") or "", "pts": pts}


async def _projections(season, week: int, ids: list[str], scoring: dict) -> dict:
    """{player_id: league-scored projection} for EVERY id, batched — no cap.

    A previous version stopped after 600 ids taken in dictionary order and
    presented the result as complete; a starting quarterback sat past the
    cut and was never priced.
    """
    out: dict = {}
    for i in range(0, len(ids), 150):
        chunk = ids[i:i + 150]
        d = await gql(
            '{stats_for_players_in_week(sport:"nfl",season:"%s",'
            'season_type:"regular",week:%d,player_ids:%s,category:"proj")'
            '{player_id stats}}'
            % (season, week, "[" + ",".join(f'"{c}"' for c in chunk) + "]"))
        for r in (d.get("stats_for_players_in_week") or []):
            out[r["player_id"]] = scored(r["stats"] or {}, scoring)
    return out


async def _pool(lg: str, rid: int, week: int):
    """Everyone startable on a roster, with points under the league's scoring.

    IR and TAXI players sit inside `players` and cannot start; both are
    excluded. `pts` is None where Sleeper published no projection.
    """
    P = await players()
    lgd = await league(lg)
    scoring = lgd.get("scoring_settings") or {}
    rosters = await rest(f"/league/{lg}/rosters")
    me = next((r for r in rosters if r["roster_id"] == rid), None)
    if not me:
        return None, None, None
    ids = [str(p) for p in (me.get("players") or [])]
    off = {str(p) for p in (me.get("reserve") or [])}
    off |= {str(p) for p in (me.get("taxi") or [])}
    active = [i for i in ids if i not in off]
    proj = await _projections(lgd.get("season"), week, active, scoring) if active else {}
    pool = [_entry(pid, P.get(pid) or {}, proj.get(pid)) for pid in active]
    return pool, P, lgd


async def _teams_playing(season, week: int) -> set | None:
    """Teams with a game that week, from the scoreboard. None if unknown."""
    try:
        games = await rest(f"/scores/nfl/regular/{season}/{week}")
    except Exception:                                   # noqa: BLE001
        return None
    if not games:
        return None
    out = set()
    for g in games:
        md = g.get("metadata") or {}
        out |= {md.get("home_team"), md.get("away_team")}
    return {t for t in out if t}


@tool(annotations=READ)
async def bye_outlook(league_id_: str = "", roster_id_: int = 0,
                      through_week: int = 17) -> str:
    """Which upcoming weeks you CANNOT field a legal lineup, and why.

    A player on a bye returns no projection for that week, and the
    scoreboard says which teams play, so byes and UNKNOWNS are told apart: a
    player missing from a week his team DOES play is reported as UNKNOWN,
    not scored as zero and not called a bye. An unmeasurable value must not
    silently take the healthy default; that is how a hole gets hidden until
    Sunday. A week Sleeper has not projected at all is named as such rather
    than listed as unfillable.

    Args:
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
        roster_id_: Defaults to SLEEPER_ROSTER_ID.
        through_week: Last week to project. Default 17.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    now = await current_week()
    slots = await starting_slots(lg)
    lgd = await league(lg)
    s = lgd.get("settings") or {}
    season = (await state()).get("season")

    out = [f"{lgd.get('name')} — weeks {now} to {through_week}",
           f"  starters: {' '.join(slots)}",
           f"  trade deadline week {s.get('trade_deadline')}   "
           f"playoffs start week {s.get('playoff_week_start') or 'none'}", "",
           f"  {'wk':>3} {'proj':>8} {'out':>4}  {'empty slots':14} byes / UNKNOWN"]

    trouble, unknowns, unprojected = [], [], []
    for wk in range(now, through_week + 1):
        pool, _, _ = await _pool(lg, rid, wk)
        if pool is None:
            return f"No roster {rid} in league {lg}."
        playing = [p for p in pool if p["pts"] is not None]
        away = [p for p in pool if p["pts"] is None]
        if not playing:
            unprojected.append(wk)
            out.append(f"  {wk:>3} {'-':>8} {'-':>4}  {'(no projections published)':14}")
            continue
        teams = await _teams_playing(season, wk)
        byes = [p for p in away if teams is None or p["team"] not in teams]
        unknown = [p for p in away if teams is not None and p["team"] in teams]
        total, assign = best_lineup(playing, slots)
        empty = [slots[i] for i, a in enumerate(assign) if a is None]
        if empty:
            trouble.append(wk)
        if unknown:
            unknowns.append(wk)
        who = ", ".join(p["name"].split()[-1] for p in byes)
        if unknown:
            who += ("  " if who else "") + "UNKNOWN: " + \
                ", ".join(p["name"].split()[-1] for p in unknown)
        out.append(f"  {wk:>3} {total:8.1f} {len(away):>4}  "
                   f"{','.join(empty) or '-':14} {who[:60] or '-'}"
                   + ("   <<<" if empty else ""))

    if trouble:
        out += ["", f"  Weeks you cannot fill a legal lineup: {trouble}.",
                f"  Anything you intend to fix by trade has to happen before "
                f"week {s.get('trade_deadline')}."]
    else:
        out += ["", "  Every projected week can field a legal lineup."]
    if unknowns:
        out.append(f"  UNKNOWN in week(s) {unknowns}: a player whose team plays "
                   f"but who has no projection — injured, suspended or "
                   f"unsigned. Not a bye, and not scored as zero.")
    if unprojected:
        out.append(f"  Week(s) {unprojected} have no projections published yet; "
                   f"nothing can be said about them.")
    return "\n".join(out)


@tool(annotations=READ)
async def waiver_targets(league_id_: str = "", roster_id_: int = 0,
                         week: int = 0, limit: int = 15,
                         position: str = "") -> str:
    """Free agents ranked by what they ADD TO YOUR STARTING LINEUP.

    Not by projection, and not by value over a generic replacement. The number
    is `best_lineup(roster + him) - best_lineup(roster)`, which is zero for
    anyone who cannot crack your lineup — so a high-projection player at a
    position you are already deep in correctly prices at nothing.

    EVERY free agent is priced; nothing is silently cut off. Each is marked
    ON WAIVERS (a claim, processed when they clear) or FREE (add now), and
    your remaining FAAB is shown.

    "Nothing improves your lineup this week" is a real answer and this tool
    will give it rather than padding a list.

    Args:
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
        roster_id_: Defaults to SLEEPER_ROSTER_ID.
        week: NFL week. 0 (default) uses the current week.
        limit: How many candidates to show. Default 15.
        position: Restrict to QB/RB/WR/TE/K/DEF. Blank for all.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    if position and position.upper() not in FANTASY_POSITIONS:
        return f"position must be one of {'/'.join(FANTASY_POSITIONS)}, got {position!r}."
    wk = week or await current_week()
    slots = await starting_slots(lg)
    pool, P, lgd = await _pool(lg, rid, wk)
    if pool is None:
        return f"No roster {rid} in league {lg}."
    scoring = lgd.get("scoring_settings") or {}
    live = [p for p in pool if p["pts"] is not None]
    base, _ = best_lineup(live, slots)

    rosters = await rest(f"/league/{lg}/rosters")
    owned = {str(p) for r in rosters for p in (r.get("players") or [])}
    me = next((r for r in rosters if r["roster_id"] == rid), None) or {}
    wanted = {position.upper()} if position else set(FANTASY_POSITIONS)
    free = [pid for pid, v in P.items()
            if pid not in owned and v and v.get("team")
            and positions(v) & wanted]
    if not free:
        return "No free agents found at that position — is the league id right?"

    proj = await _projections(lgd.get("season"), wk, free, scoring)

    # ON WAIVERS vs FREE, from the public league_players query. An
    # annotation only: if it cannot be fetched the pricing still stands.
    clears: dict = {}
    try:
        d = await gql('{league_players(league_id:"%s"){player_id settings}}' % lg)
        now = time.time()
        for r in (d.get("league_players") or []):
            at = (r.get("settings") or {}).get("waiver_clears_at")
            if at and float(at) > now:
                clears[str(r.get("player_id"))] = float(at)
    except Exception:                                   # noqa: BLE001
        pass

    rows = []
    for pid, pts in proj.items():
        if pts <= 0:
            continue
        v = P.get(pid) or {}
        cand = _entry(pid, v, pts)
        with_him, _ = best_lineup(live + [cand], slots)
        gain = round(with_him - base, 2)
        if gain > 0:
            rows.append((gain, pts, cand["pos"], cand["name"], v.get("team"),
                         cand["inj"], pid))
    rows.sort(reverse=True)

    s = lgd.get("settings") or {}
    budget = int(s.get("waiver_budget") or 0)
    used = int((me.get("settings") or {}).get("waiver_budget_used") or 0)
    out = [f"{lgd.get('name')} — week {wk} waiver targets",
           f"  your best legal lineup projects {base:.1f}",
           f"  {len(proj)} of {len(free)} free agents have a projection; "
           f"{len(rows)} improve your lineup"
           + (f"   FAAB: ${budget - used} of ${budget} left"
              if s.get("waiver_type") == 2 else ""), ""]
    if not rows:
        out.append("  NOTHING improves your lineup this week. That is a real "
                   "answer, not an empty list — your starters beat every "
                   "available player at their position.")
        return "\n".join(out)
    for gain, pts, pos, name, team, inj, pid in rows[:limit]:
        flag = f"  [{inj}]" if inj else ""
        at = clears.get(pid)
        status = (f"  ON WAIVERS until "
                  f"{dt.datetime.fromtimestamp(at, dt.timezone.utc):%a %H:%M} UTC"
                  if at else "  FREE")
        out.append(f"  +{gain:5.1f}  {pos or '?':4} {name[:24]:24} "
                   f"{team or '-':4} projects {pts:5.1f}{flag}{status}")
    out += ["", "  Gain is what he adds to YOUR lineup, so a player you "
            "cannot start prices at zero no matter how good he is.",
            "  FREE = add now with waiver_claim; ON WAIVERS = a bid, processed "
            "when he clears."]
    return "\n".join(out)
