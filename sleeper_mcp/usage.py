"""Usage tools — snap share, target share, and whose role is growing.

The maths lives in shares.py, which is pure and tested. This module fetches,
joins and formats.

WHAT THESE ADD THAT NOTHING ELSE HERE DOES. Every other tool prices players by
projection, and projections are rebuilt from box scores — so they describe the
week that happened. These describe the week a coach is planning: snaps and
touches, which carry forward, and which move BEFORE the points do.

POINTS ARE THE LEAGUE'S, NOT A PRESET. Every points column here is scored
against the configured league's `scoring_settings`; only with no league
configured does it fall back to Sleeper's half-PPR preset, and then it says so.
"""

from __future__ import annotations

import asyncio

from .client import READ, gql, league, league_id, players, rest, state, tool
from .lookup import (FANTASY_POSITIONS, ambiguous, display_name,
                     fantasy_position, find_player, positions)
from .shares import collect, rank, trend

SEASON_TYPE = "regular"

# weekly_stats requires both of these. `category` is "stat", singular, and
# `order_by` is String! — NOT NULLABLE — so the obvious minimal query fails with
# a type error rather than a useful one. Neither fact is documented anywhere.
CATEGORY = "stat"
ORDER_BY = "pts_half_ppr"

# The last week of the fantasy regular season. Week 18 exists in the data but
# is when clinched teams rest starters, so a window ending there reads a
# healthy player's rest game as a role collapse.
LAST_FANTASY_WEEK = 17

SKILL = ("QB", "RB", "WR", "TE")


async def _scoring(league_id_: str = "") -> tuple[dict | None, str]:
    """The league's scoring settings, and a label for the points column."""
    from . import client
    lg = str(league_id_ or client.DEFAULT_LEAGUE or "").strip()
    if not lg:
        return None, "pts (half-PPR preset — no league configured)"
    cfg = await league(league_id(lg))
    return cfg.get("scoring_settings") or None, "pts (league scoring)"


async def _week(season: str, week: int) -> list[dict]:
    """One week of stats for EVERY position.

    Deliberately unfiltered by position. shares.py builds its denominators from
    these same rows, so dropping running backs and tight ends would inflate
    every receiver's target share past 1.0 — a query bug that presents as a
    maths bug.
    """
    q = ('{weekly_stats(sport:"nfl",season:"%s",season_type:"%s",week:%d,'
         'category:"%s",order_by:"%s"){player_id team opponent week stats}}'
         % (season, SEASON_TYPE, week, CATEGORY, ORDER_BY))
    return (await gql(q)).get("weekly_stats") or []


async def _history(season: str, through: int,
                   weeks: int) -> tuple[list[dict], list[int]]:
    """The last `weeks` COMPLETED weeks, fetched concurrently.

    Returns (rows, failed_weeks). A week that fails to fetch used to be
    swallowed and return an empty list, which is indistinguishable from a week
    nobody played — the trend then quietly covered fewer games than it claimed
    and said nothing. The caller is told which weeks are missing instead.
    """
    first = max(1, through - weeks + 1)
    wanted = list(range(first, through + 1))
    got = await asyncio.gather(*(_week(season, w) for w in wanted),
                               return_exceptions=True)
    rows, failed = [], []
    for w, batch in zip(wanted, got):
        if isinstance(batch, BaseException):
            failed.append(w)
        else:
            rows.extend(batch)
    return rows, failed


async def _completed(season: str | None) -> tuple[str, int, str]:
    """Which season, and the last week that has actually finished.

    A week in progress is not history — the same rule season.split_games
    enforces, for the same reason: mid-week a player shows one game's worth of
    a Thursday and it looks like a collapsed role.

    WHEN NOTHING HAS FINISHED THIS SEASON, THESE TOOLS SAY SO. They do not
    quietly substitute last year. Last year's snap shares are a different
    coaching staff, a different depth chart and in many cases a different team,
    and presenting them under this season's heading would be a confident answer
    to a question nobody asked.
    """
    st = await state()
    cur_season = str(st.get("season") or "")
    cur_week = int(st.get("week") or 0)
    if season and season != cur_season:
        return season, LAST_FANTASY_WEEK, ""
    return cur_season, cur_week - 1, cur_season


def _pct(v) -> str:
    return f"{v * 100:5.0f}%" if v is not None else "    -"


