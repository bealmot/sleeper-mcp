"""Keepers — who a team is holding into the next draft.

A keeper is a player carried over instead of being returned to the pool, and
the league's `max_keepers` says how many each team may hold.

TWO DIFFERENT THINGS WEAR THE SAME NAME, and conflating them is the trap here.

`roster.keepers` is a FORWARD designation — who a manager has marked to carry
into the next draft. The authoritative record of who was ACTUALLY kept is the
draft itself: picks carrying `is_keeper`.

They disagree, and not subtly. In the league this was built against the 2026
draft held eight `is_keeper` picks — McCaffrey, Gibbs, Nacua, Lamar Jackson —
while `roster.keepers` listed two completely different players. Reading the
roster field as "who was kept" reports the wrong players with total confidence.
So this shows both, separately labelled.

What a mid-season write to `roster.keepers` does at the NEXT draft is not
established. It is plainly not ignored — managers in this league have entries
sitting there mid-season — but nothing observed here proves how it is consumed.
The write says so rather than guessing in either direction.
"""

from __future__ import annotations

import asyncio
import json

from .client import (ConfigError, cache_clear, gql, league, league_id, mcp,
                     players, require_writes, rest, roster_id)
from .reads import _ambiguous, _find, _owners

# Sleeper hands `keepers` BACK as a list and takes it IN as a String. The
# asymmetry is undocumented and silent: pass a list to the mutation and it is
# accepted without storing anything.
PRE_DRAFT = "pre_draft"


def parse_keepers(value) -> list[str]:
    """Normalise whatever the roster carries into a list of player ids.

    Seen as None, as a list, and as a JSON string depending on where it is
    read from. Treating the JSON string as a list of characters yields a
    keeper roster of quotes and brackets.
    """
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def encode_keepers(ids: list[str]) -> str:
    """The mutation's `keepers` argument: a JSON array, as a String."""
    return json.dumps([str(i) for i in ids])


def timing_note(status: str, season: str) -> str:
    """What is known about when a designation takes effect.

    States facts rather than a mechanism. An earlier version asserted that a
    write outside the pre-draft window "does nothing" — which was never
    verified and is contradicted by managers in a live league carrying
    designations mid-season.
    """
    if status == PRE_DRAFT:
        return ""
    if status == "drafting":
        return ("The draft is UNDER WAY and keeper slots are being consumed as "
                "it runs. Changing them now is unlikely to take.")
    return (f"This league is '{status}' — the {season} draft has already run, "
            f"so a change here cannot affect it. `roster.keepers` is a forward "
            f"designation and the next draft is the earliest thing it could "
            f"bind; exactly how it is consumed then is not established.")


async def _kept_in_draft(lg_cfg: dict, P: dict, owner: dict) -> list[tuple]:
    """Who was ACTUALLY kept, from the draft — the authoritative record.

    `roster.keepers` is a forward designation and does not agree with this.
    """
    did = lg_cfg.get("draft_id")
    if not did:
        return []
    try:
        picks = await rest(f"/draft/{did}/picks") or []
    except Exception:
        return []
    out = []
    for pk in picks:
        if not pk.get("is_keeper"):
            continue
        nm = (P.get(str(pk.get("player_id"))) or {}).get(
            "full_name", str(pk.get("player_id")))
        out.append((owner.get(pk.get("roster_id"),
                              f"roster {pk.get('roster_id')}"),
                    nm, pk.get("round"), pk.get("pick_no")))
    return sorted(out)


@mcp.tool()
async def keepers(history: bool = True, league_id_: str = "") -> str:
    """Who was kept, who is designated to be kept, and the league's rules.

    TWO SEPARATE THINGS, shown separately because they disagree. The draft's
    `is_keeper` picks are the record of who was actually kept. `roster.keepers`
    is a forward designation for the NEXT draft — in a live league those lists
    named entirely different players, so reporting either as "the keepers"
    would be confidently wrong half the time.

    Args:
        history: Also show previous seasons. Default True.
        league_id_: Override the configured league.
    """
    lg = league_id(league_id_ or None)
    lg_cfg = await league(lg)
    settings = lg_cfg.get("settings") or {}
    cap = settings.get("max_keepers")
    status = str(lg_cfg.get("status") or "?")
    season = str(lg_cfg.get("season") or "?")

    if not cap:
        return (f"  {lg_cfg.get('name')} is not a keeper league — "
                f"max_keepers is {cap!r}.")

    P, rosters, owner = await asyncio.gather(
        players(), rest(f"/league/{lg}/rosters"), _owners(lg))

    def show(ids):
        return ", ".join((P.get(i) or {}).get("full_name", i)
                         for i in ids) or "-"

    out = [f"  {lg_cfg.get('name')} — {cap} keeper(s) per team, "
           f"{season} ({status})"]
    note = timing_note(status, season)
    if note:
        out.append(f"  {note}")
    out.append("")

    kept = await _kept_in_draft(lg_cfg, P, owner)
    out.append(f"  KEPT in the {season} draft ({len(kept)}) — "
               f"the authoritative record")
    for name, nm, rnd, pick in kept:
        out.append(f"    {name[:22]:22} {nm[:24]:24} r{rnd} pick {pick}")
    if not kept:
        out.append("    (none, or the draft has not run)")

    rows = [(owner.get(r["roster_id"], f"roster {r['roster_id']}"),
             parse_keepers(r.get("keepers"))) for r in (rosters or [])]
    designated = [(n, k) for n, k in rows if k]
    out.append("")
    out.append(f"  DESIGNATED for the next draft ({len(designated)}) — "
               f"a forward marker, not this season's result")
    for name, ids in sorted(designated):
        out.append(f"    {name[:22]:22} {show(ids)}")
    if not designated:
        out.append("    (none)")

    if history:
        prev = str(lg_cfg.get("previous_league_id") or "")
        seen = 0
        while prev and prev not in ("0", "") and seen < 5:
            old = await league(prev)
            old_rosters, old_owner = await asyncio.gather(
                rest(f"/league/{prev}/rosters"), _owners(prev))
            old_kept = await _kept_in_draft(old, P, old_owner)
            out.append("")
            out.append(f"  {old.get('season')} draft — {len(old_kept)} kept")
            for name, nm, rnd, pick in old_kept:
                out.append(f"    {name[:22]:22} {nm[:24]:24} r{rnd} pick {pick}")
            prev = str(old.get("previous_league_id") or "")
            seen += 1
    return "\n".join(out)


