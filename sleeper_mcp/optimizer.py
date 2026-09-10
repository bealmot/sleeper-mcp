"""Optimal-lineup maths. PURE — no MCP, no network, no configuration.

Kept separate from the tools that use it so the logic can be tested with plain
Python and no framework installed. It is also the part most worth getting
right: every waiver price and bye-week hole in this server is derived from it.

The central idea: **a player's value is what he adds to YOUR lineup.**

    gain(player) = best_lineup(roster + player) - best_lineup(roster)

which is zero by construction for anyone who cannot crack the lineup. A sixth
receiver projecting 12 is worth nothing behind four better ones; a tight end
projecting 11 is worth ten points when your only other one is on a bye.
Positional rankings cannot express that, because they do not know your roster.
"""

from __future__ import annotations

import itertools

# Which positions may fill which slot. Sleeper's flex names are stable, but an
# unknown slot deliberately falls back to "only its own position": that
# UNDER-counts rather than over-counts, so an unfamiliar league shows a slot it
# cannot fill instead of silently mispricing a player into it.
SLOT_ELIGIBILITY: dict[str, set[str]] = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"},
    "K": {"K"}, "DEF": {"DEF"},
    "DL": {"DL"}, "LB": {"LB"}, "DB": {"DB"},
    "IDP_FLEX": {"DL", "LB", "DB"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "WRRB_WRT": {"RB", "WR", "TE"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}


def eligible(slot: str, position: str | None) -> bool:
    """May a player at `position` fill `slot`?"""
    return position in SLOT_ELIGIBILITY.get(slot, {slot})


def best_lineup(pool: list[dict], slots: list[str]) -> tuple[float, list]:
    """Highest-scoring legal lineup, and which player fills each slot.

    `pool` entries need at least {"pos", "pts"}; anything with `pts` of None is
    treated as unavailable (a bye, or no projection) and excluded.

    Returns `(total, assignment)` where assignment[i] is the player in slots[i],
    or None if that slot could not be filled — an unfillable slot is reported,
    never silently skipped.

    Slots are filled MOST-CONSTRAINED-FIRST: a slot only one position can fill
    is decided before a flex that several could. For the nested eligibility
    sets Sleeper uses — FLEX is a superset of RB/WR/TE rather than an
    overlapping alternative — that ordering is exact. Small rosters take an
    exhaustive path instead, so the common case is provably optimal rather than
    merely probably.
    """
    live = [p for p in pool if p.get("pts") is not None]
    if not slots:
        return 0.0, []

    if len(live) <= 10 and len(slots) <= 8:
        best, assign = -1.0, [None] * len(slots)
        for perm in itertools.permutations(live, min(len(slots), len(live))):
            if any(not eligible(slots[i], p.get("pos"))
                   for i, p in enumerate(perm)):
                continue
            tot = sum(p["pts"] for p in perm)
            if tot > best:
                best = tot
                assign = list(perm) + [None] * (len(slots) - len(perm))
        if best >= 0:
            return round(best, 2), assign

    order = sorted(range(len(slots)),
                   key=lambda i: len(SLOT_ELIGIBILITY.get(slots[i],
                                                          {slots[i]})))
    taken: set[int] = set()
    assign: list = [None] * len(slots)
    for i in order:
        cands = [(j, p) for j, p in enumerate(live)
                 if j not in taken and eligible(slots[i], p.get("pos"))]
        if not cands:
            continue
        j, pick = max(cands, key=lambda jp: jp[1]["pts"])
        assign[i] = pick
        taken.add(j)
    return round(sum(p["pts"] for p in assign if p), 2), assign


def gain(pool: list[dict], slots: list[str], candidate: dict) -> float:
    """What `candidate` would add to this lineup. Zero if he cannot start."""
    before, _ = best_lineup(pool, slots)
    after, _ = best_lineup(pool + [candidate], slots)
    return round(after - before, 2)


def holes(pool: list[dict], slots: list[str]) -> list[str]:
    """Slots that cannot be filled at all from this pool."""
    _, assign = best_lineup(pool, slots)
    return [slots[i] for i, a in enumerate(assign) if a is None]


def percentile_within(values: dict[str, float]) -> dict[str, float]:
    """Value -> percentile rank (0-100) within this group. Ties share a rank.

    Used to make two incomparable scales comparable. A projection in points and
    a conviction count cannot be subtracted, and a quarterback's 22 points are
    not a tight end's 22 — but "where does he sit among quarterbacks according
    to each source" is one question both can answer.

    Lives here rather than beside the tools that use it so it can be tested
    without the MCP framework installed.
    """
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: kv[1])
    n = len(ordered)
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        pct = 100.0 * (i + j) / 2 / max(n - 1, 1)
        for k in range(i, j + 1):
            out[ordered[k][0]] = round(pct, 1)
        i = j + 1
    return out
