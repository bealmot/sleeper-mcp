"""Finding your own ids — the first thing a new user needs.

Sleeper's ids are not visible in the app's UI, and every other tool here wants
one. The league lookups below are PUBLIC: no token, no login, just a username.
Pick'em pools are the exception (see pickem.py): the lobby is only listed for
the logged-in user, so discovering it needs a token.
"""

from __future__ import annotations

import datetime as dt
import urllib.parse

from . import client as _client
from . import config as _config
from .boundaries import RefusedByPolicy
from .client import (READ, WRITE, AuthError, TransportFailure, gql, league,
                     rest, tool)

LEAGUE_TYPES = {0: "redraft", 1: "keeper", 2: "dynasty", 3: "guillotine"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]


def _format(lg: dict) -> str:
    """'keeper (1 kept)', 'guillotine — clone of 138...', 'redraft'."""
    s = lg.get("settings") or {}
    md = lg.get("metadata") or {}
    kind = LEAGUE_TYPES.get(s.get("type"), f"type {s.get('type')}")
    bits = [kind]
    if s.get("type") in (1, 2) and s.get("max_keepers"):
        bits.append(f"{s['max_keepers']} kept")
    if s.get("best_ball"):
        bits.append("best ball")
    if s.get("league_average_match"):
        bits.append("plays the league median")
    if md.get("cloned_from"):
        bits.append(f"CLONE of {md['cloned_from']}")
    return ", ".join(bits)


@tool(annotations=READ)
async def find_my_leagues(username: str, season: str = "") -> str:
    """Look up your Sleeper user id, your leagues, and your roster id in each.

    Start here. Everything this returns is what the other tools need, and none
    of it requires a token — it is all public. Leagues are listed with their
    status and format, and a CLONE (Sleeper's copy of a league, often a
    guillotine variant of the same group) is marked and listed after the
    original, because two entries with one name are otherwise identical.
    Pick'em pools are not here: use find_my_pools.

    Args:
        username: Your Sleeper username (the display name you log in with).
        season: Season year, e.g. "2026". Defaults to the current season.
    """
    name = (username or "").strip()
    if not name:
        return "Give a username."
    try:
        user = await rest(f"/user/{urllib.parse.quote(name, safe='')}")
    except RefusedByPolicy as e:
        return f"Refused: {e}"
    except TransportFailure as e:
        return f"Sleeper could not be reached ({e.kind}); nothing is wrong with the name."
    except Exception as e:                              # noqa: BLE001
        return (f"Sleeper answered with an error looking up {name!r} "
                f"({e.__class__.__name__}: {str(e)[:120]}).")
    if not user or not user.get("user_id"):
        return (f"No Sleeper user called {name!r}. Use the username you log in "
                f"with, not a team name.")
    uid = user["user_id"]

    if not season:
        st = await rest("/state/nfl")
        season = str(st.get("season") or dt.date.today().year)

    leagues = await rest(f"/user/{uid}/leagues/nfl/{season}") or []
    out = [f"{user.get('display_name') or name}",
           f"  SLEEPER_USER_ID   {uid}",
           f"  season            {season}",
           f"  leagues           {len(leagues)}", ""]
    if not leagues:
        out.append("  No NFL leagues found for that season. Try another year "
                   "with season=\"2025\".")
        return "\n".join(out)

    # Originals first, clones after; then by name.
    leagues.sort(key=lambda lg: (bool((lg.get("metadata") or {}).get("cloned_from")),
                                 str(lg.get("name") or "")))
    for lg in leagues:
        lid = lg.get("league_id")
        rosters = await rest(f"/league/{lid}/rosters") or []
        mine = next((r for r in rosters
                     if r.get("owner_id") == uid or uid in (r.get("co_owners") or [])),
                    None)
        role = ("" if not mine or mine.get("owner_id") == uid else "  (co-owner)")
        slots = [p for p in (lg.get("roster_positions") or [])
                 if p not in ("BN", "IR", "TAXI")]
        out.append(f"  {lg.get('name')}")
        out.append(f"    SLEEPER_LEAGUE_ID  {lid}")
        out.append(f"    SLEEPER_ROSTER_ID  "
                   f"{mine.get('roster_id') if mine else '?? (not an owner)'}{role}")
        out.append(f"    {lg.get('status')} · {_format(lg)} · teams "
                   f"{lg.get('total_rosters')} · starters {'/'.join(slots)}")
        prev = lg.get("previous_league_id")
        if prev and str(prev) not in ("0", ""):
            out.append(f"    continues {prev}")
        out.append("")

    out.append("  Set SLEEPER_LEAGUE_ID and SLEEPER_ROSTER_ID for the league "
               "you want as the default, or pass league_id_= per call.")
    return "\n".join(out)