@tool(annotations=READ)
async def usage(player_name: str, weeks: int = 5, season: str = "",
                league_id_: str = "") -> str:
    """How much work one player is actually getting, week by week.

    Snap share is the share of his own team's offensive plays he was on the
    field for. Target share is his cut of the passing game. Opportunity share
    is his share of the team's targets AND carries, so it compares a receiver
    with a running back on one scale. All three lead fantasy points: a role
    changes first and the scoring follows, which is why this answers "is he
    getting more work?" rather than "did he score?"

    Args:
        player_name: Full or partial name, or a Sleeper player id.
        weeks: How many completed weeks to show. Default 5.
        season: Look at a past season, e.g. "2025". Defaults to the current one.
        league_id_: League whose scoring prices the pts column. Defaults to
            SLEEPER_LEAGUE_ID.
    """
    P = await players()
    hits = find_player(P, player_name, allow_unsigned=True)
    if len(hits) != 1:
        return ambiguous(player_name, hits)
    pid, v = hits[0]

    szn, through, _cur = await _completed(season or None)
    if through < 1:
        return (f"  No completed weeks in {szn} yet — week {through + 1} is "
                f"still being played, and a week in progress is not usage.\n"
                f"  Pass season=\"{int(szn) - 1}\" to look at last year.")

    scoring, pts_label = await _scoring(league_id_)
    rows, failed = await _history(szn, through, weeks)
    by_player = collect(rows, scoring)
    mine = by_player.get(str(pid))
    if not mine:
        return (f"  No {szn} usage recorded for {display_name(v, pid)} "
                f"through week {through}.")

    t = trend(mine)
    first = max(1, through - weeks + 1)
    # The team is the one he EARNED THESE SNAPS FOR, from the stat rows —
    # the dictionary holds where he plays today.
    team = t.get("team") or v.get("team") or "?"
    out = [f"  {display_name(v, pid)} — {fantasy_position(v)} {team}   id={pid}"
           f"   {szn} weeks {first}-{through}: {t['games']} game(s) played"]
    if failed:
        out.append(f"  INCOMPLETE — week(s) {failed} could not be fetched, so "
                   f"this is missing data, not missing usage.")
    out += ["",
            f"  {'wk':>3} {'opp':>4} {'tgt':>4} {'car':>4} {'snap%':>6} "
            f"{'tgt%':>6} {'opp%':>6} {'rz':>3} {'pts':>6}"]
    for w in mine:
        out.append(f"  {w['week']:>3} {w['opportunity']:>4.0f} "
                   f"{w['targets']:>4.0f} {w['carries']:>4.0f} "
                   f"{_pct(w['snap_share']):>6} {_pct(w['target_share']):>6} "
                   f"{_pct(w['opp_share']):>6} {w['red_zone']:>3.0f} "
                   f"{w['points']:>6.1f}")

    if t["delta"] is None:
        out += ["", f"  Not enough weeks to show a trend — {t['games']} game(s) "
                    f"is a level, not a direction."]
    else:
        out += ["", f"  opportunity share {_pct(t['base_opp_share']).strip()} -> "
                    f"{_pct(t['opp_share']).strip()} ({t['delta'] * 100:+.0f}pp), "
                    f"snaps {_pct(t['base_snap_share']).strip()} -> "
                    f"{_pct(t['snap_share']).strip()}"]
    if t["snap_games"] < t["games"]:
        out.append(f"  {t['games'] - t['snap_games']} week(s) carry no snap data "
                   f"and are left out of the snap averages.")
    out.append(f"  opp = targets + carries. {pts_label}.")
    return "\n".join(out)


