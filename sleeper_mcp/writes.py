"""Mutations. Every one is gated twice, dry-runs by default, and verifies
through GraphQL — or says plainly when it could not.

FOUR RULES, all of them learned the hard way:

1. `roster_update_starters` IS THE WRONG MUTATION. It succeeds, it persists,
   and it changes nothing that scores. The weekly lineup lives in the matchup
   leg — `update_matchup_leg`. A tool built on the wrong one reports success
   forever while the lineup never changes.

2. NEVER VERIFY A WRITE WITH REST. It is Cloudflare-cached and will happily
   return the pre-write state for minutes. Read back over GraphQL, through a
   different query than the one that wrote — or, failing that, trust the
   mutation's own response over a cached feed.

3. A VERIFICATION STEP CAN FAIL ON ITS OWN TERMS. `matchup_legs` is
   authenticated; a read-back that throws is not evidence the write failed. If
   the check itself breaks, say so rather than reporting a failure.

4. A TIMEOUT AFTER SENDING IS NOT A FAILURE. A mutation that times out on the
   read may well have landed; telling the user it "failed" invites a retry
   that sends a second trade offer to a real person. Say "unknown" and point
   at the read that settles it.

Every write here is off unless SLEEPER_ENABLE_WRITES=1, and additionally
defaults to a dry run. That is deliberate: trades and waiver claims reach real
people in someone's league, and an accepted trade may execute with no veto
window at all.

NAMES RESOLVE AGAINST A KNOWN POOL ONLY — the roster for anything you own, the
free-agent pool for a claim — through the one resolver in lookup.py. A defence
goes by team code, city or nickname; a Sleeper player id works anywhere a name
does.
"""

from __future__ import annotations

import datetime as dt
import time

from .client import (READ, WRITE, ConfigError, TransportFailure, cache_clear,
                     current_week, gql, league, league_id, owners, players,
                     require_writes, rest, roster_id, snowflake,
                     starting_slots, tool)
from .lookup import (display_name, fantasy_position, is_fantasy, positions,
                     resolve_names)
from .optimizer import eligible
from . import txn


async def _my(lg: str, rid: int) -> dict:
    rosters = await rest(f"/league/{lg}/rosters")
    me = next((r for r in rosters if r["roster_id"] == rid), None)
    if not me:
        raise ConfigError(f"No roster {rid} in league {lg}.")
    return me


def _active(me: dict) -> tuple[set, set, set]:
    """(startable, on IR, on taxi). IR and taxi players sit INSIDE `players`."""
    ids = {str(p) for p in (me.get("players") or [])}
    reserve = {str(p) for p in (me.get("reserve") or [])}
    taxi = {str(p) for p in (me.get("taxi") or [])}
    return ids - reserve - taxi, reserve, taxi


def _capacity(lgd: dict) -> int:
    """Active roster spots: every slot that is not IR or taxi."""
    return sum(1 for x in (lgd.get("roster_positions") or [])
               if x not in ("IR", "TAXI"))


async def _league_players(lg: str) -> dict[str, dict]:
    """player_id -> settings from the PUBLIC `league_players` query.

    This is where the trade block (`otb`, the roster that listed him) and the
    waiver clock (`waiver_clears_at`, epoch SECONDS) actually live. The REST
    roster carries neither. Tolerant of failure: these are annotations, and a
    write must not be blocked because a decoration could not be fetched.
    """
    try:
        d = await gql('{league_players(league_id:"%s"){player_id settings}}' % lg)
    except Exception:                                   # noqa: BLE001
        return {}
    return {str(r.get("player_id")): (r.get("settings") or {})
            for r in (d.get("league_players") or []) if r.get("player_id")}


def _clears(settings: dict) -> str:
    """'ON WAIVERS until 2026-09-21 04:19 UTC' or 'free agent'."""
    at = (settings or {}).get("waiver_clears_at")
    if at and float(at) > time.time():
        when = dt.datetime.fromtimestamp(float(at), dt.timezone.utc)
        return f"ON WAIVERS until {when:%Y-%m-%d %H:%M} UTC"
    return "free agent"


def _unknown(action: str, e: TransportFailure, plan: str, check: str) -> str:
    return (f"SENT? UNKNOWN — {action} timed out ({e.kind}) after the request "
            f"may have been delivered. Do NOT simply retry: {check} first.\n"
            f"{plan}")


# --- lineup ------------------------------------------------------------------

