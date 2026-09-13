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

# Which positions may fill which slot. Sleeper's flex names are stable, but an
# unknown slot deliberately falls back to "only its own position": that
# UNDER-counts rather than over-counts, so an unfamiliar league shows a slot it
# cannot fill instead of silently mispricing a player into it.
# The exact solver doubles in cost per slot. Measured on a 25-player roster:
# 9 slots 3ms, 12 slots 32ms, 14 slots 152ms, 16 slots 770ms, 18 slots 3.7s.
# A normal lineup has nine or ten, so 14 leaves generous headroom while
# refusing to hang a deep IDP league — beyond it the greedy approximation
# returns instead, which is wrong only for overlapping non-nested flex slots.
MAX_DP_SLOTS = 14

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

    ONE PATH, and it is exact. There used to be two — an exhaustive search for
    small inputs and a greedy pass for everything else — and they DISAGREED
    with each other above the threshold, which is the only reason the greedy
    pass survived as long as it did: every test roster was small enough to take
    the exhaustive branch, while every real roster was not.

    The greedy rule was "most-constrained slot first, best eligible player".
    That is exact when slot eligibility NESTS, and the docstring here used to
    claim Sleeper's does. It does not: WRRB_FLEX is {RB, WR} and REC_FLEX is
    {WR, TE}, overlapping without either containing the other. Given a 30-point
    receiver, a 25-point back and a 1-point tight end, greedy handed the
    receiver to the first slot and scored 31 where 55 was there to take.
    """
    live = [p for p in pool if p.get("pts") is not None]
    if not slots:
        return 0.0, []

    # EXACT, by dynamic programming over which slots are filled.
    #
    # This replaced a greedy pass — most-constrained slot first, best eligible
    # player — which is optimal only when slot eligibility NESTS. Sleeper's
    # does not: WRRB_FLEX is {RB, WR} and REC_FLEX is {WR, TE}, which overlap
    # without either containing the other. In a league running both, greedy
    # gave the best receiver to the first slot and left the second to a tight
    # end, scoring 31 where 55 was available.
    #
    # It was invisible because the two paths disagreed only above the
    # exact-search threshold: the same roster returned 55.0 with three players
    # and 31.0 once padded past ten. Every real roster is past ten.
    #
    # Cost is O(players x 2^slots x slots). A lineup has nine or ten slots, so
    # the state space is about a thousand; beyond MAX_DP_SLOTS the greedy pass
    # returns as a documented approximation rather than hanging.
    live = _prune(live, slots)

    if len(slots) > MAX_DP_SLOTS:
        return _greedy(live, slots)

    full = 1 << len(slots)
    # best[mask] = (points, assignment) using exactly the slots in `mask`
    best_of: list = [(0.0, None)] * full
    best_of[0] = (0.0, ())
    for p in live:
        pts = p["pts"]
        for mask in range(full - 1, -1, -1):
            cur = best_of[mask]
            if cur[1] is None:
                continue
            for i, slot in enumerate(slots):
                bit = 1 << i
                if mask & bit or not eligible(slot, p.get("pos")):
                    continue
                cand = (cur[0] + pts, cur[1] + ((i, p),))
                if cand[0] > best_of[mask | bit][0]:
                    best_of[mask | bit] = cand

    top = max(best_of, key=lambda x: x[0])
    assign: list = [None] * len(slots)
    for i, p in (top[1] or ()):
        assign[i] = p
    return round(top[0], 2), assign


def _prune(live: list[dict], slots: list[str]) -> list[dict]:
    """Drop players who cannot appear in ANY optimal lineup. Exact, not a
    heuristic.

    At most K players of a position can start, where K is the number of slots
    that position is eligible for — two WR slots and a FLEX means at most three
    receivers. And if k of them start, the best k by points always beat any
    other k, since swapping a lower scorer for a higher unused one never loses.
    So only the top K at each position can matter, and the rest cannot change
    the answer.

    Worth doing because the solver's cost is linear in players and exponential
    in slots: a fifteen-man roster usually prunes to nine or ten, and
    waiver_targets runs this several hundred times in a row.
    """
    capacity: dict[str, int] = {}
    for slot in slots:
        for pos in SLOT_ELIGIBILITY.get(slot, {slot}):
            capacity[pos] = capacity.get(pos, 0) + 1
    by_pos: dict = {}
    for p in live:
        by_pos.setdefault(p.get("pos"), []).append(p)
    kept = []
    for pos, group in by_pos.items():
        k = capacity.get(pos, 0)
        if k:
            kept.extend(sorted(group, key=lambda x: -x["pts"])[:k])
    return kept


def _greedy(live: list[dict], slots: list[str]) -> tuple[float, list]:
    """Approximate fallback for lineups too wide to solve exactly.

    Most-constrained slot first, best eligible player. Optimal only when slot
    eligibility nests, which Sleeper's does not — see best_lineup. Reached only
    above MAX_DP_SLOTS, which no ordinary lineup approaches.
    """
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


def gain(pool: list[dict], slots: list[str], candidate: dict,
         base: float | None = None) -> float:
    """What `candidate` would add to this lineup. Zero if he cannot start.

    Pass `base` when pricing many candidates against one roster — the baseline
    is identical every time, and recomputing it doubles the work of a sweep
    over several hundred free agents.
    """
    before = best_lineup(pool, slots)[0] if base is None else base
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
