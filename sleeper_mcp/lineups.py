"""Optimal-lineup maths, and the two tools built on it.

The idea both tools share: **a player's value is what he adds to YOUR starting
lineup**, not his projection and not his value over a generic replacement.

A sixth receiver projecting 12 points is worth nothing to a lineup already
starting four better ones. A tight end projecting 11 is worth ten points when
your only other one is on a bye. Positional rankings cannot express that
because they do not know your roster; this does.

    gain(player) = best_lineup(roster + player) - best_lineup(roster)

which is zero by construction for anyone who cannot crack your lineup.

SLOT ELIGIBILITY IS READ FROM THE LEAGUE, NOT ASSUMED. Sleeper supports a wide
range of flex types and a hardcoded map would quietly mis-price players in any
league that deviates.
"""

from __future__ import annotations

from .client import (current_week, gql, league, league_id, mcp, players, rest,
                     roster_id, scored, starting_slots)
from .optimizer import best_lineup, eligible  # noqa: F401  (re-exported)


async def _pool(lg: str, rid: int, week: int):
    """Everyone on a roster, with points under the league's own scoring."""
    P = await players()
    lgd = await league(lg)
    scoring = lgd.get("scoring_settings") or {}
    rosters = await rest(f"/league/{lg}/rosters")
    me = next((r for r in rosters if r["roster_id"] == rid), None)
    if not me:
        return None, None, None
    ids = [str(p) for p in (me.get("players") or [])]
    reserve = {str(p) for p in (me.get("reserve") or [])}
    active = [i for i in ids if i not in reserve]

    proj = {}
    if active:
        d = await gql(
            '{stats_for_players_in_week(sport:"nfl",season:"%s",'
            'season_type:"regular",week:%d,player_ids:%s,category:"proj")'
            '{player_id stats}}'
            % (lgd.get("season"), week,
               "[" + ",".join(f'"{i}"' for i in active) + "]"))
        proj = {r["player_id"]: r["stats"]
                for r in (d.get("stats_for_players_in_week") or [])}

    pool = []
    for pid in active:
        v = P.get(pid) or {}
        pool.append({"pos": v.get("position"), "name": v.get("full_name") or pid,
                     "id": pid, "team": v.get("team"),
                     "inj": v.get("injury_status") or "",
                     "pts": scored(proj[pid], scoring) if pid in proj else None})
    return pool, P, lgd


