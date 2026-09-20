"""Transactions: one fetch that sees everything, and one renderer.

TWO SOURCES, NEITHER A SUPERSET. The public REST feed
(`/league/{id}/transactions/{week}`) carries completed moves and failed
claims. It NEVER carries a manager's own pending waiver claim — not as a cache
artefact but at origin: a cache-busted fetch returns none while the same
claim sits in GraphQL. Cancelled and rejected trades are also absent from REST.
`league_transactions_filtered` (authenticated) carries all of those and lacks
the failed claims. So a pending list has to come from GraphQL when a token
exists, and say so when it cannot.

ONE RENDERER. A trade puts every player in BOTH `adds` and `drops` because he
moves between rosters; printed as two lists each player appears twice. Traded
picks arrive as objects from REST and as comma-separated strings from GraphQL;
FAAB transfers sit in `waiver_budget` as a list, while a claim's bid sits in
`settings.waiver_bid`. Three tools used to render these three ways, and only
one of them got all of it right.
"""

from __future__ import annotations

from .client import AuthError, ConfigError, gql, rest
from .lookup import display_name, fantasy_position
from .moves import parse_draft_pick, pick_label

FIELDS = ("transaction_id type status leg created roster_ids adds drops "
          "draft_picks waiver_budget settings creator consenter_ids league_id")


async def pending(lg: str, week: int | None = None) -> tuple[list[dict], str]:
    """Every pending transaction, with where it came from.

    Returns (rows, note). `note` is empty when the authenticated source was
    read; otherwise it says what the caller cannot see and why.
    """
    from . import client
    note = ""
    rows: dict[str, dict] = {}
    if client.TOKEN:
        try:
            args = [f'league_id:"{lg}"', 'status_filters:["pending"]', "limit:200"]
            if week:
                args.append(f"leg_filters:[{int(week)}]")
            d = await gql("{league_transactions_filtered(%s){%s}}"
                          % (",".join(args), FIELDS), auth=True)
            for r in (d.get("league_transactions_filtered") or []):
                rows[str(r.get("transaction_id"))] = r
        except (AuthError, ConfigError) as e:
            note = f"pending waiver claims are not visible: {e}"
    else:
        note = ("pending WAIVER CLAIMS are private to the claimant and only "
                "visible with a token — set SLEEPER_TOKEN to see yours.")
    if week:
        try:
            for t in (await rest(f"/league/{lg}/transactions/{int(week)}") or []):
                if t.get("status") == "pending":
                    rows.setdefault(str(t.get("transaction_id")), t)
        except Exception as e:                       # noqa: BLE001
            note = (note + "; " if note else "") + \
                f"public feed unavailable ({e.__class__.__name__})"
    out = sorted(rows.values(), key=lambda r: r.get("created") or 0, reverse=True)
    return out, note


def bid_of(t: dict):
    """A claim's FAAB bid. It is in settings, NOT in waiver_budget."""
    return (t.get("settings") or {}).get("waiver_bid")


def faab_transfers(t: dict, owner: dict) -> list[str]:
    """'$5 FAAB from Alder to Birch' for each transfer inside a trade."""
    out = []
    for x in (t.get("waiver_budget") or []):
        if isinstance(x, dict):
            out.append(f"${x.get('amount')} FAAB from "
                       f"{owner.get(x.get('sender'), '?')} to "
                       f"{owner.get(x.get('receiver'), '?')}")
        elif isinstance(x, str):                    # "sender,receiver,amount"
            parts = x.split(",")
            if len(parts) == 3:
                try:
                    out.append(f"${int(parts[2])} FAAB from "
                               f"{owner.get(int(parts[0]), '?')} to "
                               f"{owner.get(int(parts[1]), '?')}")
                except ValueError:
                    out.append(f"FAAB {x}")
    return out


def sides(t: dict, P: dict, owner: dict) -> dict[int, list[str]]:
    """What each roster RECEIVES in a trade: players, picks and FAAB."""
    def nm(pid):
        v = P.get(str(pid)) or {}
        return f"{fantasy_position(v) or '?'} {display_name(v, pid)}"

    dest: dict = {}
    for pid, rid in (t.get("adds") or {}).items():
        dest.setdefault(rid, []).append(nm(pid))
    for raw in (t.get("draft_picks") or []):
        pk = parse_draft_pick(raw)
        if pk:
            dest.setdefault(pk.get("owner_id"), []).append(pick_label(pk, owner))
    for x in (t.get("waiver_budget") or []):
        if isinstance(x, dict) and x.get("receiver") is not None:
            dest.setdefault(x["receiver"], []).append(f"${x.get('amount')} FAAB")
    return dest


def render(t: dict, P: dict, owner: dict, indent: str = "      ",
           with_id: bool = False) -> list[str]:
    """Lines for one transaction, correct for every type."""
    def nm(pid):
        v = P.get(str(pid)) or {}
        return f"{fantasy_position(v) or '?'} {display_name(v, pid)}"

    by = ", ".join(owner.get(r, f"roster {r}") for r in (t.get("roster_ids") or []))
    bid = bid_of(t)
    head = f"{t.get('type') or '?':12} {t.get('status') or '?':10} {by}"
    if bid is not None:
        head += f"  bid ${bid}"
    if with_id:
        head += f"  id={t.get('transaction_id')} leg={t.get('leg')}"
    out = [head]
    if t.get("type") == "trade":
        for rid, got in sides(t, P, owner).items():
            out.append(f"{indent}{owner.get(rid, f'roster {rid}')[:18]:18} gets "
                       + ", ".join(got))
    else:
        for pid in (t.get("adds") or {}):
            out.append(f"{indent}+ {nm(pid)}")
        for pid in (t.get("drops") or {}):
            out.append(f"{indent}- {nm(pid)}")
    return out