@tool(annotations=READ)
async def league_info(league_id_: str = "") -> str:
    """League settings that change how everything else should be read.

    Format (redraft / keeper / dynasty / guillotine), scoring, roster slots,
    waiver type and the trade rules all vary by league, and several of them
    silently change what a tool's output means — a lineup is only valid
    against this league's slot layout, points only against its scoring, and
    a guillotine league has no standings, playoffs or trades at all.

    Args:
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
    """
    lg = await league(league_id_ or None)
    s = lg.get("settings") or {}
    slots = [p for p in (lg.get("roster_positions") or [])
             if p not in ("BN", "IR", "TAXI")]
    bench = sum(1 for p in (lg.get("roster_positions") or []) if p == "BN")
    sc = lg.get("scoring_settings") or {}
    kind = s.get("type")

    waiver = {0: "rolling waivers", 1: "reverse standings", 2: "FAAB"}.get(
        s.get("waiver_type"), f"type {s.get('waiver_type')}")
    day = s.get("waiver_day_of_week")
    day_name = WEEKDAYS[day] if isinstance(day, int) and 0 <= day < 7 else str(day)
    ir_ok = sorted(k.replace("reserve_allow_", "").upper()
                   for k, v in s.items() if k.startswith("reserve_allow_") and v)

    out = [f"{lg.get('name')}  ({lg.get('season')} {lg.get('season_type')}, "
           f"{lg.get('status')})",
           f"  league_id      {lg.get('league_id')}",
           f"  format         {_format(lg)}",
           f"  teams          {lg.get('total_rosters')}",
           f"  starters       {' '.join(slots)}",
           f"  bench          {bench}   IR slots {s.get('reserve_slots', 0)}"
           + (f" (allowed: {', '.join(ir_ok)})" if ir_ok else "")
           + (f"   taxi {s.get('taxi_slots')}" if s.get("taxi_slots") else ""),
           ""]
    if kind == 3:
        out += ["  GUILLOTINE: the lowest weekly score is eliminated and its "
                "players go to waivers. No win-loss record, no playoffs.", ""]
    out += [f"  waivers        {waiver}"
            + (f", budget ${s.get('waiver_budget')}" if s.get("waiver_budget") else ""),
            f"  waiver day     {day_name}   clears in {s.get('waiver_clear_days')} day(s)"]
    if s.get("disable_trades"):
        out.append("  trades         DISABLED")
    else:
        deadline = s.get("trade_deadline")
        out.append(f"  trade deadline {'none' if deadline in (99, None) else f'week {deadline}'}"
                   f"   review {s.get('trade_review_days')} day(s)"
                   + ("  <- 0 means an accepted trade executes IMMEDIATELY, with "
                      "no veto window" if s.get("trade_review_days") == 0 else ""))
    if kind == 3 or not s.get("playoff_week_start"):
        out.append("  playoffs       none")
    else:
        out.append(f"  playoffs       week {s.get('playoff_week_start')}, "
                   f"{s.get('playoff_teams')} teams")
    out.append("")

    # Surface the scoring rules most likely to make a naive reading wrong.
    notable = [("rec", "per reception (league-wide)"),
               ("bonus_rec_rb", "per reception, RB"),
               ("bonus_rec_wr", "per reception, WR"),
               ("bonus_rec_te", "per reception, TE"),
               ("rec_fd", "per receiving first down"),
               ("rush_fd", "per rushing first down"),
               ("pass_fd", "per passing first down"),
               ("pass_td", "per passing TD"), ("pass_int", "per interception"),
               ("fum_lost", "per fumble lost"),
               ("rec_td_50p", "bonus: 50+ yd receiving TD"),
               ("rush_td_50p", "bonus: 50+ yd rushing TD"),
               ("pass_td_50p", "bonus: 50+ yd passing TD")]
    out.append("  scoring worth knowing")
    for k, label in notable:
        if k in sc and sc[k]:
            out.append(f"    {label:32} {sc[k]}")
    # PPR is per POSITION when the league-level `rec` is 0 and the bonus keys
    # carry it; reading `rec` alone calls a half-PPR league zero-PPR.
    per_pos = {p: sc.get(f"bonus_rec_{p}", 0) or 0 for p in ("rb", "wr", "te")}
    base = sc.get("rec", 0) or 0
    effective = {p: base + v for p, v in per_pos.items()}
    if len(set(effective.values())) == 1:
        ppr = next(iter(effective.values()))
        style = ("PPR" if ppr >= 1 else "half-PPR" if ppr == 0.5
                 else "zero-PPR" if ppr == 0 else f"{ppr} per reception")
        out.append(f"    -> this is a {style} league")
    else:
        out.append("    -> receptions are worth "
                   + ", ".join(f"{v:g} at {p.upper()}" for p, v in effective.items()))
    te_prem = effective["te"] - max(effective["rb"], effective["wr"])
    if te_prem > 0:
        out.append(f"    -> TE premium: +{te_prem:g} per reception over RB/WR")
    if sc.get("rec_fd") or sc.get("rush_fd") or sc.get("pass_fd"):
        out.append("    -> it also pays FIRST DOWNS, which most rankings and "
                   "projections do not account for")
    return "\n".join(out)


