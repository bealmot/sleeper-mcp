"""Finding your own ids — the first thing a new user needs.

Sleeper's ids are not visible in the app's UI, and every other tool here wants
one. All three lookups below are PUBLIC: no token, no login, just a username.
"""

from __future__ import annotations

import datetime as dt

from . import config as _config
from .client import (DEFAULT_LEAGUE, DEFAULT_ROSTER, TOKEN, WRITES_ENABLED,
                     AuthError, gql, mcp, rest)


@mcp.tool()
async def find_my_leagues(username: str, season: str = "") -> str:
    """Look up your Sleeper user id, your leagues, and your roster id in each.

    Start here. Everything this returns is what the other tools need, and none
    of it requires a token — it is all public.

    Args:
        username: Your Sleeper username (the display name you log in with).
        season: Season year, e.g. "2026". Defaults to the current season.
    """
    try:
        user = await rest(f"/user/{username}")
    except Exception as e:
        return (f"Could not find user {username!r} ({e.__class__.__name__}). "
                f"Use the username you log in with, not a team name.")
    if not user or not user.get("user_id"):
        return f"No Sleeper user called {username!r}."
    uid = user["user_id"]

    if not season:
        st = await rest("/state/nfl")
        season = str(st.get("season") or dt.date.today().year)

    leagues = await rest(f"/user/{uid}/leagues/nfl/{season}") or []
    out = [f"{user.get('display_name') or username}",
           f"  SLEEPER_USER_ID   {uid}",
           f"  season            {season}",
           f"  leagues           {len(leagues)}", ""]
    if not leagues:
        out.append("  No NFL leagues found for that season. Try another year "
                   "with season=\"2025\".")
        return "\n".join(out)

    for lg in leagues:
        lid = lg.get("league_id")
        rosters = await rest(f"/league/{lid}/rosters") or []
        mine = next((r for r in rosters if r.get("owner_id") == uid), None)
        slots = [p for p in (lg.get("roster_positions") or [])
                 if p not in ("BN", "IR", "TAXI")]
        out.append(f"  {lg.get('name')}")
        out.append(f"    SLEEPER_LEAGUE_ID  {lid}")
        out.append(f"    SLEEPER_ROSTER_ID  "
                   f"{mine.get('roster_id') if mine else '?? (not an owner)'}")
        out.append(f"    teams {lg.get('total_rosters')}  ·  "
                   f"starters {'/'.join(slots)}")
        out.append("")

    out.append("  Set SLEEPER_LEAGUE_ID and SLEEPER_ROSTER_ID for the league "
               "you want as the default, or pass league_id= per call.")
    return "\n".join(out)


@mcp.tool()
async def league_info(league_id: str = "") -> str:
    """League settings that change how everything else should be read.

    Scoring, roster slots, waiver type and the trade rules all vary by league,
    and several of them silently change what a tool's output means — a lineup
    is only valid against this league's slot layout, and points are only
    meaningful against this league's scoring.

    Args:
        league_id: Defaults to SLEEPER_LEAGUE_ID.
    """
    from .client import league as _league
    lg = await _league(league_id or None)
    s = lg.get("settings") or {}
    slots = [p for p in (lg.get("roster_positions") or [])
             if p not in ("BN", "IR", "TAXI")]
    bench = sum(1 for p in (lg.get("roster_positions") or []) if p == "BN")
    sc = lg.get("scoring_settings") or {}

    waiver = {0: "rolling waivers", 1: "reverse standings", 2: "FAAB"}.get(
        s.get("waiver_type"), f"type {s.get('waiver_type')}")

    out = [f"{lg.get('name')}  ({lg.get('season')} {lg.get('season_type')})",
           f"  league_id      {lg.get('league_id')}",
           f"  teams          {lg.get('total_rosters')}",
           f"  starters       {' '.join(slots)}",
           f"  bench          {bench}   IR slots {s.get('reserve_slots', 0)}",
           "",
           f"  waivers        {waiver}"
           + (f", budget {s.get('waiver_budget')}"
              if s.get("waiver_budget") else ""),
           f"  waiver day     {s.get('waiver_day_of_week')}   "
           f"clears in {s.get('waiver_clear_days')} day(s)",
           f"  trade deadline week {s.get('trade_deadline')}   "
           f"review {s.get('trade_review_days')} day(s)"
           + ("  <- 0 means an accepted trade executes IMMEDIATELY, with no "
              "veto window" if s.get("trade_review_days") == 0 else ""),
           f"  playoffs       week {s.get('playoff_week_start')}",
           ""]

    # Surface the scoring rules most likely to make a naive reading wrong.
    notable = [("rec", "per reception"), ("rec_fd", "per receiving first down"),
               ("rush_fd", "per rushing first down"),
               ("pass_td", "per passing TD"), ("bonus_rec_te", "TE premium")]
    out.append("  scoring worth knowing")
    for k, label in notable:
        if k in sc:
            out.append(f"    {label:28} {sc[k]}")
    ppr = sc.get("rec", 0)
    style = ("PPR" if ppr >= 1 else "half-PPR" if ppr >= 0.5
             else "zero-PPR" if ppr == 0 else f"{ppr} per reception")
    out.append(f"    -> this is a {style} league")
    if sc.get("rec_fd") or sc.get("rush_fd"):
        out.append("    -> it also pays FIRST DOWNS, which most rankings and "
                   "projections do not account for")
    return "\n".join(out)


@mcp.tool()
async def auth_status() -> str:
    """Where each setting came from and whether the token actually works.

    Call this first when a write or an authenticated read fails. It reports
    the token's length, shape and source (environment or config file), names
    the usual delivery mistakes — an unexpanded ${SLEEPER_TOKEN}, surrounding
    quotes, a "Bearer " prefix — and then asks Sleeper whether it accepts the
    token. The token itself is never included in the output.
    """
    path = _config.config_path()
    out = [f"config file    {path}  ({'present' if path.exists() else 'absent'})",
           f"token          {_config.describe_token(TOKEN)}"]
    for p in _config.diagnose_token(TOKEN):
        out.append(f"  !! {p}")
    out.append(f"writes         {'ENABLED' if WRITES_ENABLED else 'disabled'}  "
               f"(from {_config.source('SLEEPER_ENABLE_WRITES')})")
    out.append(f"league id      {DEFAULT_LEAGUE or '-'}  "
               f"(from {_config.source('SLEEPER_LEAGUE_ID')})")
    out.append(f"roster id      {DEFAULT_ROSTER if DEFAULT_ROSTER is not None else '-'}  "
               f"(from {_config.source('SLEEPER_ROSTER_ID')})")
    out.append("")
    if not TOKEN:
        out.append("live check     skipped — no token. Reads work without one; "
                   "run `sleeper-mcp setup` to add one for writes.")
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
