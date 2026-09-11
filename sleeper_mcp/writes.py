"""Mutations. Every one is gated twice and verified afterwards.

THREE RULES, all of them learned the hard way:

1. `roster_update_starters` IS THE WRONG MUTATION. It succeeds, it persists,
   and it changes nothing that scores. The weekly lineup lives in the matchup
   leg — `update_matchup_leg`. A tool built on the wrong one reports success
   forever while the lineup never changes.

2. NEVER VERIFY A WRITE WITH REST. It is Cloudflare-cached and will happily
   return the pre-write state for minutes. Read back over GraphQL, through a
   different query than the one that wrote.

3. A VERIFICATION STEP CAN FAIL ON ITS OWN TERMS. `matchup_legs` is
   authenticated; a read-back that throws is not evidence the write failed. If
   the check itself breaks, say so rather than reporting a failure.

Every write here is off unless SLEEPER_ENABLE_WRITES=1, and additionally
defaults to a dry run. That is deliberate: trades and waiver claims reach real
people in someone's league, and an accepted trade may execute with no veto
window at all.
"""

from __future__ import annotations

import datetime as dt

from .client import (ConfigError, cache_clear, current_week, gql, league,
                     league_id, mcp, players, require_writes, rest, roster_id,
                     starting_slots)


async def _my(lg: str, rid: int) -> dict:
    rosters = await rest(f"/league/{lg}/rosters")
    me = next((r for r in rosters if r["roster_id"] == rid), None)
    if not me:
        raise ConfigError(f"No roster {rid} in league {lg}.")
    return me


def _resolve(P: dict, names: list[str], pool: set, label: str):
    """Names -> ids, against a KNOWN pool only.

    Never resolve against the full player dictionary: it holds ~11,000 entries
    including retired players, and names are not unique. Restricting to a
    roster is what makes name input safe.
    """
    got, bad = [], []
    for want in names:
        w = want.lower().strip()
        hits = [p for p in pool
                if w in ((P.get(p) or {}).get("full_name") or "").lower()]
        if len(hits) == 1:
            got.append(hits[0])
        elif not hits:
            bad.append(f"{want!r} is not on {label}")
        else:
            bad.append(f"{want!r} is ambiguous on {label}: "
                       + ", ".join((P.get(h) or {}).get("full_name", h)
                                   for h in hits))
    return got, bad


