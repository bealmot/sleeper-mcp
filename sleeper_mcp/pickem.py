"""Pick'em pools: finding yours, and the standings Sleeper already keeps.

Pick'em has no web interface, and the lobby id used to be obtainable only
from the mobile app's share link. It is discoverable: the authenticated
`my_leagues` query lists the pool with sport "pickem:nfl", and the PUBLIC
`rosters_by_user` query gives the caller's entry in it.

The pool's own scoring sits in plain sight too: each entry's roster carries
`metadata.points_by_leg`, a JSON STRING of {leg_id: points}, on the public
REST rosters endpoint. That is Sleeper's number — the one the app shows —
so a standings table needs no re-derivation from the scoreboard.
"""

from __future__ import annotations

import json

from . import client as _client
from .client import READ, ConfigError, gql, owners, rest, state, tool


@tool(annotations=READ)
async def find_my_pools(season: str = "") -> str:
    """Discover your pick'em pool and entry ids. NEEDS A TOKEN.

    Pools do not appear in the public league list, so this asks Sleeper for
    the logged-in user's leagues, keeps the pick'em ones, and looks up your
    entry in each. The two ids it prints are what SLEEPER_PICKEM_LEAGUE and
    SLEEPER_PICKEM_ROSTER need; every pick'em tool also accepts them as
    arguments.

    Args:
        season: Season year. Defaults to the current season.
    """
    me = (await gql("{me{user_id display_name}}", auth=True)).get("me") or {}
    uid = me.get("user_id")
    if not uid:
        raise ConfigError("Sleeper returned no user for this token.")
    yr = season or str((await state()).get("season") or "")
    mine = (await gql('{my_leagues(season:"%s"){league_id name sport season '
                      'status total_rosters}}' % yr, auth=True)
            ).get("my_leagues") or []
    pools = [l for l in mine if str(l.get("sport") or "").startswith("pickem")]
    out = [f"{me.get('display_name') or uid}  (user {uid}), season {yr}", ""]
    if not pools:
        out.append("  No pick'em pools found for this season.")
        return "\n".join(out)
    for p in pools:
        sport = p.get("sport") or "pickem:nfl"
        entries = (await gql('{rosters_by_user(user_id:"%s",sport:"%s",'
                             'season_type:"regular",season:"%s"){league_id roster_id}}'
                             % (uid, sport, yr))).get("rosters_by_user") or []
        mine_in = [e for e in entries if str(e.get("league_id")) == str(p.get("league_id"))]
        out.append(f"  {str(p.get('name') or '').strip()}  ({p.get('status')}, "
                   f"{p.get('total_rosters')} entries)")
        out.append(f"    SLEEPER_PICKEM_LEAGUE  {p.get('league_id')}")
        if mine_in:
            for e in mine_in:
                out.append(f"    SLEEPER_PICKEM_ROSTER  {e.get('roster_id')}")
        else:
            out.append("    SLEEPER_PICKEM_ROSTER  ?? (no entry of yours found)")
        out.append("")
    out.append("  Set both, or pass pickem_league= and pickem_roster= per call.")
    return "\n".join(out)


@tool(annotations=READ)
async def pickem_standings(pickem_league: str = "", pickem_roster: int = 0,
                           limit: int = 25) -> str:
    """The pool leaderboard, from Sleeper's own scoring. No token needed.

    Each entry's points per week sit in the public roster metadata, so this
    is the number the app shows rather than a re-derivation. Your entry is
    marked, with the gap to the lead.

    Args:
        pickem_league: Defaults to SLEEPER_PICKEM_LEAGUE.
        pickem_roster: Defaults to SLEEPER_PICKEM_ROSTER.
        limit: How many entries to list. Default 25.
    """
    lg = str(pickem_league or _client.DEFAULT_PICKEM_LEAGUE or "").strip()
    rid = pickem_roster or _client.DEFAULT_PICKEM_ROSTER
    if not lg:
        raise ConfigError("Needs SLEEPER_PICKEM_LEAGUE (or pickem_league=). "
                          "find_my_pools discovers it.")
    lg = _client.snowflake(lg, "pick'em league id")
    pool, rosters = await rest(f"/league/{lg}"), await rest(f"/league/{lg}/rosters")
    try:
        names = await owners(lg)
    except Exception:                                   # noqa: BLE001
        names = {}

    rows = []
    legs: set = set()
    for r in rosters or []:
        raw = (r.get("metadata") or {}).get("points_by_leg")
        try:
            by_leg = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            by_leg = {}
        by_leg = {k: float(v or 0) for k, v in (by_leg or {}).items()}
        legs |= set(by_leg)
        rows.append((sum(by_leg.values()), r.get("roster_id"), by_leg))
    if not rows:
        return f"  No entries found in pool {lg}."
    rows.sort(key=lambda x: (-x[0], x[1]))
    weeks = sorted(legs, key=lambda k: int(str(k).rsplit(":", 1)[-1] or 0))
    show = weeks[-6:]
    lead = rows[0][0]
    playing = sum(1 for r in rows if r[2])
    title = str((pool or {}).get("name") or "").strip() or "Pick'em"
    head = [f"  {title} — "
            f"{len(rows)} entries, {playing} with points, "
            f"{len(weeks)} week(s) scored", "",
            f"  {'#':>4} {'entry':22} {'total':>6} "
            + " ".join(f"{str(w).rsplit(':', 1)[-1]:>4}" for w in show)]
    out = list(head)
    mine_line = None
    for i, (total, roster, by_leg) in enumerate(rows, 1):
        who = names.get(roster) or f"entry {roster}"
        mark = "  <- you" if rid is not None and roster == rid else ""
        line = (f"  {i:>4} {who[:22]:22} {total:6.1f} "
                + " ".join(f"{by_leg.get(w, 0):4.0f}" for w in show) + mark)
        if mark:
            mine_line = (i, total)
        if i <= limit or mark:
            out.append(line)
    if mine_line:
        i, total = mine_line
        out += ["", f"  You are {i} of {len(rows)}, {lead - total:.0f} behind the "
                    f"lead" + (" (tied)" if lead == total else "")]
    return "\n".join(out)
