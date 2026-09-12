"""Resolving a player name to a player.

PURE. No MCP import, no network — it takes Sleeper's player dictionary as an
argument, which is what makes it testable without a league.

THIS IS THE MOST SAFETY-CRITICAL FUNCTION IN THE PACKAGE and it used to live
unexported and untested inside reads.py. Everything that writes — setting a
lineup, claiming a waiver, proposing a trade — passes a human's typed name
through here first, so a wrong match here submits the wrong player. It has
already done so once: "Kenneth Walker" matches two entries, one of them retired
since 2019, and taking the first hit sent Sleeper an ineligible player whose
error message was blamed on a locked lineup for several hours.
"""

from __future__ import annotations

# Only positions a fantasy roster can hold. Sleeper's dictionary also carries
# coaches and practice-squad players, who must never be matchable.
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


def find_player(P: dict, name: str,
                pool: set | None = None) -> list[tuple[str, dict]]:
    """Every player whose name contains `name`. May return 0, 1 or many.

    Returning a LIST rather than a best guess is the whole design: the caller
    must decide what an ambiguous name means, and for a write the answer is
    always to refuse. A function that picked one would hide exactly the case
    that matters.

    Args:
        P: Sleeper's player dictionary.
        pool: Restrict to these ids — ALWAYS pass one when you can. Sleeper's
            dictionary holds about 11,000 players including the long retired,
            and a roster holds fifteen. Searching the whole dictionary when you
            meant "someone on my team" is how a retired player gets submitted.
    """
    want = (name or "").lower().strip()
    if not want:
        return []
    items = ((pid, P[pid]) for pid in pool if pid in P) if pool else P.items()
    return [(pid, v) for pid, v in items
            if v and v.get("team")
            and v.get("position") in FANTASY_POSITIONS
            and want in (v.get("full_name") or "").lower()]


def ambiguous(name: str, hits: list) -> str:
    """The message for a name that matched nothing, or too much.

    Names the alternatives, because "ambiguous" alone leaves the reader to
    guess what to type next.
    """
    opts = ", ".join(f"{v.get('full_name')} ({v.get('position')}-{v.get('team')})"
                     for _, v in hits[:8])
    return (f"No player matches {name!r}." if not hits
            else f"Ambiguous — {len(hits)} match {name!r}: {opts}")