@tool(annotations=WRITE)
async def set_lineup(players_in_slot_order: list[str], league_id_: str = "",
                     roster_id_: int = 0, week: int = 0,
                     confirm: bool = False) -> str:
    """WRITE. Set your starting lineup for a week.

    Takes player NAMES (or ids) in SLOT ORDER. The slots come from your
    league's own `roster_positions`, so a superflex or 3-WR league works
    correctly without configuration — `league_info` shows the order.

    Refuses, sending nothing, if a name is not on your active roster, is on
    IR, cannot legally fill its slot, or appears twice. The dry run marks
    which slots would change from the lineup as it stands.

    In-week changes work, including on a lineup containing players whose games
    have already kicked off; only the locked players themselves are immovable.

    Args:
        players_in_slot_order: One name per starting slot, in order. A defence
            goes by team code, city or nickname ("DET", "Detroit", "Lions").
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
    active, reserve, taxi = _active(me)
    current = [str(p) for p in (me.get("starters") or [])]

    ids, problems = [], []
    for i, name in enumerate(players_in_slot_order):
        slot = slots[i]
        # A defence resolves only against the defences you own; anything else
        # against the whole active roster. Nothing is ever passed through
        # verbatim as an id.
        pool = ({p for p in active if (P.get(p) or {}).get("position") == "DEF"}
                if slot == "DEF" else active)
        got, bad = resolve_names(P, [name], pool, "your active roster")
        if bad:
            # Say WHY when the reason is IR or taxi rather than a typo.
            on_ir, _ = resolve_names(P, [name], reserve, "IR")
            on_taxi, _ = resolve_names(P, [name], taxi, "taxi")
            if on_ir:
                bad = [f"{display_name(P.get(on_ir[0]), on_ir[0])} is on IR — "
                       f"use set_ir to activate him first"]
            elif on_taxi:
                bad = [f"{display_name(P.get(on_taxi[0]), on_taxi[0])} is on "
                       f"the taxi squad and cannot start"]
            problems.extend(bad)
            continue
        pid = got[0]
        v = P.get(pid) or {}
        if not eligible(slot, positions(v)):
            problems.append(f"{display_name(v, pid)} ({fantasy_position(v)}) "
                            f"cannot fill the {slot} slot")
        ids.append(pid)
    if len(set(ids)) != len(ids):
        dup = next(p for p in ids if ids.count(p) > 1)
        problems.append(f"{display_name(P.get(dup), dup)} appears in two slots")
    if problems:
        return "Refused, nothing sent:\n  " + "\n  ".join(problems)

    def listing(seq):
        rows = []
        for i, p in enumerate(seq[:len(slots)]):
            was = current[i] if i < len(current) else None
            mark = "   *" if was != p else ""
            rows.append(f"{slots[i]:5} {display_name(P.get(str(p)), p)}{mark}")
        return "\n  ".join(rows)

    changes = sum(1 for i, p in enumerate(ids) if i >= len(current) or current[i] != p)
    plan = (f"  {listing(ids)}\n\n  {changes} slot(s) change (*) from the "
            f"lineup as it stands")
    if not confirm:
        return f"DRY RUN — nothing sent.\n{plan}\n\n  Call again with confirm=True."

    require_writes("set_lineup")
    # A write can invalidate cached reads. Clearing is cheap; verifying against
    # a stale cache is not — that is how a check confirms the pre-write state.
    cache_clear()
    try:
        await gql(
            "mutation($r:Int!,$lg:Snowflake!,$leg:Int!,$rid:Int!,$s:[String]){"
            "update_matchup_leg(round:$r,leg:$leg,league_id:$lg,roster_id:$rid,"
            "starters:$s){roster_id starters}}",
            {"r": wk, "leg": wk, "lg": lg, "rid": rid, "s": ids}, auth=True)
    except TransportFailure as e:
        return _unknown("set_lineup", e, plan, "call `roster` and check the lineup")

    # Read back through matchup_legs — a DIFFERENT query from the one that
    # wrote — and note that it is authenticated, so its own failure is not
    # evidence the write failed.
    try:
        chk = await gql('{matchup_legs(round:%d,league_id:"%s")'
                        '{roster_id starters}}' % (wk, lg), auth=True)
    except Exception as e:                              # noqa: BLE001
        return (f"WRITE SENT, VERIFICATION FAILED ({e.__class__.__name__}). "
                f"The mutation returned no error, so the lineup may well be "
                f"set — check the app rather than assuming either way.\n"
                f"  {listing(ids)}")
    live = next((x.get("starters") for x in (chk.get("matchup_legs") or [])
                 if x["roster_id"] == rid), []) or []
    ok = [str(x) for x in live] == [str(x) for x in ids]
    return (f"{'VERIFIED' if ok else 'MISMATCH'} — week {wk} lineup:\n  "
            + listing([str(x) for x in live])
            + ("" if ok else f"\n\n  sent {ids}\n  got  {live}"))


# --- waivers -----------------------------------------------------------------

@tool(annotations=WRITE)
async def waiver_claim(add_player: str, drop_player: str = "", bid: int = 0,
                       league_id_: str = "", roster_id_: int = 0,
                       confirm: bool = False) -> str:
    """WRITE. Submit a waiver claim.

    Says whether the player is ON WAIVERS (a bid, processed when they clear)
    or a FREE AGENT, how much FAAB you have left, and whether you already
    have a claim in on him. Check `league_info` for your league's waiver
    type — a bid is meaningless outside FAAB.

    `submit_waiver_claim` takes PARALLEL k_/v_ arrays: k_adds holds player ids,
    v_adds the roster receiving them, and k_settings/v_settings carry the bid.

    Args:
        add_player: Free agent name, id, or a defence's team code.
        drop_player: Name from your roster. Leave blank if you have an open
            roster spot; refused if you do not.
        bid: FAAB dollars. Default 0.
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
        roster_id_: Defaults to SLEEPER_ROSTER_ID.
        confirm: Must be True to send. Default False = dry run.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    P = await players()
    lgd = await league(lg)
    rosters = await rest(f"/league/{lg}/rosters")
    owned = {str(p) for r in rosters for p in (r.get("players") or [])}
    me = next((r for r in rosters if r["roster_id"] == rid), None)
    if not me:
        raise ConfigError(f"No roster {rid} in league {lg}.")
    active, reserve, taxi = _active(me)
    free = {pid for pid, v in P.items()
            if pid not in owned and v and v.get("team") and is_fantasy(v)}

    add, e1 = resolve_names(P, [add_player], free, "the free-agent pool")
    drop, e2 = [], []
    if (drop_player or "").strip():
        drop, e2 = resolve_names(P, [drop_player], active | reserve, "your roster")
    else:
        capacity = _capacity(lgd)
        if len(active) >= capacity:
            e2 = [f"your active roster is full ({len(active)}/{capacity}) — "
                  f"name a drop_player"]
    if e1 or e2:
        return "Refused, nothing sent:\n  " + "\n  ".join(e1 + e2)
    pid = add[0]

    settings = (lgd.get("settings") or {})
    budget = int(settings.get("waiver_budget") or 0)
    used = int((me.get("settings") or {}).get("waiver_budget_used") or 0)
    left = budget - used
    faab = settings.get("waiver_type") == 2
    if faab and bid > left:
        return (f"Refused, nothing sent:\n  bid ${bid} exceeds your remaining "
                f"FAAB (${left} of ${budget}).")

    lp = await _league_players(lg)
    status = _clears(lp.get(pid, {}))
    existing = ""
    queued, _note = await txn.pending(lg)
    for t in queued:
        if t.get("type") == "waiver" and rid in (t.get("roster_ids") or []) \
                and pid in (t.get("adds") or {}):
            existing = (f"\n  NOTE  you already have a claim on him: id="
                        f"{t.get('transaction_id')} bid ${txn.bid_of(t)} — "
                        f"use change_bid or cancel_claim rather than a second claim")

    plan = (f"  ADD   {display_name(P.get(pid), pid)}  ({status})\n"
            f"  DROP  {display_name(P.get(drop[0]), drop[0]) if drop else '(none — open roster spot)'}\n"
            f"  BID   ${bid}" + (f"  (you have ${left} of ${budget} left)" if faab else "")
            + existing)
    if not confirm:
        return f"DRY RUN — nothing sent.\n{plan}\n\n  Call again with confirm=True."

    require_writes("waiver_claim")
    cache_clear()
    try:
        d = await gql(
            "mutation($lg:Snowflake!,$ka:[String],$va:[Int],$kd:[String],"
            "$vd:[Int],$ks:[String],$vs:[Int]){submit_waiver_claim(league_id:$lg,"
            "k_adds:$ka,v_adds:$va,k_drops:$kd,v_drops:$vd,k_settings:$ks,"
            "v_settings:$vs){transaction_id status}}",
            {"lg": lg, "ka": add, "va": [rid], "kd": drop, "vd": [rid] if drop else [],
             "ks": ["waiver_bid"], "vs": [bid]}, auth=True)
    except TransportFailure as e:
        return _unknown("waiver_claim", e, plan, "call `pending`")
    res = d.get("submit_waiver_claim") or {}
    tid = res.get("transaction_id")
    # Read back through the authenticated pending list — the one source that
    # carries a claim. The public feed never will, so do not point there.
    try:
        queued, _ = await txn.pending(lg)
        seen = any(str(t.get("transaction_id")) == str(tid) for t in queued)
    except Exception:                                   # noqa: BLE001
        seen = None
    state = ("VERIFIED — queued" if seen else
             "SUBMITTED (read-back could not confirm it yet)" if seen is None
             else "SUBMITTED but NOT FOUND in the pending list — check the app")
    return (f"{state}: id={tid} status={res.get('status')}\n{plan}\n\n"
            f"  `pending` lists it; cancel_claim({tid!r}) withdraws it; "
            f"change_bid adjusts the bid.")