@mcp.tool()
async def set_keepers(player_names: list[str], confirm: bool = False,
                      league_id_: str = "", roster_id_: int = 0) -> str:
    """WRITE. Designate which of your players you are keeping.

    The list is COMPLETE, not a delta — pass everyone you intend to keep, or an
    empty list to clear. The league's `max_keepers` caps it.

    This writes the FORWARD designation, `roster.keepers`. It does not change
    who was kept in a draft that has already run — that lives in the draft's
    `is_keeper` picks and cannot be edited here. See `keepers` for both.

    Refuses outright only while a draft is actively running, since slots are
    being consumed as it goes. Outside that it reports what is known about
    timing rather than asserting a mechanism nothing here has verified.

    Args:
        player_names: Full or partial names, all from your roster.
        confirm: Must be True to send. Defaults to a dry run.
        league_id_: Override the configured league.
        roster_id_: Override your roster.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    lg_cfg = await league(lg)
    settings = lg_cfg.get("settings") or {}
    cap = int(settings.get("max_keepers") or 0)
    status = str(lg_cfg.get("status") or "?")
    season = str(lg_cfg.get("season") or "?")

    if not cap:
        return (f"  Refused, nothing sent: {lg_cfg.get('name')} is not a "
                f"keeper league (max_keepers={cap}).")

    P, rosters = await asyncio.gather(players(), rest(f"/league/{lg}/rosters"))
    me = next((r for r in (rosters or []) if r["roster_id"] == rid), None)
    if not me:
        raise ConfigError(f"No roster {rid} in league {lg}.")
    mine = {str(p) for p in (me.get("players") or [])}

    chosen, bad = [], []
    for name in player_names or []:
        hits = [(pid, v) for pid, v in _find(P, name) if pid in mine]
        if len(hits) != 1:
            bad.append(_ambiguous(name, hits).strip()
                       if hits else f"{name}: not on your roster.")
        else:
            chosen.append(hits[0][0])
    if bad:
        return "Refused, nothing sent:\n  " + "\n  ".join(bad)
    if len(chosen) > cap:
        return (f"  Refused, nothing sent: {len(chosen)} chosen but this "
                f"league allows {cap}.")

    def show(ids):
        return ", ".join((P.get(i) or {}).get("full_name", i)
                         for i in ids) or "(none)"

    current = parse_keepers(me.get("keepers"))
    plan = (f"  keepers now:   {show(current)}\n"
            f"  keepers after: {show(chosen)}   (cap {cap})")

    note = timing_note(status, season)
    if status == "drafting":
        return (f"Refused, nothing sent.\n  {note}\n\n{plan}")
    if note:
        plan = f"{plan}\n\n  {note}"
    if not confirm:
        return f"DRY RUN — nothing sent.\n{plan}\n\n  Call again with confirm=True."

    require_writes("set_keepers")
    cache_clear()
    # `keepers` is a String holding a JSON array. Passing a real list is
    # accepted and stores nothing.
    await gql("mutation($lg:Snowflake!,$rid:Int!,$k:String){"
              "roster_set_keepers(league_id:$lg,roster_id:$rid,keepers:$k)"
              "{roster_id keepers}}",
              {"lg": lg, "rid": rid, "k": encode_keepers(chosen)}, auth=True)

    after = await rest(f"/league/{lg}/rosters")
    mine_after = next((r for r in (after or []) if r["roster_id"] == rid), {})
    now = parse_keepers(mine_after.get("keepers"))
    ok = sorted(now) == sorted(chosen)
    return (f"{'Set' if ok else 'SENT BUT NOT CONFIRMED'} — keepers are now "
            f"{show(now)}.\n{plan}" + ("" if ok else
            "\n  The read-back does not match. Check Sleeper directly."))
