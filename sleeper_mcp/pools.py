"""Pick'em pools: what the field picked, and where you differ from it.

PURE. No MCP import, no network.

`outcome` IS NOT A RESULT. Every pick in a live pool carries `outcome: "win"`,
on all 1,283 picks across 159 entries — it records which way the pick points,
not whether it came in. Read as a result it scores every entrant at 100%, and
the mistake is invisible because a pool where everyone is perfect still renders
as a tidy table. Nothing here claims to know who won.

What IS in the data is the split: how much of the field is on each side. In a
pool of 159 that is the only lever a picker has. Taking the 99% side of a game
wins nothing you were not already going to win; the weeks are decided by the
games where the field is divided, and by the ones where you are alone.
"""

from __future__ import annotations


def entries(book: dict) -> dict:
    """How many entries exist, and how many actually submitted.

    Empty entries are the majority in a casual pool and must not be counted in
    a denominator — a game "89% of the pool picked" is 89% of the people who
    picked, not 89% of the people signed up.
    """
    total = len(book or {})
    submitted = sum(1 for e in (book or {}).values()
                    if ((e or {}).get("picks") or {}))
    return {"total": total, "submitted": submitted,
            "empty": total - submitted}


def field_splits(book: dict) -> dict[str, dict[str, int]]:
    """-> {game_id: {team: how many picked it}}."""
    out: dict[str, dict[str, int]] = {}
    for entry in (book or {}).values():
        for gid, pick in ((entry or {}).get("picks") or {}).items():
            team = (pick or {}).get("team")
            if team:
                out.setdefault(gid, {})
                out[gid][team] = out[gid].get(team, 0) + 1
    return out


def my_picks(book: dict, roster_id) -> dict[str, str]:
    """-> {game_id: team} for one entry. The book is keyed by roster id."""
    entry = (book or {}).get(str(roster_id)) or (book or {}).get(roster_id) or {}
    return {gid: (p or {}).get("team")
            for gid, p in ((entry or {}).get("picks") or {}).items()
            if (p or {}).get("team")}


def consensus(book: dict, roster_id) -> list[dict]:
    """Per game: the field's split, your side, and how exposed that leaves you.

    `share` is the fraction of PICKERS on your side. Sorted by it ascending, so
    the games where you stand apart come first — those are the ones that decide
    a week, and they are invisible in a list ordered by kickoff.
    """
    splits = field_splits(book)
    mine = my_picks(book, roster_id)
    rows = []
    for gid, counts in splits.items():
        total = sum(counts.values())
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        fav, fav_n = ranked[0]
        pick = mine.get(gid)
        rows.append({
            "game_id": gid,
            "teams": [t for t, _n in ranked],
            "counts": counts,
            "pickers": total,
            "favourite": fav,
            "favourite_share": (fav_n / total) if total else 0.0,
            "my_pick": pick,
            "share": (counts.get(pick, 0) / total) if (pick and total) else None,
            "with_field": (pick == fav) if pick else None,
        })
    rows.sort(key=lambda r: (r["share"] is None, r["share"] or 0.0))
    return rows


def exposure(rows: list[dict], contrarian_below: float = 0.5) -> dict:
    """How differentiated an entry is: picks made, and how many go against.

    An entry that matches the chalk everywhere finishes wherever the field
    finishes. This counts the places it can separate.
    """
    made = [r for r in rows if r["my_pick"]]
    return {
        "picked": len(made),
        "unpicked": len(rows) - len(made),
        "against_field": sum(1 for r in made if not r["with_field"]),
        "contrarian": sum(1 for r in made
                          if r["share"] is not None
                          and r["share"] < contrarian_below),
    }


# --- results ----------------------------------------------------------------
# Scoring a pool needs the games, and they are NOT in the pick data. Every pick
# carries `outcome: "win"` whether it came in or not, so the only honest source
# is Sleeper's own scoreboard: GET /scores/nfl/regular/<season>/<week>.
#
# The teams and scores sit inside `metadata` — `home_team`, `away_team`,
# `home_score`, `away_score` — while `status` is top level. Observed statuses
# are "complete" and "pre_game"; anything that is not complete is treated as
# undecided, so a game in progress is never scored from a partial lead.

FINAL = "complete"


def winners(games: list[dict]) -> dict[str, str]:
    """-> {game_id: winning team}, FINAL GAMES ONLY.

    A game that has not finished is absent rather than present with a guess.
    Scoring a leader at half time is the pick'em version of counting a
    Thursday-night player as a completed week.
    """
    out = {}
    for g in games or []:
        if g.get("status") != FINAL:
            continue
        md = g.get("metadata") or {}
        home, away = md.get("home_team"), md.get("away_team")
        hs, as_ = md.get("home_score"), md.get("away_score")
        if not home or not away or hs is None or as_ is None:
            continue
        out[str(g.get("game_id"))] = (home if hs > as_
                                      else away if as_ > hs else "TIE")
    return out


def score_entry(entry: dict, results: dict) -> dict:
    """-> {correct, wrong, pending} for one entry."""
    correct = wrong = pending = 0
    for gid, pick in ((entry or {}).get("picks") or {}).items():
        won = results.get(str(gid))
        if won is None:
            pending += 1
        elif (pick or {}).get("team") == won:
            correct += 1
        else:
            wrong += 1
    return {"correct": correct, "wrong": wrong, "pending": pending}


def leaderboard(book: dict, results: dict) -> list[tuple]:
    """-> [(correct, wrong, pending, roster_id)], best first, submitted only."""
    rows = []
    for rid, entry in (book or {}).items():
        if not ((entry or {}).get("picks") or {}):
            continue
        s = score_entry(entry, results)
        rows.append((s["correct"], s["wrong"], s["pending"], str(rid)))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return rows


def chalk_score(book: dict, results: dict) -> int:
    """How many an entry would have if it took the pool favourite every time.

    THE COMPARISON THAT MATTERS. A raw record says whether the week went well;
    this says whether the picking did. Differentiating is a choice with a
    price, and the price is only visible against what following the room would
    have returned.
    """
    got = 0
    for gid, counts in field_splits(book).items():
        won = results.get(str(gid))
        if won and max(counts, key=counts.get) == won:
            got += 1
    return got


def against_the_field(entry: dict, book: dict, results: dict) -> dict:
    """Split one entry's decided picks by whether they followed the favourite."""
    splits = field_splits(book)
    out = {"with": [0, 0], "against": [0, 0]}      # [hit, miss]
    for gid, pick in ((entry or {}).get("picks") or {}).items():
        won = results.get(str(gid))
        counts = splits.get(gid)
        if won is None or not counts:
            continue
        side = ("with" if (pick or {}).get("team")
                == max(counts, key=counts.get) else "against")
        out[side][0 if pick.get("team") == won else 1] += 1
    return out