@tool(annotations=WRITE)
async def change_bid(transaction_id: str, bid: int, week: int = 0,
                     league_id_: str = "", confirm: bool = False) -> str:
    """WRITE. Change the FAAB bid on one of your pending waiver claims.

    News lands between a Tuesday claim and a Wednesday run; this adjusts the
    bid in place instead of cancelling and resubmitting.

    Args:
        transaction_id: From `pending` or from the claim's own confirmation.
        bid: The new FAAB amount.
        week: The claim's leg. 0 (default) uses the current week.
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
        confirm: Must be True to send. Default False = dry run.
    """
    lg = league_id(league_id_ or None)
    wk = week or await current_week()
    claim, plan = await _find_pending(lg, transaction_id, "waiver")
    if not claim:
        return plan
    old = txn.bid_of(claim)
    plan += f"\n  BID   ${old} -> ${bid}"
    if not confirm:
        return f"DRY RUN — nothing sent.\n{plan}\n\n  Call again with confirm=True."
    require_writes("change_bid")
    cache_clear()
    try:
        await gql("mutation($leg:Int!,$lg:Snowflake!,$tx:Snowflake!,$ks:[String],"
                  "$vs:[Int]){update_waiver_claim(leg:$leg,league_id:$lg,"
                  "transaction_id:$tx,k_settings:$ks,v_settings:$vs)"
                  "{transaction_id status}}",
                  {"leg": int(claim.get("leg") or wk), "lg": lg, "tx": transaction_id,
                   "ks": ["waiver_bid"], "vs": [bid]}, auth=True)
    except TransportFailure as e:
        return _unknown("change_bid", e, plan, "call `pending`")
    queued, _ = await txn.pending(lg)
    now = next((txn.bid_of(t) for t in queued
                if str(t.get("transaction_id")) == str(transaction_id)), None)
    ok = now == bid
    return f"{'VERIFIED' if ok else 'MISMATCH'} — bid is now ${now}.\n{plan}"