@tool(annotations=READ)
async def breakouts(position: str = "", weeks: int = 4, limit: int = 12,
                    min_snap_share: float = 0.25, season: str = "",
                    league_id_: str = "") -> str:
    """Free agents in your league whose ROLE is growing.

    This is the gap waiver_targets cannot close on its own. That tool prices
    players by projection, and projections are rebuilt from box scores, so they
    move a week after the usage does — by which time the player is rostered.
    This ranks by the change in a player's share of his team's targets and
    carries, which is the coach's decision and the thing that carries forward.

    Args:
        position: QB, RB, WR, TE. Blank means all.
        weeks: Completed weeks to consider. Default 4.
        limit: How many to list. Default 12.
        min_snap_share: Ignore players below this share of their team's plays.
            Default 0.25 — under that a spike is garbage time, not a promotion.
            A player whose rows carry no snap data cannot be filtered by it
            and is shown with '-'.
        season: A past season, e.g. "2025". Defaults to the current one.
        league_id_: Override the configured league.
    """
    lg = league_id(league_id_ or None)
    if position and position.upper() not in SKILL:
        return f"position must be one of {'/'.join(SKILL)}, got {position!r}."
    szn, through, _cur = await _completed(season or None)
    if through < 1:
        return (f"  No completed weeks in {szn} yet — week {through + 1} is "
                f"still being played.\n  Pass season=\"{int(szn) - 1}\" to see "
                f"how roles finished last year.")

    scoring, pts_label = await _scoring(lg)
    P, rosters, history = await asyncio.gather(
        players(), rest(f"/league/{lg}/rosters"),
        _history(szn, through, weeks))
    rows, failed = history

    owned = set()
    for r in (rosters or []):
        for key in ("players", "reserve", "taxi"):
            owned |= {str(p) for p in (r.get(key) or [])}
    wanted = {position.upper()} if position else set(SKILL)

    by_player = collect(rows, scoring)
    # Filtered by position only. Requiring a CURRENT team dropped every
    # player cut or retired since a past season from that season's list; the
    # team he played for is on the stat rows.
    trends = {pid: trend(w) for pid, w in by_player.items()
              if pid not in owned and (positions(P.get(pid)) & wanted)}
    ranked = rank(trends, min_snap_share=min_snap_share)
    if not ranked:
        return (f"  No available player is above {min_snap_share * 100:.0f}% "
                f"of his team's snaps. Lower min_snap_share to see more.")

    first = max(1, through - weeks + 1)
    have_delta = any(d is not None for d, _s, _p, _t in ranked)
    head = (f"  Free agents by change in role — {szn} weeks {first}-{through}"
            if have_delta else
            f"  Free agents by current role — {szn} weeks {first}-{through}")
    if failed:
        head += f"   INCOMPLETE: week(s) {failed} failed to fetch"
    note = ("" if have_delta else
            "  Too early for a trend: nobody has weeks on both sides of the "
            "window, so this is ranked by CURRENT share, not by change.")

    out = [head] + ([note] if note else []) + [
        "", f"  {'player':22} {'pos':>3} {'tm':>3} {'snap%':>6} {'opp%':>6} "
            f"{'chg':>6} {'opp/g':>6} {'rz':>3} {'pts/g':>6}"]
    no_snaps = 0
    for _d, _s, pid, t in ranked[:limit]:
        v = P.get(pid) or {}
        chg = f"{t['delta'] * 100:+5.0f}pp" if t["delta"] is not None else "     -"
        team = t.get("team") or v.get("team") or "?"
        if t["snap_share"] is None:
            no_snaps += 1
        out.append(f"  {display_name(v, pid)[:22]:22} "
                   f"{fantasy_position(v) or '?':>3} {team:>3} "
                   f"{_pct(t['snap_share']):>6} {_pct(t['opp_share']):>6} "
                   f"{chg:>6} {t['opportunity']:6.1f} {t['red_zone']:>3.0f} "
                   f"{t['points']:6.1f}")
    out += ["", "  opp = targets + carries. chg = change in share of the team's "
                "opportunities", "  against the earlier weeks in the window. "
                f"rz = red-zone looks. {pts_label}."]
    if no_snaps:
        out.append(f"  {no_snaps} row(s) show snap% '-': Sleeper published no "
                   f"snap counts for those games, so the garbage-time filter "
                   f"could not be applied to them.")
    if not position:
        out.append("  Running backs dominate an unfiltered list: carries "
                   "concentrate on one man, so their")
        out.append("  share of a team's work is structurally higher than a "
                   "receiver's. Pass position= to compare like with like.")
    return "\n".join(out)


# Curated metrics for season_leaders. Raw Sleeper keys work too, but these are
# the ones worth ranking by, and several are rates this module computes rather
# than fields Sleeper returns.
METRICS = {
    "points": None,               # league-scored, computed
    "targets": "rec_tgt",
    "carries": "rush_att",
    "opportunity": None,          # targets + carries, computed
    "target_share": None,         # computed against the team row
    "opp_share": None,
    "snap_share": None,
    "rec_yards": "rec_yd",
    "rush_yards": "rush_yd",
    "red_zone": None,
}


async def season_rows(season: str) -> list[dict]:
    """Every player's season totals, UNFILTERED BY POSITION.

    The position filter strips the synthetic per-team rows: asking for WR
    returns 1,364 rows and zero TEAM_ entries, against 8,248 rows and 32 team
    rows unfiltered. Those team rows are the denominators, so filtering at the
    query and then computing shares divides a receiver's targets by the WR-only
    total and inflates every share. Filter locally instead.

    `week` is None on a season aggregate, and shares.team_totals keys its
    buckets by (team, week) while skipping rows with no week — so these are
    stamped week 0. One bucket per team is exactly right for a season.
    """
    q = ('{season_stats(sport:"nfl",season:"%s",season_type:"regular",'
         'category:"%s",order_by:"%s"){player_id team stats}}'
         % (season, CATEGORY, ORDER_BY))
    rows = (await gql(q)).get("season_stats") or []
    return [{**r, "week": 0} for r in rows]


