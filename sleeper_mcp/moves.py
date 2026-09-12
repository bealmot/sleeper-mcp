"""Reading a transaction from ONE player's point of view.

PURE. No MCP import, no network.

THE TRAP THIS MODULE EXISTS FOR. `league_transactions_by_player` returns every
transaction that TOUCHED a player, in either direction — including ones where
he was the corresponding drop and somebody else was the point of the move. A
row can read `adds: {"10226": 5}, drops: {"9754": 5}`, which is a claim on a
different player entirely. Rendering these as a list of acquisitions produces a
history in which a player was signed six times and never released.

So every row is classified RELATIVE to the player asked about, and the same row
means different things depending on whose history it appears in.
"""

from __future__ import annotations

import datetime as dt

DRAFTED = "drafted"
ADDED = "added"
DROPPED = "dropped"
TRADED = "traded"

# Sleeper's own type strings, mapped to something a person would say.
HOW = {
    "draft_pick": "draft",
    "free_agent": "free agent",
    "waiver": "waiver",
    "trade": "trade",
    "commissioner": "commissioner",
}


def when(created_ms) -> str:
    """Sleeper timestamps are epoch MILLISECONDS. Seconds gives 1970."""
    try:
        return dt.datetime.fromtimestamp(
            int(created_ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return "?"


def classify(txn: dict, player_id: str) -> dict | None:
    """What this transaction did TO THIS PLAYER. None if it did nothing.

    A trade moves him between two rosters, so he appears in both adds and
    drops; that ordering is what separates a trade from an add that happened
    to cut someone else.
    """
    pid = str(player_id)
    adds = {str(k): v for k, v in (txn.get("adds") or {}).items()}
    drops = {str(k): v for k, v in (txn.get("drops") or {}).items()}
    to_roster, from_roster = adds.get(pid), drops.get(pid)
    if to_roster is None and from_roster is None:
        return None

    kind = (TRADED if to_roster is not None and from_roster is not None
            else ADDED if to_roster is not None else DROPPED)
    how = HOW.get(txn.get("type"), txn.get("type") or "?")

    # A SEASON ROLLOVER RELEASES THE WHOLE ROSTER IN ONE TRANSACTION, typed
    # `draft_pick` with no adds and a dozen drops — the previous year's squad
    # cleared before the new draft. Treated as an ordinary cut it renders as
    # "dropped him for <three arbitrary team-mates>", which is wrong about both
    # the reason and the other names, and typed as a "draft" it reads as
    # nonsense. It is neither an add nor a considered drop, so it is named.
    bulk = len(adds) + len(drops) > 4
    if kind == ADDED and txn.get("type") == "draft_pick":
        kind = DRAFTED
    elif kind == DROPPED and txn.get("type") == "draft_pick" and bulk:
        how = "roster reset"

    # Everyone else in the same transaction — who he was picked up over, or
    # who came back the other way in a trade.
    others = sorted({p for p in list(adds) + list(drops) if p != pid})
    return {
        "kind": kind,
        "how": how,
        "bulk": bulk,
        "week": txn.get("leg"),
        "date": when(txn.get("created")),
        "created": txn.get("created") or 0,
        "to_roster": to_roster,
        "from_roster": from_roster,
        "others": others,
        "faab": txn.get("waiver_budget"),
        "status": txn.get("status"),
    }


def history(txns: list[dict], player_id: str) -> list[dict]:
    """Every move involving this player, newest first, drafts included."""
    out = [c for c in (classify(t, player_id) for t in (txns or [])) if c]
    out.sort(key=lambda c: c["created"], reverse=True)
    return out


def churn(moves: list[dict]) -> dict:
    """How contested a player has been.

    `rosters` counts the distinct teams that have held him. A player added and
    dropped repeatedly by ONE manager is a different story from one four teams
    have tried, and a plain transaction count cannot tell them apart.
    """
    rosters = {m["to_roster"] for m in moves if m["to_roster"] is not None}
    rosters |= {m["from_roster"] for m in moves if m["from_roster"] is not None}
    return {
        "moves": len(moves),
        "adds": sum(1 for m in moves if m["kind"] in (ADDED, DRAFTED)),
        "drops": sum(1 for m in moves if m["kind"] == DROPPED),
        "trades": sum(1 for m in moves if m["kind"] == TRADED),
        "rosters": len(rosters),
    }


# --- outcomes ---------------------------------------------------------------

# Statuses Sleeper uses. Only `complete` actually happened; the rest are the
# interesting part, because they are what a league TRIED to do.
COMPLETE = "complete"


def outcomes(rows: list[dict]) -> dict:
    """Counts by type and status, with the completion rate per type.

    The rate is the point. A league that proposed 36 trades and completed 4 is
    not a quiet league, it is a league where nobody accepts — and a list of
    completed transactions cannot tell those apart, because it contains only
    the four.
    """
    by_type: dict[str, dict[str, int]] = {}
    for r in rows or []:
        t = r.get("type") or "?"
        s = r.get("status") or "?"
        by_type.setdefault(t, {})
        by_type[t][s] = by_type[t].get(s, 0) + 1
    out = {}
    for t, counts in by_type.items():
        total = sum(counts.values())
        done = counts.get(COMPLETE, 0)
        out[t] = {"counts": counts, "total": total, "complete": done,
                  "rate": (done / total) if total else 0.0}
    return out


def by_manager(rows: list[dict], names: dict) -> list[tuple]:
    """Who initiated what, and how much of it stuck.

    A transaction lists every roster it touches, so a trade counts for both
    sides. That is deliberate: "was involved in" is the answerable question,
    while "who proposed it" is not reliably in the data.
    """
    tally: dict[int, dict[str, int]] = {}
    for r in rows or []:
        for rid in (r.get("roster_ids") or []):
            slot = tally.setdefault(rid, {"total": 0, "complete": 0})
            slot["total"] += 1
            if r.get("status") == COMPLETE:
                slot["complete"] += 1
    return sorted(((names.get(rid, f"roster {rid}"), v["total"], v["complete"])
                   for rid, v in tally.items()),
                  key=lambda x: -x[1])


# --- traded draft picks -----------------------------------------------------

# GraphQL returns traded picks as COMMA-SEPARATED STRINGS, while REST returns
# objects for the same trade. Field order was read off both forms of one
# transaction rather than guessed:
#
#   GraphQL "9,2026,6,5,9"
#   REST    {roster_id: 9, season: "2026", round: 6, owner_id: 5,
#            previous_owner_id: 9}
#
# A renderer that expects the object silently drops the string, and a trade
# whose other half was a pick then displays as one side receiving nothing.
PICK_FIELDS = ("roster_id", "season", "round", "owner_id", "previous_owner_id")


def parse_draft_pick(value) -> dict | None:
    """Normalise a traded pick from either form. None if unreadable."""
    if isinstance(value, dict):
        return {k: value.get(k) for k in PICK_FIELDS}
    if not isinstance(value, str):
        return None
    parts = [p.strip() for p in value.split(",")]
    if len(parts) < len(PICK_FIELDS):
        return None
    out = {}
    for key, raw in zip(PICK_FIELDS, parts):
        if key == "season":
            out[key] = raw
        else:
            try:
                out[key] = int(raw)
            except ValueError:
                out[key] = None
    return out


def pick_label(pick: dict, names: dict | None = None) -> str:
    """"2026 round 6 (originally Juniper)" — whose pick it started as matters.

    A sixth-rounder from a team that finished last is not the same asset as one
    from the champion, and only `roster_id` says which it is.
    """
    names = names or {}
    origin = pick.get("roster_id")
    who = names.get(origin)
    tail = f" (originally {who})" if who else ""
    return f"{pick.get('season')} round {pick.get('round')}{tail}"
