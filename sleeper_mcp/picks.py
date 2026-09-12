"""Draft value: what a pick cost against what it returned.

PURE. No MCP import, no network.

NO INVENTED BASELINE. The tempting move is to model "expected points at pick
37" and measure against that, which needs far more drafts than one league has
and quietly encodes whoever fitted it. Instead a pick is compared with the
players it was actually chosen ahead of:

    value = (Nth at his position taken) - (his finish among those players)

The eleventh quarterback off the board who finishes as QB3 is +8.

WITHIN POSITION, WHICH IS THE WHOLE POINT. Ranking every position together by
raw points looks reasonable and is badly wrong: quarterbacks outscore running
backs and receivers by a wide margin in most formats, so every late quarterback
scores as an enormous steal and every early receiver as a bust. The first
version of this did exactly that and returned a "best picks" list that was
nothing but quarterbacks and defences — an artifact of the scoring system, not
a fact about anybody's drafting.

The ranking is over DRAFTED players only. Ranking against everyone in the
league would penalise a manager for undrafted players he never had a chance to
take. What none of this can do is separate a good pick from a lucky one;
nothing that reads only outcomes can.
"""

from __future__ import annotations


def finish_ranks(points: dict[str, float]) -> dict[str, int]:
    """{player_id: points} -> {player_id: rank}, 1 = highest. Ties share."""
    ordered = sorted(points.items(), key=lambda kv: -kv[1])
    ranks, last_pts, last_rank = {}, None, 0
    for i, (pid, pts) in enumerate(ordered, start=1):
        if pts != last_pts:
            last_rank, last_pts = i, pts
        ranks[pid] = last_rank
    return ranks


def value(picks: list[dict], points: dict[str, float],
          positions: dict[str, str] | None = None) -> list[dict]:
    """Join picks to production, comparing each pick WITHIN ITS POSITION.

    Args:
        picks: [{pick_no, round, roster_id, player_id, is_keeper}].
        points: {player_id: season points}. A player absent from it scored
            nothing that counted — injured, cut, never active — and is kept at
            0.0 rather than dropped, because a pick that returned nothing is
            the most important kind of bust to show.
        positions: {player_id: "QB"}. Without it everyone falls into one pool,
            which is the cross-position distortion this exists to avoid.
    """
    positions = positions or {}
    pts, pos = {}, {}
    for p in picks:
        pid = str(p.get("player_id") or "")
        if not pid:
            continue
        pts[pid] = float(points.get(pid, 0.0))
        pos[pid] = positions.get(pid, "?")

    # Draft order and finish rank are both computed per position, so a pick is
    # measured against the players it was genuinely chosen ahead of.
    taken: dict[str, int] = {}
    order: dict[str, int] = {}
    for p in sorted((x for x in picks if x.get("player_id")),
                    key=lambda x: x.get("pick_no") or 0):
        pid = str(p["player_id"])
        group = pos[pid]
        taken[group] = taken.get(group, 0) + 1
        order[pid] = taken[group]

    finish: dict[str, int] = {}
    for group in set(pos.values()):
        members = {pid: pts[pid] for pid, g in pos.items() if g == group}
        finish.update(finish_ranks(members))

    rows = []
    for p in picks:
        pid = str(p.get("player_id") or "")
        if not pid:
            continue
        drafted_at, finished_at = order.get(pid), finish.get(pid)
        rows.append({
            "player_id": pid,
            "position": pos[pid],
            "pick_no": p.get("pick_no"),
            "round": p.get("round"),
            "roster_id": p.get("roster_id"),
            "is_keeper": bool(p.get("is_keeper")),
            "points": pts[pid],
            "pos_taken": drafted_at,
            "finish": finished_at,
            "value": (drafted_at - finished_at)
            if (drafted_at and finished_at) else None,
        })
    rows.sort(key=lambda r: -(r["value"] if r["value"] is not None else -10**6))
    return rows


def by_roster(rows: list[dict]) -> list[tuple]:
    """Per manager: picks made, total value, and the mean.

    Total rewards volume — a manager with more picks accumulates more of it —
    so the mean is what compares managers, and both are returned rather than
    one being chosen for the reader.
    """
    tally: dict[int, list[int]] = {}
    for r in rows:
        if r["value"] is not None and r["roster_id"] is not None:
            tally.setdefault(r["roster_id"], []).append(r["value"])
    out = [(rid, len(v), sum(v), sum(v) / len(v)) for rid, v in tally.items()]
    return sorted(out, key=lambda t: -t[3])