@tool(annotations=READ)
async def season_leaders(position: str = "", metric: str = "points",
                         per_game: bool = True, min_games: int = 4,
                         limit: int = 15, season: str = "",
                         league_id_: str = "") -> str:
    """Season-long leaders, by RATE rather than accumulation by default.

    Season totals are the most misleading number in fantasy: they reward
    availability as much as quality, so a player who missed five games ranks
    below a worse one who did not. Ranking per game separates those, and games
    played is shown either way so the trade-off stays visible. Shares are per
    game too, so a missed month does not shrink a player's role.

    Args:
        position: QB, RB, WR, TE, K or DEF. Blank means the skill positions.
            Kickers and defences matter in leagues that score them heavily.
        metric: points, targets, carries, opportunity, target_share, opp_share,
            snap_share, rec_yards, rush_yards, red_zone — or a raw Sleeper stat
            key that appears in the season rows.
        per_game: Divide counting stats by games played. Default True. Shares
            are already rates and are unaffected.
        min_games: Ignore players below this many games. Default 4 — a rate
            over one game is not a rate. Early in a season nobody clears it;
            the output says so and names the maximum seen.
        limit: How many to list. Default 15.
        season: Defaults to the current season once a week has finished, and
            to the previous season before then. Pass "2025" for last year.
        league_id_: League whose scoring prices `points`. Defaults to
            SLEEPER_LEAGUE_ID.
    """
    if position and position.upper() not in FANTASY_POSITIONS:
        return (f"position must be one of {'/'.join(FANTASY_POSITIONS)}, "
                f"got {position!r}.")
    szn, through, _cur = await _completed(season or None)
    if not season and through < 1:
        szn = str(int(szn) - 1)          # nothing finished this year yet

    scoring, pts_label = await _scoring(league_id_)
    P, rows = await asyncio.gather(players(), season_rows(szn))
    if not rows:
        return f"  No season stats for {szn}."

    known_keys = set()
    for r in rows:
        known_keys.update((r.get("stats") or {}).keys())
    if metric not in METRICS and metric not in known_keys:
        return (f"  Unknown metric {metric!r}. Known: {', '.join(sorted(METRICS))}"
                f" — or any raw Sleeper stat key, e.g. rec_td, pass_yd.")

    by_player = collect(rows, scoring)   # drops TEAM_ rows, keeps them as totals
    # Index once. Scanning `rows` inside the loop is quadratic over 8,000+ rows.
    raw = {str(r.get("player_id")): (r.get("stats") or {}) for r in rows}
    wanted = {position.upper()} if position else set(SKILL)
    key = METRICS.get(metric, metric)

    table, max_gp = [], 0.0
    for pid, weeks in by_player.items():
        v = P.get(pid) or {}
        if not (positions(v) & wanted):
            continue
        w = weeks[0]
        st = raw.get(pid, {})
        gp = float(st.get("gp") or 0)
        max_gp = max(max_gp, gp)
        if gp < min_games:
            continue

        if metric in ("target_share", "opp_share", "snap_share"):
            value, rate = w.get(metric), True
        elif metric == "opportunity":
            value, rate = w["opportunity"], False
        elif metric == "red_zone":
            value, rate = w["red_zone"], False
        elif metric == "points":
            value, rate = w["points"], False
        else:
            value, rate = float(st.get(key) or 0), False
        if value is None:
            continue
        shown = (value / gp) if (per_game and not rate and gp) else value
        table.append((shown, value, gp, pid, v, w))

    if not table:
        return (f"  No {position or 'skill'} player has {min_games}+ games in "
                f"{szn} yet (most seen: {max_gp:.0f}). Lower min_games, or pass "
                f"season=\"{int(szn) - 1}\" for a finished season.")
    table.sort(key=lambda t: -t[0])

    is_share = metric.endswith("_share")
    unit = "" if is_share else ("/g" if per_game else "")
    head = [f"  {szn} {position or 'skill'} leaders by {metric}{unit}"
            f"   (min {min_games} games)"]
    if per_game and not is_share:
        head.append("  Ranked PER GAME — season totals reward availability as "
                    "much as quality.")
    out = head + ["",
                  f"  {'player':22} {'pos':>3} {'tm':>3} {'gp':>4} "
                  f"{metric[:13] + unit:>15} {'total':>9} {'tgt%':>6} "
                  f"{'snap%':>6}"]
    for shown, total, gp, pid, v, w in table[:limit]:
        fmt = f"{shown * 100:14.1f}%" if is_share else f"{shown:15.1f}"
        tot = "-" if is_share else f"{total:9.0f}"
        out.append(f"  {display_name(v, pid)[:22]:22} "
                   f"{fantasy_position(v) or '?':>3} {w.get('team') or '?':>3} "
                   f"{gp:4.0f} {fmt} {tot:>9} {_pct(w['target_share']):>6} "
                   f"{_pct(w['snap_share']):>6}")
    out.append(f"  tgt% and opp% are per game (player's rate over the team's); "
               f"{pts_label if metric == 'points' else 'snap% counts only games he played'}.")
    return "\n".join(out)
