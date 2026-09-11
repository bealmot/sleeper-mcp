"""Usage shares — what a player is being GIVEN, not what he scored.

PURE. No MCP import, no network. Takes stat rows, returns numbers.

WHY THIS EXISTS. Every other tool in this server prices players by projection,
and projections are rebuilt from box scores, which makes them a LAGGING
measure. Snap share and target share lead: a back whose snap share moved from
40% to 75% is this week's claim, and the projection will not say so until next
week, by which time he is rostered. Usage is the part of a fantasy week that
is decided by a coach rather than by variance, and it is the part that carries
forward.

A caution that applies to everything here: usage is a rate, and a rate over one
game is not evidence. Every summary carries the number of games behind it, for
the same reason season.py reports how much of an estimate is prior.
"""

from __future__ import annotations

# Sleeper's stat keys. Targets and carries are the two ways a coach hands a
# skill player a chance to score; adding them is the closest single number to
# "how much work did he get".
TARGETS = "rec_tgt"
CARRIES = "rush_att"
SNAPS = "off_snp"
TEAM_SNAPS = "tm_off_snp"
RED_ZONE = ("rec_rz_tgt", "rush_rz_att")


def _num(stats: dict, key: str) -> float:
    try:
        return float(stats.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def opportunity(stats: dict) -> float:
    """Targets plus carries — the chances a coach handed this player."""
    return _num(stats, TARGETS) + _num(stats, CARRIES)


def is_team_row(row: dict) -> bool:
    """Sleeper mixes a synthetic per-team aggregate in with the players.

    Its player_id is "TEAM_LAR" and its stats are the whole offence's. It is
    not a player and must never appear in a player list — and summing every
    row to get a team total counts the offence twice, which halves every share
    silently. That is how this was found: a receiver with 16 targets showed a
    9% share, which is wrong to anyone who watches football and invisible to
    any assertion about the numbers being between 0 and 1.
    """
    return str(row.get("player_id") or "").startswith("TEAM_")


def team_totals(rows: list[dict]) -> dict[tuple[str, int], dict]:
    """(team, week) -> {opp, targets, carries}, the denominators for shares.

    PREFERS SLEEPER'S OWN TEAM ROW. Summing the players works only when the
    query returned every position; the aggregate row is right regardless, so a
    position-filtered query still produces correct shares instead of ones
    inflated past 1.0. Falls back to summing players when no team row is
    present.
    """
    totals: dict[tuple[str, int], dict] = {}
    summed: dict[tuple[str, int], dict] = {}
    for r in rows:
        team, week = r.get("team"), r.get("week")
        if not team or week is None:
            continue
        st = r.get("stats") or {}
        entry = {"opp": opportunity(st), "targets": _num(st, TARGETS),
                 "carries": _num(st, CARRIES)}
        if is_team_row(r):
            totals[(team, week)] = entry
        else:
            acc = summed.setdefault((team, week),
                                    {"opp": 0.0, "targets": 0.0, "carries": 0.0})
            for k in acc:
                acc[k] += entry[k]
    for key, acc in summed.items():
        totals.setdefault(key, acc)
    return totals


def team_opportunity(rows: list[dict]) -> dict[tuple[str, int], float]:
    """(team, week) -> total targets + carries. Thin wrapper on team_totals."""
    return {k: v["opp"] for k, v in team_totals(rows).items()}


def week_usage(row: dict, totals: dict) -> dict:
    """One player, one week: the rates, plus the raw counts behind them."""
    st = row.get("stats") or {}
    snaps, team_snaps = _num(st, SNAPS), _num(st, TEAM_SNAPS)
    opp, tgt = opportunity(st), _num(st, TARGETS)
    tot = totals.get((row.get("team"), row.get("week"))) or {}
    t_opp, t_tgt = tot.get("opp", 0.0), tot.get("targets", 0.0)
    return {
        "week": row.get("week"),
        "team": row.get("team"),
        "opponent": row.get("opponent"),
        "snaps": snaps,
        "snap_share": (snaps / team_snaps) if team_snaps > 0 else None,
        "targets": tgt,
        "target_share": (tgt / t_tgt) if t_tgt > 0 else None,
        "carries": _num(st, CARRIES),
        "opportunity": opp,
        "opp_share": (opp / t_opp) if t_opp > 0 else None,
        "red_zone": sum(_num(st, k) for k in RED_ZONE),
        "points": _num(st, "pts_half_ppr"),
    }


def collect(rows: list[dict]) -> dict[str, list[dict]]:
    """-> {player_id: [week_usage, ...]} sorted oldest week first."""
    totals = team_totals(rows)
    out: dict[str, list[dict]] = {}
    for r in rows:
        pid = r.get("player_id")
        if pid and not is_team_row(r):        # the aggregate is not a player
            out.setdefault(str(pid), []).append(week_usage(r, totals))
    for weeks in out.values():
        weeks.sort(key=lambda w: w["week"] if w["week"] is not None else -1)
    return out


def _mean(values: list) -> float | None:
    real = [v for v in values if v is not None]
    return sum(real) / len(real) if real else None


def trend(weeks: list[dict], recent: int = 2) -> dict:
    """Split a player's weeks into recent and baseline, and compare.

    `delta` is the change in share of his team's work. It is None when there is
    no baseline to compare against, which is the ordinary state of the first
    few weeks of a season — NOT zero. Zero would claim a player's role is
    unchanged when nothing is known about what it was.
    """
    played = [w for w in weeks if w["snaps"] > 0 or w["opportunity"] > 0]
    tail, head = played[-recent:], played[:-recent]
    r_opp, b_opp = _mean([w["opp_share"] for w in tail]), \
        _mean([w["opp_share"] for w in head])
    r_snap, b_snap = _mean([w["snap_share"] for w in tail]), \
        _mean([w["snap_share"] for w in head])
    return {
        # From the STAT ROW, not the player dictionary. The dictionary holds a
        # player's team TODAY, which is not where he earned a past season's
        # snaps — it listed a 2025 breakout under the club he signed with
        # afterwards.
        "team": played[-1]["team"] if played else None,
        "games": len(played),
        "recent_games": len(tail),
        "base_games": len(head),
        "opp_share": r_opp,
        "base_opp_share": b_opp,
        "delta": (r_opp - b_opp) if (r_opp is not None and b_opp is not None)
        else None,
        "snap_share": r_snap,
        "base_snap_share": b_snap,
        "snap_delta": (r_snap - b_snap)
        if (r_snap is not None and b_snap is not None) else None,
        "target_share": _mean([w["target_share"] for w in tail]),
        "opportunity": _mean([w["opportunity"] for w in tail]) or 0.0,
        "red_zone": sum(w["red_zone"] for w in tail),
        "points": _mean([w["points"] for w in tail]) or 0.0,
    }


def rank(trends: dict[str, dict], min_snap_share: float = 0.25) -> list[tuple]:
    """Order players by rising role, falling back to level when new.

    Sorted by `delta` where a baseline exists, because a role that is GROWING
    is the actionable signal — a player already at a steady 70% is priced in.
    Players with no baseline cannot be ranked that way and are ordered by their
    current share instead; they are kept rather than dropped, since in week 2
    that is everybody.

    `min_snap_share` filters out one-week noise: a player on the field for a
    fifth of his team's downs did not have a role change, he had a garbage-time
    drive.
    """
    out = []
    for pid, t in trends.items():
        snap = t["snap_share"]
        if snap is not None and snap < min_snap_share:
            continue
        if t["opp_share"] is None:
            continue
        out.append((t["delta"], t["opp_share"], pid, t))
    # Rising role first. Players with no baseline sort after everyone who has
    # one, then among themselves by current share — in week 2 that is the whole
    # list, which is the honest outcome rather than a fabricated ordering.
    out.sort(key=lambda x: (x[0] is None, -(x[0] or 0.0), -x[1]))
    return out