@mcp.tool()
async def set_lineup(players_in_slot_order: list[str], league_id_: str = "",
                     roster_id_: int = 0, week: int = 0,
                     confirm: bool = False) -> str:
    """WRITE. Set your starting lineup for a week.

    Takes player NAMES in SLOT ORDER. The slots come from your league's own
    `roster_positions`, so a superflex or 3-WR league works correctly without
    configuration — call `league_info` to see the order you must supply.

    In-week changes work, including on a lineup containing players whose games
    have already kicked off; only the locked players themselves are immovable.

    Args:
        players_in_slot_order: One name per starting slot, in order. Use a team
            code for a defence, e.g. "NE".
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
        roster_id_: Defaults to SLEEPER_ROSTER_ID.
        week: NFL week. 0 (default) uses the current week.
        confirm: Must be True to send. Default False = dry run.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    wk = week or await current_week()
    slots = await starting_slots(lg)
    if len(players_in_slot_order) != len(slots):
        return (f"Refused: this league starts {len(slots)} "
                f"({' '.join(slots)}), got {len(players_in_slot_order)}.")

    P = await players()
    me = await _my(lg, rid)
    pool = {str(p) for p in (me.get("players") or [])}
    # A defence is submitted as its team code, not a numeric id, and will not
    # be in the roster's player list in the same form.
    ids, problems = [], []
    for i, name in enumerate(players_in_slot_order):
        if slots[i] == "DEF" or (len(name) <= 4 and name.isupper()):
            ids.append(name.upper())
            continue
        got, bad = _resolve(P, [name], pool, "your roster")
        ids.extend(got)
        problems.extend(bad)
    if problems:
        return "Refused, nothing sent:\n  " + "\n  ".join(problems)

    listing = "\n  ".join(f"{slots[i]:5} {(P.get(p) or {}).get('full_name', p)}"
                          for i, p in enumerate(ids))
    if not confirm:
        return f"DRY RUN — nothing sent.\n  {listing}\n\n  Call again with confirm=True."

    require_writes("set_lineup")
    # A write can invalidate cached reads. Clearing is cheap; verifying against
    # a stale cache is not — that is how a check confirms the pre-write state.
    cache_clear()
    await gql(
        "mutation($r:Int!,$lg:Snowflake!,$leg:Int!,$rid:Int!,$s:[String]){"
        "update_matchup_leg(round:$r,leg:$leg,league_id:$lg,roster_id:$rid,"
        "starters:$s){roster_id starters}}",
        {"r": wk, "leg": wk, "lg": lg, "rid": rid, "s": ids}, auth=True)

    # Read back through matchup_legs — a DIFFERENT query from the one that
    # wrote — and note that it is authenticated, so its own failure is not
    # evidence the write failed.
    try:
        chk = await gql('{matchup_legs(round:%d,league_id:"%s")'
                        '{roster_id starters}}' % (wk, lg), auth=True)
    except Exception as e:
        return (f"WRITE SENT, VERIFICATION FAILED ({e.__class__.__name__}). "
                f"The mutation returned no error, so the lineup may well be "
                f"set — check the app rather than assuming either way.\n"
                f"  {listing}")
    live = next((x.get("starters") for x in (chk.get("matchup_legs") or [])
                 if x["roster_id"] == rid), []) or []
    ok = [str(x) for x in live] == [str(x) for x in ids]
    return (f"{'VERIFIED' if ok else 'MISMATCH'} — week {wk} lineup:\n  "
            + "\n  ".join(f"{slots[i]:5} "
                          f"{(P.get(str(p)) or {}).get('full_name', p)}"
                          for i, p in enumerate(live[:len(slots)]))
            + ("" if ok else f"\n\n  sent {ids}\n  got  {live}"))


@mcp.tool()
async def waiver_claim(add_player: str, drop_player: str, bid: int = 0,
                       league_id_: str = "", roster_id_: int = 0,
                       confirm: bool = False) -> str:
    """WRITE. Submit a waiver claim.

    `submit_waiver_claim` takes PARALLEL k_/v_ arrays: k_adds holds player ids,
    v_adds the roster receiving them, and k_settings/v_settings carry the FAAB
    bid. Check `league_info` for your league's waiver type — a bid is
    meaningless outside FAAB.

    Args:
        add_player: Free agent name.
        drop_player: Name from your roster.
        bid: FAAB dollars. Default 0.
        confirm: Must be True to send. Default False = dry run.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    P = await players()
    rosters = await rest(f"/league/{lg}/rosters")
    owned = {str(p) for r in rosters for p in (r.get("players") or [])}
    me = await _my(lg, rid)
    mine = {str(p) for p in (me.get("players") or [])}
    free = {pid for pid, v in P.items()
            if pid not in owned and v.get("team")
            and v.get("position") in ("QB", "RB", "WR", "TE", "K", "DEF")}

    add, e1 = _resolve(P, [add_player], free, "the free-agent pool")
    drop, e2 = _resolve(P, [drop_player], mine, "your roster")
    if e1 or e2:
        return "Refused, nothing sent:\n  " + "\n  ".join(e1 + e2)

    plan = (f"  ADD   {(P.get(add[0]) or {}).get('full_name')}\n"
            f"  DROP  {(P.get(drop[0]) or {}).get('full_name')}\n"
            f"  BID   ${bid}")
    if not confirm:
        return f"DRY RUN — nothing sent.\n{plan}\n\n  Call again with confirm=True."

    require_writes("waiver_claim")
    cache_clear()
    d = await gql(
        "mutation($lg:Snowflake!,$ka:[String],$va:[Int],$kd:[String],"
        "$vd:[Int],$ks:[String],$vs:[Int]){submit_waiver_claim(league_id:$lg,"
        "k_adds:$ka,v_adds:$va,k_drops:$kd,v_drops:$vd,k_settings:$ks,"
        "v_settings:$vs){transaction_id status}}",
        {"lg": lg, "ka": add, "va": [rid], "kd": drop, "vd": [rid],
         "ks": ["waiver_bid"], "vs": [bid]}, auth=True)
    return (f"SUBMITTED — {d.get('submit_waiver_claim') or 'no body returned'}\n"
            f"{plan}\n\n  Check `pending` to see it queued.")


