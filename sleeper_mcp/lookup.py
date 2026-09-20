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

THREE SHAPES IN THE DICTIONARY THAT A NAIVE full_name MATCH MISSES:

  * A team defence has NO full_name. Its id is the team code ("DET") and it
    carries first_name "Detroit" / last_name "Lions". Every one of the 32 is
    like this, so "Lions", "Detroit" and "DET" all resolved to nothing.
  * Some fantasy-relevant players are listed under a non-fantasy `position`
    while `fantasy_positions` says otherwise: a two-way player at DB with
    fantasy_positions [DB, WR]; every fullback at FB with [RB].
  * An active player who is simply unsigned this week has `team: None`, the
    same as a retired duplicate. A read (news, history) about him is exactly
    what a manager wants that week; a write must still never guess.
"""

from __future__ import annotations

# Only positions a fantasy roster can hold. Sleeper's dictionary also carries
# coaches and practice-squad players, who must never be matchable.
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

_ALL_FANTASY = set(FANTASY_POSITIONS) | {"DL", "LB", "DB"}      # IDP too


def display_name(v: dict | None, pid: str = "") -> str:
    """A printable name for any dictionary entry, defences included."""
    v = v or {}
    return (v.get("full_name")
            or " ".join(x for x in (v.get("first_name"), v.get("last_name")) if x)
            or str(pid))


def positions(v: dict | None) -> set[str]:
    """Every position this player may FILL — fantasy_positions, plus position.

    `position` is where Sleeper lists him; `fantasy_positions` is where a
    lineup may start him. They differ for two-way players and fullbacks, and
    slot eligibility has to be judged on the second.
    """
    v = v or {}
    out = {p for p in (v.get("fantasy_positions") or []) if p}
    if v.get("position"):
        out.add(v["position"])
    return out


def fantasy_position(v: dict | None) -> str | None:
    """The position to DISPLAY: the first fantasy-eligible one.

    A two-way player listed at DB but startable at WR is a receiver to a
    fantasy roster; printing "DB" beside him in a lineup reads as an error.
    """
    v = v or {}
    pos = v.get("position")
    if pos in FANTASY_POSITIONS:
        return pos
    for p in (v.get("fantasy_positions") or []):
        if p in FANTASY_POSITIONS:
            return p
    return pos


def is_fantasy(v: dict | None) -> bool:
    return bool(positions(v) & _ALL_FANTASY)


def _matches(want: str, pid: str, v: dict) -> bool:
    """Does the typed text name this entry?

    Exact id ("4866", "DET"), exact team code for a defence, or a substring
    of the display name — which for a defence is "Detroit Lions", so both the
    city and the nickname match.
    """
    if want == str(pid).lower():
        return True
    if v.get("position") == "DEF" and want == str(v.get("team") or "").lower():
        return True
    return want in display_name(v, pid).lower()


def find_player(P: dict, name: str, pool: set | None = None,
                *, allow_unsigned: bool = False) -> list[tuple[str, dict]]:
    """Every player whose name (or id) matches `name`. May return 0, 1 or many.

    Returning a LIST rather than a best guess is the whole design: the caller
    must decide what an ambiguous name means, and for a write the answer is
    always to refuse. A function that picked one would hide exactly the case
    that matters.

    EXACT BEATS PARTIAL. If any entry matches the text exactly (by id, team
    code or full display name) only those are returned, so "DET" never comes
    back ambiguous with a player whose name contains "det".

    Args:
        P: Sleeper's player dictionary.
        pool: Restrict to these ids — ALWAYS pass one when you can. Sleeper's
            dictionary holds about 11,000 players including the long retired,
            and a roster holds fifteen. Searching the whole dictionary when you
            meant "someone on my team" is how a retired player gets submitted.
            Within a pool the `team` filter is dropped: a rostered player whose
            NFL club released him this week is still on the roster.
        allow_unsigned: For READS ONLY. When nothing with a team matches, fall
            back to active players with no team, so news about a player cut
            this week is still reachable. Writes must leave this False.
    """
    want = (name or "").lower().strip()
    if not want:
        return []
    if pool is not None:
        items = [(str(pid), P.get(str(pid)) or {}) for pid in pool]
        cands = [(pid, v) for pid, v in items if _matches(want, pid, v)]
    else:
        cands = [(pid, v) for pid, v in P.items()
                 if v and is_fantasy(v) and _matches(want, pid, v)]
        signed = [(pid, v) for pid, v in cands if v.get("team")]
        if signed or not allow_unsigned:
            cands = signed
        else:
            cands = [(pid, v) for pid, v in cands if v.get("active")]
    exact = [(pid, v) for pid, v in cands
             if want in (str(pid).lower(), display_name(v, pid).lower(),
                         str(v.get("team") or "").lower()
                         if v.get("position") == "DEF" else "")]
    return exact or cands


def resolve_names(P: dict, names: list[str], pool: set | None,
                  label: str) -> tuple[list[str], list[str]]:
    """Names -> ids against a KNOWN pool. Returns (ids, problems).

    The one resolver every write uses. Each name must match exactly one
    player in `pool`; anything else is a problem naming the alternatives, and
    a caller with any problems must send nothing.
    """
    got, bad = [], []
    for want in names or []:
        hits = find_player(P, want, pool)
        if len(hits) == 1:
            got.append(hits[0][0])
        elif not hits:
            bad.append(f"{want!r} is not on {label}")
        else:
            bad.append(f"{want!r} is ambiguous on {label}: "
                       + ", ".join(f"{display_name(v, p)} (id {p})"
                                   for p, v in hits))
    return got, bad


def ambiguous(name: str, hits: list) -> str:
    """The message for a name that matched nothing, or too much.

    Names the alternatives WITH THEIR IDS, because two namesakes at the same
    position and club cannot be told apart any other way, and every tool here
    accepts an id wherever it accepts a name.
    """
    opts = ", ".join(f"{display_name(v, p)} ({fantasy_position(v)}-"
                     f"{v.get('team') or 'no team'}, id {p})"
                     for p, v in hits[:8])
    return (f"No player matches {name!r}." if not hits
            else f"Ambiguous — {len(hits)} match {name!r}: {opts}")
