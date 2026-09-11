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