async def _find_pending(lg: str, transaction_id: str, kind: str
                        ) -> tuple[dict | None, str]:
    """One pending transaction of `kind`, rendered — or a refusal."""
    queued, note = await txn.pending(lg)
    hit = next((t for t in queued
                if str(t.get("transaction_id")) == str(transaction_id)), None)
    if not hit:
        ids = ", ".join(f"{t.get('transaction_id')} ({t.get('type')})"
                        for t in queued) or "none"
        return None, (f"Refused, nothing sent: no pending transaction "
                      f"{transaction_id!r}. Pending now: {ids}."
                      + (f"\n  {note}" if note else ""))
    if kind and hit.get("type") != kind:
        return None, (f"Refused, nothing sent: {transaction_id} is a "
                      f"{hit.get('type')}, not a {kind}.")
    P, owner = await players(), await owners(lg)
    return hit, "\n".join("  " + line for line in txn.render(hit, P, owner, "        ", True))


@tool(annotations=WRITE)
async def cancel_claim(transaction_id: str, week: int = 0,
                       league_id_: str = "", confirm: bool = False) -> str:
    """WRITE. Withdraw one of your pending waiver claims.

    Get transaction_id from `pending` (needs a token: claims are private to
    the claimant and the public feed never lists them). Useful when news
    lands after a claim goes in and before waivers process.

    Args:
        transaction_id: The claim to withdraw.
        week: The claim's leg. 0 (default) uses the claim's own leg.
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
        confirm: Must be True to send. Default False = dry run.
    """
    lg = league_id(league_id_ or None)
    claim, plan = await _find_pending(lg, transaction_id, "waiver")
    if not claim:
        return plan
    leg = week or int(claim.get("leg") or 0) or await current_week()
    if not confirm:
        return (f"DRY RUN — would CANCEL this claim (leg {leg}):\n{plan}\n\n"
                f"  Call again with confirm=True.")
    require_writes("cancel_claim")
    cache_clear()
    try:
        await gql("mutation($leg:Int!,$lg:Snowflake!,$tx:Snowflake!){"
                  "cancel_waiver_claim(leg:$leg,league_id:$lg,transaction_id:$tx)"
                  "{transaction_id status}}",
                  {"leg": leg, "lg": lg, "tx": transaction_id}, auth=True)
    except TransportFailure as e:
        return _unknown("cancel_claim", e, plan, "call `pending`")
    queued, _ = await txn.pending(lg)
    still = any(str(t.get("transaction_id")) == str(transaction_id) for t in queued)
    return (f"{'MISMATCH — still pending' if still else 'VERIFIED — cancelled'} "
            f"{transaction_id}.\n{plan}")


# --- IR ---------------------------------------------------------------------