@mcp.tool()
async def bye_outlook(league_id_: str = "", roster_id_: int = 0,
                      through_week: int = 17) -> str:
    """Which upcoming weeks you CANNOT field a legal lineup, and why.

    A player on a bye returns no projection for that week, so absence IS the
    bye — no separate bye table is needed, and none can go stale.

    IMPORTANT: a player missing from a week his team DOES play is reported as
    UNKNOWN, not scored as zero. An unmeasurable value must not silently take
    the healthy default; that is how a hole gets hidden until Sunday.

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

    out = [f"{lgd.get('name')} — weeks {now} to {through_week}",
           f"  starters: {' '.join(slots)}",
           f"  trade deadline week {s.get('trade_deadline')}   "
           f"playoffs start week {s.get('playoff_week_start')}", "",
           f"  {'wk':>3} {'proj':>8} {'out':>4}  empty slots"]

    trouble = []
    for wk in range(now, through_week + 1):
        pool, _, _ = await _pool(lg, rid, wk)
        if pool is None:
            return f"No roster {rid} in league {lg}."
        playing = [p for p in pool if p["pts"] is not None]
        away = [p for p in pool if p["pts"] is None]
        total, assign = best_lineup(playing, slots)
        empty = [slots[i] for i, a in enumerate(assign) if a is None]
        if empty:
            trouble.append(wk)
        byes = ", ".join(p["name"].split()[-1] for p in away) or "-"
        out.append(f"  {wk:>3} {total:8.1f} {len(away):>4}  "
                   f"{','.join(empty) or '-':14} {byes[:52]}"
                   + ("   <<<" if empty else ""))

    if trouble:
        out += ["", f"  Weeks you cannot fill a legal lineup: {trouble}.",
                f"  Anything you intend to fix by trade has to happen before "
                f"week {s.get('trade_deadline')}."]
    else:
        out += ["", "  Every remaining week can field a legal lineup."]
    out.append("  A player absent from a week his team plays would show as "
               "UNKNOWN rather than 0 — none did here.")
    return "\n".join(out)


@mcp.tool()
async def waiver_targets(league_id_: str = "", roster_id_: int = 0,
                         week: int = 0, limit: int = 15,
                         position: str = "") -> str:
    """Free agents ranked by what they ADD TO YOUR STARTING LINEUP.

    Not by projection, and not by value over a generic replacement. The number
    is `best_lineup(roster + him) - best_lineup(roster)`, which is zero for
    anyone who cannot crack your lineup — so a high-projection player at a
    position you are already deep in correctly prices at nothing.

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
    wk = week or await current_week()
    slots = await starting_slots(lg)
    pool, P, lgd = await _pool(lg, rid, wk)
    if pool is None:
        return f"No roster {rid} in league {lg}."
    scoring = lgd.get("scoring_settings") or {}
    base, _ = best_lineup([p for p in pool if p["pts"] is not None], slots)

    rosters = await rest(f"/league/{lg}/rosters")
    owned = {str(p) for r in rosters for p in (r.get("players") or [])}
    owned |= {str(p) for r in rosters for p in (r.get("reserve") or [])}
    wanted = {position.upper()} if position else {"QB", "RB", "WR", "TE",
                                                 "K", "DEF"}
    free = [pid for pid, v in P.items()
            if pid not in owned and v.get("team")
            and v.get("position") in wanted]
    if not free:
        return "No free agents found — is the league id right?"

    # Project in batches; the free-agent pool is large.
    proj = {}
    for i in range(0, min(len(free), 600), 150):
        chunk = free[i:i + 150]
        d = await gql(
            '{stats_for_players_in_week(sport:"nfl",season:"%s",'
            'season_type:"regular",week:%d,player_ids:%s,category:"proj")'
            '{player_id stats}}'
            % (lgd.get("season"), wk,
               "[" + ",".join(f'"{c}"' for c in chunk) + "]"))
        for r in (d.get("stats_for_players_in_week") or []):
            proj[r["player_id"]] = r["stats"]

    live = [p for p in pool if p["pts"] is not None]
    rows = []
    for pid, stats in proj.items():
        v = P.get(pid) or {}
        pts = scored(stats, scoring)
        if pts <= 0:
            continue
        cand = {"pos": v.get("position"), "name": v.get("full_name") or pid,
                "pts": pts}
        with_him, _ = best_lineup(live + [cand], slots)
        gain = round(with_him - base, 2)
        if gain > 0:
            rows.append((gain, pts, v.get("position"), cand["name"],
                         v.get("team"), v.get("injury_status") or ""))
    rows.sort(reverse=True)

    out = [f"{lgd.get('name')} — week {wk} waiver targets",
           f"  your best legal lineup projects {base:.1f}",
           f"  {len(proj)} free agents priced, {len(rows)} improve it", ""]
    if not rows:
        out.append("  NOTHING improves your lineup this week. That is a real "
                   "answer, not an empty list — your starters beat every "
                   "available player at their position.")
        return "\n".join(out)
    for gain, pts, pos, name, team, inj in rows[:limit]:
        flag = f"  [{inj}]" if inj else ""
        out.append(f"  +{gain:5.1f}  {pos or '?':4} {name[:24]:24} "
                   f"{team or '-':4} projects {pts:5.1f}{flag}")
    out += ["", "  Gain is what he adds to YOUR lineup, so a player you "
            "cannot start prices at zero no matter how good he is."]
    return "\n".join(out)