@tool(annotations=READ)
async def auth_status() -> str:
    """Where each setting came from and whether the token actually works.

    Call this first when a write or an authenticated read fails. It reports
    the token's length, shape and source (environment, config file, or the
    setup page), names the usual delivery mistakes — an unexpanded
    ${SLEEPER_TOKEN}, surrounding quotes, a "Bearer " prefix — and then asks
    Sleeper whether it accepts the token. The token itself is never included.
    """
    # Read the LIVE values from client, not copies taken at import: the setup
    # page swaps client.TOKEN in place, and this is the tool that confirms it.
    token, writes = _client.TOKEN, _client.WRITES_ENABLED
    path = _config.config_path()
    out = [f"config file    {path}  ({'present' if path.exists() else 'absent'})",
           f"token          {_config.describe_token(token)}"]
    for p in _config.diagnose_token(token):
        out.append(f"  !! {p}")
    out.append(f"writes         {'ENABLED' if writes else 'disabled'}  "
               f"(from {_config.source('SLEEPER_ENABLE_WRITES')})")
    out.append(f"league id      {_client.DEFAULT_LEAGUE or '-'}  "
               f"(from {_config.source('SLEEPER_LEAGUE_ID')})")
    out.append(f"roster id      "
               f"{_client.DEFAULT_ROSTER if _client.DEFAULT_ROSTER is not None else '-'}  "
               f"(from {_config.source('SLEEPER_ROSTER_ID')})")
    out.append(f"pick'em        league {_client.DEFAULT_PICKEM_LEAGUE or '-'}, "
               f"entry {_client.DEFAULT_PICKEM_ROSTER if _client.DEFAULT_PICKEM_ROSTER is not None else '-'}")
    out.append("")
    if not token:
        out.append("live check     skipped — no token. Most reads work without "
                   "one; writes and the reads marked NEEDS A TOKEN do not. "
                   "Run `sleeper-mcp setup` or the setup_token tool to add one.")
        return "\n".join(out)
    try:
        me = (await gql("{ me { user_id display_name } }", auth=True)).get("me") or {}
        who = me.get("display_name") or me.get("user_id") or "?"
        out.append(f"live check     OK — Sleeper accepts the token; it belongs to {who}")
    except AuthError as e:
        out.append(f"live check     FAILED — {e}")
    except Exception as e:                                   # noqa: BLE001
        out.append(f"live check     could not run ({e.__class__.__name__}: {e}). "
                   f"That is a network or API problem, not evidence the token is bad.")
    return "\n".join(out)


@tool(annotations=WRITE)
async def setup_token(enable_writes: bool = False) -> str:
    """Start a one-time local page for adding your Sleeper token SAFELY.

    Use this instead of ever pasting the token into the conversation. The
    server opens a random, single-use address on 127.0.0.1 that expires in
    five minutes; you open it in the browser where you are logged in to
    Sleeper and follow one of three routes (a one-line console snippet that
    needs nothing copied, a copy-and-paste into a masked field, or the manual
    DevTools path). The token goes browser -> loopback -> this process ->
    config file (0600), is verified against Sleeper before it is saved, and
    takes effect immediately — no restart. It never appears in a tool result.

    Args:
        enable_writes: Also switch writes on (lineups, waivers, trades). The
            page says so in large type and asks the user to tick a box before
            the token is accepted with writes on; an assistant must not set
            this without the user asking for writes. Every write tool still
            dry-runs unless called with confirm=True.
    """
    from .webauth import start
    s = start(enable_writes=enable_writes)
    out = [f"Open this in the browser where you are logged in to Sleeper:\n\n"
           f"    {s.url}\n\n"
           f"The page explains three ways to hand over the token; the first "
           f"needs nothing copied. The link works once and expires in 5 "
           f"minutes. When the page reports success, call auth_status to "
           f"confirm — the running server already uses the new token."]
    if enable_writes:
        out.append("\nTHIS LINK WILL ALSO ENABLE WRITES (lineups, waivers, "
                   "trades) once the user ticks the box on the page.")
    if _config.source("SLEEPER_TOKEN") == "environment":
        out.append("\nNOTE: SLEEPER_TOKEN is also set in this server's "
                   "environment. The saved token takes effect now, but after a "
                   "restart the environment one wins again — remove it from "
                   "the client config or launcher to keep using the saved one.")
    return "\n".join(out)