@tool(annotations=WRITE)
async def set_ir(player_names: list[str], league_id_: str = "",
                 roster_id_: int = 0, confirm: bool = False) -> str:
    """WRITE. Set which players occupy your IR slots.

    `reserve` is the complete list, not a delta — pass everyone who should be
    on IR, or an empty list to clear it. Refuses more players than the league
    has IR slots, and warns when a player's injury status is not one the
    league allows on IR or when he is a current starter.

    NOTE: Sleeper models IR as a SUBSET of your roster. A reserve player appears
    in BOTH the players list and the reserve list, and does not show on the
    bench because he occupies the IR slot. That is not a bug.

    Args:
        player_names: Everyone who should be on IR. [] clears it.
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
        roster_id_: Defaults to SLEEPER_ROSTER_ID.
        confirm: Must be True to send. Default False = dry run.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    P = await players()
    lgd = await league(lg)
    me = await _my(lg, rid)
    mine = {str(p) for p in (me.get("players") or [])}
    chosen, bad = resolve_names(P, player_names, mine, "your roster")
    if bad:
        return "Refused, nothing sent:\n  " + "\n  ".join(bad)
    s = lgd.get("settings") or {}
    cap = int(s.get("reserve_slots") or 0)
    if len(chosen) > cap:
        return (f"Refused, nothing sent: {len(chosen)} players but this league "
                f"has {cap} IR slot(s).")

    allowed = {k.replace("reserve_allow_", "").upper()
               for k, v in s.items() if k.startswith("reserve_allow_") and v}
    starters = {str(p) for p in (me.get("starters") or [])}
    warn = []
    for pid in chosen:
        v = P.get(pid) or {}
        st = (v.get("injury_status") or "").upper()
        if allowed and st not in allowed:
            warn.append(f"{display_name(v, pid)} is '{st or 'healthy'}'; this "
                        f"league allows IR for {', '.join(sorted(allowed))} — "
                        f"Sleeper may reject him")
        if pid in starters:
            warn.append(f"{display_name(v, pid)} is a current starter — "
                        f"set_lineup afterwards")

    show = lambda ids: ", ".join(display_name(P.get(i), i) for i in ids) or "(empty)"
    current = [str(p) for p in (me.get("reserve") or [])]
    plan = f"  IR now:   {show(current)}\n  IR after: {show(chosen)}"
    if warn:
        plan += "\n  WARN  " + "\n  WARN  ".join(warn)
    if not confirm:
        return f"DRY RUN — nothing sent.\n{plan}\n\n  Call again with confirm=True."

    require_writes("set_ir")
    cache_clear()
    try:
        d = await gql("mutation($lg:Snowflake!,$rid:Int!,$r:[String]){"
                      "roster_update_reserve(league_id:$lg,roster_id:$rid,reserve:$r)"
                      "{roster_id reserve}}",
                      {"lg": lg, "rid": rid, "r": chosen}, auth=True)
    except TransportFailure as e:
        return _unknown("set_ir", e, plan, "call `roster`")
    # VERIFY OVER GRAPHQL, NEVER REST. The mutation's own response is the
    # primary read-back; a second, different query confirms it when it can.
    now = [str(p) for p in ((d.get("roster_update_reserve") or {}).get("reserve") or [])]
    how = "mutation response"
    try:
        chk = await gql('{league_rosters(league_id:"%s"){roster_id reserve}}' % lg,
                        auth=True)
        row = next((r for r in (chk.get("league_rosters") or [])
                    if r.get("roster_id") == rid), None)
        if row is not None:
            now, how = [str(p) for p in (row.get("reserve") or [])], "league_rosters"
    except Exception:                                   # noqa: BLE001
        pass
    ok = set(now) == set(chosen)
    return (f"{'VERIFIED' if ok else 'MISMATCH'} (via {how}) — IR now: "
            f"{show(now)}\n{plan}")


# --- trade block -------------------------------------------------------------

@tool(annotations=WRITE)
async def trade_block(add: list[str] | None = None,
                      remove: list[str] | None = None,
                      league_id_: str = "", roster_id_: int = 0,
                      confirm: bool = False) -> str:
    """Read (no arguments) or WRITE (add/remove) which of your players are
    advertised as available.

    The block is how a trade starts without messaging anyone — the whole
    league can see it. It lives per player in the public `league_players`
    query (`settings.otb` is the roster that listed him), not on the roster.

    Args:
        add: Names to put on the block.
        remove: Names to take off it.
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
        roster_id_: Defaults to SLEEPER_ROSTER_ID.
        confirm: Must be True to send. Default False = dry run.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    P = await players()
    me = await _my(lg, rid)
    mine = {str(p) for p in (me.get("players") or [])}

    async def block() -> dict[str, dict]:
        lp = await _league_players(lg)
        return {pid: st for pid, st in lp.items()
                if st.get("otb") is not None and pid in mine}

    cur = await block()
    nm = lambda p: display_name(P.get(p), p)

    def listing(b):
        rows = []
        for pid, st in sorted(b.items(), key=lambda kv: nm(kv[0])):
            since = st.get("otb_added_at")
            when = (f"  listed {dt.datetime.fromtimestamp(since / 1000, dt.timezone.utc):%Y-%m-%d}"
                    if since else "")
            rows.append(f"  {fantasy_position(P.get(pid)) or '?':4} {nm(pid)}{when}")
        return "\n".join(rows)

    if not add and not remove:
        if not cur:
            return "Trade block is empty — the league sees nothing from you."
        return "Trade block:\n" + listing(cur)

    a, e1 = resolve_names(P, add or [], mine, "your roster")
    r, e2 = resolve_names(P, remove or [], set(cur), "your trade block")
    if e1 or e2:
        return "Refused, nothing sent:\n  " + "\n  ".join(e1 + e2)
    plan = "\n".join([f"  ADD    {nm(p)}" for p in a]
                     + [f"  REMOVE {nm(p)}" for p in r])
    if not confirm:
        return (f"DRY RUN — nothing sent.\n{plan}\n\n  This is visible to the "
                f"whole league. Call again with confirm=True.")

    require_writes("trade_block")
    cache_clear()
    # LeaguePlayer has no roster_id field; selecting one made every write a
    # GraphQL validation error before anything was executed.
    try:
        for p in a:
            await gql('mutation{add_league_player_trade_block(player_id:"%s",'
                      'league_id:"%s"){player_id settings}}' % (p, lg), auth=True)
        for p in r:
            await gql('mutation{remove_league_player_trade_block(player_id:"%s",'
                      'league_id:"%s"){player_id settings}}' % (p, lg), auth=True)
    except TransportFailure as e:
        return _unknown("trade_block", e, plan, "call trade_block() to read it")
    after = await block()
    ok = all(p in after for p in a) and not any(p in after for p in r)
    return (f"{'VERIFIED' if ok else 'MISMATCH'} — trade block now:\n"
            + (listing(after) or "  (empty)") + f"\n{plan}")