@mcp.tool()
async def cancel_claim(transaction_id: str, leg: int = 0,
                       league_id_: str = "", confirm: bool = False) -> str:
    """WRITE. Withdraw one of your pending waiver claims.

    Get transaction_id from `pending`. Useful when news lands after a claim
    goes in and before waivers process.
    """
    lg = league_id(league_id_ or None)
    lgg = leg or await current_week()
    if not confirm:
        return (f"DRY RUN — would CANCEL claim {transaction_id} (leg {lgg}). "
                f"Call again with confirm=True.")
    require_writes("cancel_claim")
    await gql("mutation($leg:Int!,$lg:Snowflake!,$tx:Snowflake!){"
              "cancel_waiver_claim(leg:$leg,league_id:$lg,transaction_id:$tx)"
              "{transaction_id status}}",
              {"leg": lgg, "lg": lg, "tx": transaction_id}, auth=True)
    return f"Cancelled {transaction_id}. Check `pending` to confirm."


@mcp.tool()
async def set_ir(player_names: list[str], league_id_: str = "",
                 roster_id_: int = 0, confirm: bool = False) -> str:
    """WRITE. Set which players occupy your IR slots.

    `reserve` is the complete list, not a delta — pass everyone who should be
    on IR, or an empty list to clear it. Which injury designations qualify is a
    league setting (`reserve_allow_out`, `reserve_allow_doubtful` and friends);
    see `league_info`.

    NOTE: Sleeper models IR as a SUBSET of your roster. A reserve player appears
    in BOTH the players list and the reserve list, and does not show on the
    bench because he occupies the IR slot. That is not a bug.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    P = await players()
    me = await _my(lg, rid)
    mine = {str(p) for p in (me.get("players") or [])}
    chosen, bad = _resolve(P, player_names, mine, "your roster")
    if bad:
        return "Refused, nothing sent:\n  " + "\n  ".join(bad)
    show = lambda ids: ", ".join((P.get(i) or {}).get("full_name", i)
                                 for i in ids) or "(empty)"
    current = [str(p) for p in (me.get("reserve") or [])]
    plan = f"  IR now:   {show(current)}\n  IR after: {show(chosen)}"
    if not confirm:
        return f"DRY RUN — nothing sent.\n{plan}\n\n  Call again with confirm=True."

    require_writes("set_ir")
    cache_clear()
    await gql("mutation($lg:Snowflake!,$rid:Int!,$r:[String]){"
              "roster_update_reserve(league_id:$lg,roster_id:$rid,reserve:$r)"
              "{roster_id reserve}}",
              {"lg": lg, "rid": rid, "r": chosen}, auth=True)
    after = await rest(f"/league/{lg}/rosters")
    now = [str(p) for p in
           (next(r for r in after if r["roster_id"] == rid).get("reserve") or [])]
    ok = set(now) == set(chosen)
    return (f"{'VERIFIED' if ok else 'MISMATCH'} — IR now: {show(now)}\n{plan}"
            + ("" if ok else "\n\n  NOTE: that read came from REST, which is "
                             "cached. Recheck in the app before trusting a "
                             "mismatch."))


@mcp.tool()
async def trade_block(add: list[str] | None = None,
                      remove: list[str] | None = None,
                      league_id_: str = "", roster_id_: int = 0,
                      confirm: bool = False) -> str:
    """Read or set which of your players are advertised as available.

    The trade block is how a trade starts without messaging anyone — the whole
    league can see it. Call with no arguments to read it.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    P = await players()
    me = await _my(lg, rid)
    mine = {str(p) for p in (me.get("players") or [])}
    cur = {str(x) for x in (me.get("player_trade_block")
                            or (me.get("metadata") or {}).get("trade_block")
                            or [])}
    nm = lambda p: (P.get(p) or {}).get("full_name", p)

    if not add and not remove:
        if not cur:
            return "Trade block is empty — the league sees nothing from you."
        return ("Trade block:\n  " + "\n  ".join(sorted(map(nm, cur))))

    a, e1 = _resolve(P, add or [], mine, "your roster")
    r, e2 = _resolve(P, remove or [], cur, "your trade block")
    if e1 or e2:
        return "Refused, nothing sent:\n  " + "\n  ".join(e1 + e2)
    plan = "\n".join([f"  ADD    {nm(p)}" for p in a]
                     + [f"  REMOVE {nm(p)}" for p in r])
    if not confirm:
        return (f"DRY RUN — nothing sent.\n{plan}\n\n  This is visible to the "
                f"whole league. Call again with confirm=True.")

    require_writes("trade_block")
    cache_clear()
    for p in a:
        await gql('mutation{add_league_player_trade_block(player_id:"%s",'
                  'league_id:"%s"){roster_id}}' % (p, lg), auth=True)
    for p in r:
        await gql('mutation{remove_league_player_trade_block(player_id:"%s",'
                  'league_id:"%s"){roster_id}}' % (p, lg), auth=True)
    return f"Applied.\n{plan}"