# --- trades -------------------------------------------------------------------

async def _manager(lg: str, who: str, rosters: list) -> tuple[dict | None, str]:
    """Match a manager by display name or roster id, keyed by roster — two
    rosters with the same display name (or none) must not collapse into one."""
    owner = await owners(lg)
    want = (who or "").strip().lower()
    hits = [r for r in rosters
            if want and (want == str(r["roster_id"])
                         or want in (owner.get(r["roster_id"]) or "").lower())]
    if len(hits) != 1:
        opts = ", ".join(f"{owner.get(r['roster_id'], '?')} (roster {r['roster_id']})"
                         for r in sorted(rosters, key=lambda r: r["roster_id"]))
        return None, (f"Refused: {who!r} matched {len(hits)} manager(s). "
                      f"Managers: {opts}")
    return hits[0], owner.get(hits[0]["roster_id"], f"roster {hits[0]['roster_id']}")


@tool(annotations=WRITE)
async def propose_trade(give_players: list[str], receive_players: list[str],
                        with_manager: str, faab: int = 0,
                        league_id_: str = "", roster_id_: int = 0,
                        confirm: bool = False) -> str:
    """WRITE. Offer a trade to another manager.

    THIS REACHES A REAL PERSON. Check `league_info` for `trade_review_days` —
    where it is 0, an accepted trade executes IMMEDIATELY with no league vote
    and no veto window. Refused after the trade deadline or where the league
    has trades disabled.

    WIRE FORMAT. The mutation is sent the way Sleeper itself records a
    completed trade: every player appears in BOTH maps, `adds` keyed by the
    roster RECEIVING him and `drops` by the roster that holds him now, and a
    FAAB transfer as "sender,receiver,amount". That shape was read off real
    transaction records (REST and GraphQL agree on it) rather than captured
    from the app, so the first live send from this tool is worth watching.

    Args:
        give_players: Names from YOUR roster.
        receive_players: Names from THEIR roster.
        with_manager: Their display name in the league, or their roster id.
        faab: FAAB dollars you send them, if your league trades budget.
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
        roster_id_: Defaults to SLEEPER_ROSTER_ID.
        confirm: Must be True to send. Default False = dry run.
    """
    lg, rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    P = await players()
    lgd = await league(lg)
    s = lgd.get("settings") or {}
    if s.get("disable_trades"):
        return "Refused: this league has trades DISABLED."
    wk = await current_week()
    deadline = s.get("trade_deadline")
    if deadline not in (None, 99) and wk > int(deadline):
        return f"Refused: the trade deadline was week {deadline}; it is week {wk}."

    rosters = await rest(f"/league/{lg}/rosters")
    them, name = await _manager(lg, with_manager, rosters)
    if not them:
        return name
    if them["roster_id"] == rid:
        return "Refused: that is you."

    me = next(r for r in rosters if r["roster_id"] == rid)
    mine = {str(p) for p in (me.get("players") or [])}
    theirs = {str(p) for p in (them.get("players") or [])}
    g, e1 = resolve_names(P, give_players, mine, "your roster")
    r, e2 = resolve_names(P, receive_players, theirs, f"{name}'s roster")
    if e1 or e2:
        return "Refused, nothing sent:\n  " + "\n  ".join(e1 + e2)
    if not (g or faab) or not r:
        return "Refused: a trade needs something on each side."

    nm = lambda p: display_name(P.get(p), p)
    plan = (f"  WITH     {name} (roster {them['roster_id']})\n"
            f"  YOU GIVE {', '.join(map(nm, g)) or '-'}"
            + (f"  + ${faab} FAAB" if faab else "") + "\n"
            f"  YOU GET  {', '.join(map(nm, r))}")
    if s.get("trade_review_days") == 0:
        plan += "\n  NOTE  review period is 0 days: if accepted it executes at once"
    if not confirm:
        return (f"DRY RUN — nothing sent.\n{plan}\n\n  This offer goes to a "
                f"real person. Call again with confirm=True.")

    require_writes("propose_trade")
    cache_clear()
    tid = them["roster_id"]
    try:
        d = await gql(
            "mutation($lg:Snowflake!,$ka:[String],$va:[Int],$kd:[String],"
            "$vd:[Int],$wb:[String]){propose_trade(league_id:$lg,k_adds:$ka,"
            "v_adds:$va,k_drops:$kd,v_drops:$vd,waiver_budget:$wb)"
            "{transaction_id status}}",
            {"lg": lg,
             "ka": r + g, "va": [rid] * len(r) + [tid] * len(g),
             "kd": r + g, "vd": [tid] * len(r) + [rid] * len(g),
             "wb": [f"{rid},{tid},{faab}"] if faab else None},
            auth=True)
    except TransportFailure as e:
        return _unknown("propose_trade", e, plan, "call `pending`")
    res = d.get("propose_trade") or {}
    queued, _ = await txn.pending(lg)
    seen = any(str(t.get("transaction_id")) == str(res.get("transaction_id"))
               for t in queued)
    return (f"{'VERIFIED — offered' if seen else 'SENT'}: id="
            f"{res.get('transaction_id')} status={res.get('status')}\n{plan}")


@tool(annotations=WRITE)
async def respond_trade(transaction_id: str, response: str, week: int = 0,
                        league_id_: str = "", confirm: bool = False) -> str:
    """WRITE. Accept or reject a trade offered to you.

    ACCEPTING MAY BE IRREVERSIBLE — where `trade_review_days` is 0 it executes
    on acceptance with no veto window. The dry run shows exactly what changes
    hands, including picks and FAAB, and refuses an offer that is not pending
    or does not involve your roster.

    Args:
        transaction_id: From `pending`.
        response: "accept" or "reject".
        week: The offer's leg. 0 (default) uses the offer's own leg.
        league_id_: Defaults to SLEEPER_LEAGUE_ID.
        confirm: Must be True to send. Default False = dry run.
    """
    resp = (response or "").lower().strip()
    if resp not in ("accept", "reject"):
        return f"Refused: response must be 'accept' or 'reject', got {response!r}."
    lg = league_id(league_id_ or None)
    offer, plan = await _find_pending(lg, transaction_id, "trade")
    if not offer:
        return plan
    lgd = await league(lg)
    if lgd.get("settings", {}).get("trade_review_days") == 0 and resp == "accept":
        plan += "\n  NOTE  review period is 0 days: accepting executes IMMEDIATELY"
    leg = week or int(offer.get("leg") or 0) or await current_week()
    if not confirm:
        return (f"DRY RUN — would {resp.upper()} trade {transaction_id}:\n{plan}\n\n"
                f"  Call again with confirm=True.")
    require_writes("respond_trade")
    cache_clear()
    op = "accept_trade" if resp == "accept" else "reject_trade"
    try:
        d = await gql("mutation($leg:Int!,$lg:Snowflake!,$tx:Snowflake!){"
                      f"{op}(leg:$leg,league_id:$lg,transaction_id:$tx)"
                      "{transaction_id status}}",
                      {"leg": leg, "lg": lg, "tx": transaction_id}, auth=True)
    except TransportFailure as e:
        return _unknown("respond_trade", e, plan, "call `pending`")
    status = (d.get(op) or {}).get("status")
    queued, _ = await txn.pending(lg)
    still = any(str(t.get("transaction_id")) == str(transaction_id) for t in queued)
    return (f"{'MISMATCH — still pending' if still else 'VERIFIED'} — "
            f"{resp.upper()}ED {transaction_id} (status {status}).\n{plan}")


# --- pick'em -------------------------------------------------------------------