@mcp.tool()
async def propose_trade(give_players: list[str], receive_players: list[str],
                        with_manager: str, faab: int = 0,
                        league_id_: str = "", roster_id_: int = 0,
                        confirm: bool = False) -> str:
    """WRITE. Offer a trade to another manager.

    THIS REACHES A REAL PERSON. Check `league_info` for `trade_review_days` —
    where it is 0, an accepted trade executes IMMEDIATELY with no league vote
    and no veto window.

    Args:
        give_players: Names from YOUR roster.
        receive_players: Names from THEIR roster.
        with_manager: Their display name in the league.
        faab: FAAB dollars to include, if your league trades budget.
        confirm: Must be True to send. Default False = dry run.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    P = await players()
    users = await rest(f"/league/{lg}/users")
    rosters = await rest(f"/league/{lg}/rosters")
    who = {u["user_id"]: (u.get("display_name") or u.get("username"))
           for u in users}
    by_name = {who.get(r.get("owner_id"), "?"): r for r in rosters}
    hit = [n for n in by_name if with_manager.lower().strip() in n.lower()]
    if len(hit) != 1:
        return (f"Refused: {with_manager!r} matched {len(hit)}. Managers: "
                + ", ".join(sorted(by_name)))
    them = by_name[hit[0]]
    if them["roster_id"] == rid:
        return "Refused: that is you."

    mine = {str(p) for p in ((await _my(lg, rid)).get("players") or [])}
    theirs = {str(p) for p in (them.get("players") or [])}
    g, e1 = _resolve(P, give_players, mine, "your roster")
    r, e2 = _resolve(P, receive_players, theirs, f"{hit[0]}'s roster")
    if e1 or e2:
        return "Refused, nothing sent:\n  " + "\n  ".join(e1 + e2)
    if not (g or faab) or not r:
        return "Refused: a trade needs something on each side."

    nm = lambda p: (P.get(p) or {}).get("full_name", p)
    plan = (f"  WITH     {hit[0]}\n"
            f"  YOU GIVE {', '.join(map(nm, g)) or '-'}"
            + (f"  + ${faab} FAAB" if faab else "") + "\n"
            f"  YOU GET  {', '.join(map(nm, r))}")
    if not confirm:
        return (f"DRY RUN — nothing sent.\n{plan}\n\n  This offer goes to a "
                f"real person. Call again with confirm=True.")

    require_writes("propose_trade")
    cache_clear()
    d = await gql(
        "mutation($lg:Snowflake!,$ka:[String],$va:[Int],$kd:[String],"
        "$vd:[Int],$wb:[String]){propose_trade(league_id:$lg,k_adds:$ka,"
        "v_adds:$va,k_drops:$kd,v_drops:$vd,waiver_budget:$wb)"
        "{transaction_id status}}",
        {"lg": lg, "ka": r, "va": [rid] * len(r), "kd": g,
         "vd": [them["roster_id"]] * len(g),
         "wb": [f"{rid}:{them['roster_id']}:{faab}"] if faab else None},
        auth=True)
    return f"SENT — {d.get('propose_trade') or 'no body'}\n{plan}"


@mcp.tool()
async def respond_trade(transaction_id: str, response: str, leg: int = 0,
                        league_id_: str = "", confirm: bool = False) -> str:
    """WRITE. Accept or reject a trade offered to you.

    ACCEPTING MAY BE IRREVERSIBLE — where `trade_review_days` is 0 it executes
    on acceptance with no veto window. Get transaction_id from `pending`.

    Args:
        response: "accept" or "reject".
    """
    resp = response.lower().strip()
    if resp not in ("accept", "reject"):
        return f"Refused: response must be 'accept' or 'reject', got {response!r}."
    lg = league_id(league_id_ or None)
    lgg = leg or await current_week()
    if not confirm:
        return (f"DRY RUN — would {resp.upper()} trade {transaction_id}. "
                f"Accepting may execute immediately. Call again with "
                f"confirm=True.")
    require_writes("respond_trade")
    cache_clear()
    op = "accept_trade" if resp == "accept" else "reject_trade"
    await gql("mutation($leg:Int!,$lg:Snowflake!,$tx:Snowflake!){"
              f"{op}(leg:$leg,league_id:$lg,transaction_id:$tx)"
              "{transaction_id status}}",
              {"leg": lgg, "lg": lg, "tx": transaction_id}, auth=True)
    return f"{resp.upper()}ED {transaction_id}. Check `pending` to confirm."


@mcp.tool()
async def pickem_pick(game_id: str, team: str, week: int = 0,
                      pickem_league: str = "", pickem_roster: int = 0,
                      confirm: bool = False) -> str:
    """WRITE. Make or change one pick'em pick.

    Pick'em has NO web interface, so this may be the only way to fix an entry
    from a desktop. Get game_id from `pickem_status`.

    Args:
        game_id: Sleeper game id, e.g. "202609140".
        team: Team abbreviation to pick, e.g. "SEA".
        confirm: Must be True to send. Default False = dry run.
    """
    from .client import DEFAULT_PICKEM_LEAGUE, DEFAULT_PICKEM_ROSTER
    lg = (pickem_league or DEFAULT_PICKEM_LEAGUE).strip()
    rid = pickem_roster or DEFAULT_PICKEM_ROSTER
    if not lg or rid is None:
        raise ConfigError("Needs SLEEPER_PICKEM_LEAGUE and "
                          "SLEEPER_PICKEM_ROSTER (or the arguments).")
    wk = week or await current_week()
    leg_id = f"v1:regular:{wk}"
    cur = await gql('{get_pickem_legs(league_id:"%s",roster_id:%d){picks}}'
                    % (lg, rid))
    legs = cur.get("get_pickem_legs") or []
    old = ((legs[0].get("picks") or {}).get(game_id) if legs else None)
    was = f" (currently {old['team']})" if old else ""
    if not confirm:
        return (f"DRY RUN — would pick {team} in game {game_id}{was}. "
                f"Call again with confirm=True.")

    require_writes("pickem_pick")
    cache_clear()
    await gql(
        "mutation($p:InputPickemPick!,$lg:Snowflake!,$rid:Int!,$leg:String!,"
        "$old:InputPickemPick){make_pickem_pick(pick:$p,league_id:$lg,"
        "roster_id:$rid,leg_id:$leg,pick_to_replace:$old){leg_id}}",
        {"p": {"game_id": game_id, "team": team, "outcome": "win"},
         "lg": lg, "rid": rid, "leg": leg_id,
         "old": ({"game_id": game_id, "team": old["team"], "outcome": "win"}
                 if old else None)}, auth=True)
    back = await gql('{get_pickem_legs(league_id:"%s",roster_id:%d){picks}}'
                     % (lg, rid))
    now = (((back.get("get_pickem_legs") or [{}])[0].get("picks") or {})
           .get(game_id) or {})
    ok = now.get("team") == team
    return (f"{'VERIFIED' if ok else 'MISMATCH'} — game {game_id} is now "
            f"{now.get('team')}{was}.")


@mcp.tool()
async def watch_player(player_name: str, unwatch: bool = False) -> str:
    """WRITE. Add or remove a player from your Sleeper watchlist.

    GOTCHA: `watch_player` returns a Player OBJECT and needs a subfield
    selection; `unwatch_player` returns a plain Boolean and must NOT have one.
    One shared query template cannot serve both.
    """
    from .reads import _ambiguous, _find
    require_writes("watch_player")
    cache_clear()
    P = await players()
    hits = _find(P, player_name)
    if len(hits) != 1:
        return _ambiguous(player_name, hits)
    pid, v = hits[0]
    season = (await rest("/state/nfl")).get("season")
    if unwatch:
        await gql('mutation{unwatch_player(sport:"nfl",season:"%s",'
                  'player_id:"%s")}' % (season, pid), auth=True)
    else:
        await gql('mutation{watch_player(sport:"nfl",season:"%s",'
                  'player_id:"%s"){player_id}}' % (season, pid), auth=True)
    back = await gql('{watched_players(sport:"nfl"){player_id}}', auth=True)
    ids = {w["player_id"] for w in (back.get("watched_players") or [])}
    ok = (pid not in ids) if unwatch else (pid in ids)
    return (f"{'VERIFIED' if ok else 'MISMATCH'} — "
            f"{'un' if unwatch else ''}watched {v.get('full_name')}. "
            f"Watchlist now holds {len(ids)}.")