@tool(annotations=WRITE)
async def pickem_pick(game_id: str, team: str, week: int = 0,
                      pickem_league: str = "", pickem_roster: int = 0,
                      confirm: bool = False) -> str:
    """WRITE. Make or change one pick'em pick.

    Pick'em has NO web interface, so this may be the only way to fix an entry
    from a desktop. Get game_id from `pickem_status`. NEEDS A TOKEN even for
    the dry run, because reading the current pick is authenticated.

    Args:
        game_id: Sleeper game id, e.g. "202609140".
        team: Team abbreviation to pick, e.g. "SEA".
        week: NFL week. 0 (default) uses the current week.
        pickem_league: Defaults to SLEEPER_PICKEM_LEAGUE.
        pickem_roster: Defaults to SLEEPER_PICKEM_ROSTER.
        confirm: Must be True to send. Default False = dry run.
    """
    from . import client
    lg = str(pickem_league or client.DEFAULT_PICKEM_LEAGUE or "").strip()
    rid = pickem_roster or client.DEFAULT_PICKEM_ROSTER
    if not lg or rid is None:
        raise ConfigError("Needs SLEEPER_PICKEM_LEAGUE and SLEEPER_PICKEM_ROSTER "
                          "(or the arguments). find_my_pools discovers them.")
    lg = snowflake(lg, "pick'em league id")
    wk = week or await current_week()
    leg_id = f"v1:regular:{wk}"
    team = (team or "").upper().strip()

    async def read_leg():
        # get_pickem_legs returns EVERY week's leg; pick the one asked for.
        cur = await gql('{get_pickem_legs(league_id:"%s",roster_id:%d)'
                        '{leg_id picks}}' % (lg, int(rid)), auth=True)
        return next((l for l in (cur.get("get_pickem_legs") or [])
                     if l.get("leg_id") == leg_id), None)

    leg = await read_leg()
    if leg is None:
        return f"Refused: no pick'em leg for week {wk} ({leg_id})."
    old = (leg.get("picks") or {}).get(game_id)
    was = f" (currently {old['team']})" if old else ""
    if not confirm:
        return (f"DRY RUN — would pick {team} in game {game_id}{was}. "
                f"Call again with confirm=True.")

    require_writes("pickem_pick")
    cache_clear()
    try:
        await gql(
            "mutation($p:InputPickemPick!,$lg:Snowflake!,$rid:Int!,$leg:String!,"
            "$old:InputPickemPick){make_pickem_pick(pick:$p,league_id:$lg,"
            "roster_id:$rid,leg_id:$leg,pick_to_replace:$old){leg_id}}",
            {"p": {"game_id": game_id, "team": team, "outcome": "win"},
             "lg": lg, "rid": int(rid), "leg": leg_id,
             "old": ({"game_id": game_id, "team": old["team"], "outcome": "win"}
                     if old else None)}, auth=True)
    except TransportFailure as e:
        return _unknown("pickem_pick", e, f"  {team} in {game_id}", "call `pickem_status`")
    back = await read_leg()
    now = ((back or {}).get("picks") or {}).get(game_id) or {}
    ok = now.get("team") == team
    return (f"{'VERIFIED' if ok else 'MISMATCH'} — game {game_id} is now "
            f"{now.get('team')}{was}.")


# --- watchlist -------------------------------------------------------------------

@tool(annotations=WRITE)
async def watch_player(player_name: str, unwatch: bool = False,
                       confirm: bool = False) -> str:
    """WRITE. Add or remove a player from your Sleeper watchlist.

    GOTCHA: `watch_player` returns a Player OBJECT and needs a subfield
    selection; `unwatch_player` returns a plain Boolean and must NOT have one.
    One shared query template cannot serve both.

    Args:
        player_name: Full or partial name, or a Sleeper player id.
        unwatch: True to remove instead of add.
        confirm: Must be True to send. Default False = dry run.
    """
    from .lookup import ambiguous, find_player
    P = await players()
    hits = find_player(P, player_name, allow_unsigned=True)
    if len(hits) != 1:
        return ambiguous(player_name, hits)
    pid, v = hits[0]
    verb = "UNWATCH" if unwatch else "WATCH"
    if not confirm:
        return (f"DRY RUN — would {verb} {display_name(v, pid)} (id {pid}). "
                f"Call again with confirm=True.")
    require_writes("watch_player")
    cache_clear()
    season = (await rest("/state/nfl")).get("season")
    try:
        if unwatch:
            await gql('mutation{unwatch_player(sport:"nfl",season:"%s",'
                      'player_id:"%s")}' % (season, pid), auth=True)
        else:
            await gql('mutation{watch_player(sport:"nfl",season:"%s",'
                      'player_id:"%s"){player_id}}' % (season, pid), auth=True)
    except TransportFailure as e:
        return _unknown("watch_player", e, f"  {verb} {display_name(v, pid)}",
                        "call `watched_players`")
    back = await gql('{watched_players(sport:"nfl"){player_id}}', auth=True)
    ids = {w["player_id"] for w in (back.get("watched_players") or [])}
    ok = (pid not in ids) if unwatch else (pid in ids)
    return (f"{'VERIFIED' if ok else 'MISMATCH'} — "
            f"{'un' if unwatch else ''}watched {display_name(v, pid)}. "
            f"Watchlist now holds {len(ids)}.")


__all__ = ["set_lineup", "waiver_claim", "change_bid", "cancel_claim", "set_ir",
           "trade_block", "propose_trade", "respond_trade", "pickem_pick",
           "watch_player", "READ"]
