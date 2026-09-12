"""Optional: bring your own player signal.

Everything else in this server reads Sleeper. These tools read something YOU
produce — a ranking, a conviction score, a subscription's ratings, a
spreadsheet — and set it against Sleeper's own numbers and against what your
league-mates are actually doing.

The server ships no data. Point `SLEEPER_SIGNAL_FILE` at a JSON file and these
tools switch on; without it they explain the format and do nothing else.

## The format

```json
{
  "source":    "Whatever produced this",
  "generated": "2026-09-10",
  "scale":     "optional note on what score means",
  "players": {
    "4866": { "score": 21, "evidence": 25, "trend": "up",
              "updated": "20260909", "note": "why, in a few words" }
  }
}
```

**Keyed by SLEEPER PLAYER ID.** This is the one hard requirement and it is
deliberate: player names are not unique — Sleeper's dictionary holds ~11,000
entries including retired players, and "Kenneth Walker" matches two — so any
format keyed by name eventually attaches your signal to the wrong person. Ids
are in `/v1/players/nfl`, and `player_news` prints one for any player you name.

Only `score` is required.

    score      REQUIRED. Any numeric scale, any range. Higher is better.
               It is compared as a PERCENTILE WITHIN POSITION, never as a raw
               value, so your units never need to match Sleeper's.
    evidence   How much data backs this entry — claim count, sample size,
               anything monotonic. Used to filter thin entries. Default 1.
    trend      "up" | "down" | null. Recent direction, if you track it.
    updated    Free-form date. Shown so a stale entry is visible as stale.
    note       Free text. Shown verbatim; never parsed.

## Why percentile-within-position

A projection in points and a conviction count are not comparable, and neither
is a quarterback's 22 points against a tight end's 9. Ranking both sides within
position turns two incomparable scales into one comparable one, and makes the
question "does this source rank him higher or lower than Sleeper does" answerable
without either side needing to know the other's units.
"""

from __future__ import annotations

import json
import os

from .client import (current_week, gql, league, league_id, mcp, players, rest,
                     roster_id, scored)
from .optimizer import percentile_within as _pct_within

SIGNAL_FILE = (os.environ.get("SLEEPER_SIGNAL_FILE") or "").strip()

_HOWTO = """No signal file configured.

These tools read a file YOU produce and compare it against Sleeper. Set
SLEEPER_SIGNAL_FILE to a JSON file shaped like:

  {
    "source": "My rankings",
    "players": {
      "4866": {"score": 21, "evidence": 25, "trend": "up"}
    }
  }

Keyed by SLEEPER PLAYER ID — names are not unique and will eventually attach
your signal to a retired player of the same name. Only "score" is required, on
any scale you like: it is compared as a percentile within position, so your
units never have to match Sleeper's.

See the module docstring in signals.py for the full format."""


def load() -> tuple[dict, str]:
    """(players_map, source_label). Raises nothing — returns empty on failure."""
    if not SIGNAL_FILE:
        return {}, ""
    try:
        with open(os.path.expanduser(SIGNAL_FILE), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {}, f"UNREADABLE ({e.__class__.__name__})"
    rows = d.get("players") or {}
    label = d.get("source") or os.path.basename(SIGNAL_FILE)
    if d.get("generated"):
        label += f", generated {d['generated']}"
    return rows, label


@mcp.tool()
async def signal_divergence(min_evidence: int = 1, gap: int = 25,
                            limit: int = 15, league_id_: str = "",
                            week: int = 0) -> str:
    """Where your signal and Sleeper's projection disagree most.

    Both sides are converted to percentile ranks WITHIN POSITION, so a
    conviction count and a points projection become comparable without either
    needing to know the other's units.

    A large positive gap means your source rates him far above where Sleeper's
    projection puts him — the classic sleeper-pick shape. A large negative gap
    means Sleeper likes production your source argues against.

    Args:
        min_evidence: Ignore entries with less backing than this. Default 1.
        gap: Minimum percentile gap to report. Default 25.
        limit: Rows per direction. Default 15.
        week: NFL week. 0 (default) uses the current week.
    """
    sig, label = load()
    if not sig:
        return _HOWTO if not label else f"Signal file {label}."

    lg = league_id(league_id_ or None)
    wk = week or await current_week()
    P = await players()
    lgd = await league(lg)
    scoring = lgd.get("scoring_settings") or {}

    ids = [pid for pid in sig
           if (P.get(pid) or {}).get("team")
           and float((sig[pid] or {}).get("evidence", 1) or 1) >= min_evidence]
    if not ids:
        return (f"No usable entries in {label} — none matched a rostered NFL "
                f"player with evidence >= {min_evidence}.")

    proj = {}
    for i in range(0, len(ids), 150):
        chunk = ids[i:i + 150]
        d = await gql(
            '{stats_for_players_in_week(sport:"nfl",season:"%s",'
            'season_type:"regular",week:%d,player_ids:%s,category:"proj")'
            '{player_id stats}}'
            % (lgd.get("season"), wk,
               "[" + ",".join(f'"{c}"' for c in chunk) + "]"))
        for r in (d.get("stats_for_players_in_week") or []):
            proj[r["player_id"]] = scored(r["stats"], scoring)

    rosters = await rest(f"/league/{lg}/rosters")
    owned = {str(p) for r in rosters for p in (r.get("players") or [])}

    by_pos: dict[str, list[str]] = {}
    for pid in ids:
        pos = (P.get(pid) or {}).get("position")
        if pos and pid in proj:
            by_pos.setdefault(pos, []).append(pid)

    rows = []
    for pos, group in by_pos.items():
        if len(group) < 3:
            continue                      # a percentile over two players is noise
        s_pct = _pct_within({p: float(sig[p].get("score", 0)) for p in group})
        p_pct = _pct_within({p: proj[p] for p in group})
        for pid in group:
            d = round(s_pct[pid] - p_pct[pid])
            if abs(d) >= gap:
                v = P.get(pid) or {}
                rows.append((d, pos, v.get("full_name") or pid, v.get("team"),
                             proj[pid], sig[pid], pid in owned))

    out = [f"Signal vs Sleeper projection — week {wk}",
           f"  source: {label}",
           f"  {len(ids)} entries compared, percentile within position, "
           f"gap >= {gap}", ""]
    for direction, title, note in (
            (1, "YOUR SIGNAL HIGH, PROJECTION LOW",
             "your source rates him well above the model"),
            (-1, "PROJECTION HIGH, YOUR SIGNAL LOW",
             "the model likes production your source argues against")):
        sel = sorted([r for r in rows if r[0] * direction > 0],
                     key=lambda r: -abs(r[0]))[:limit]
        out.append(f"  {title}   ({note})")
        for d, pos, name, team, pts, entry, own in sel:
            extra = []
            if entry.get("trend"):
                extra.append(f"trend {entry['trend']}")
            if entry.get("evidence"):
                extra.append(f"n={entry['evidence']}")
            if entry.get("note"):
                extra.append(str(entry["note"])[:40])
            out.append(f"    {d:+4}  {pos:4} {name[:22]:22} {team or '-':4} "
                       f"proj {pts:5.1f}  score {entry.get('score')}"
                       f"  {'ROSTERED' if own else 'FREE'}"
                       + (f"  [{' · '.join(extra)}]" if extra else ""))
        if not sel:
            out.append("    (none)")
        out.append("")
    return "\n".join(out)


@mcp.tool()
async def player_signal(player_name: str) -> str:
    """What your signal says about one player, next to Sleeper's own view."""
    sig, label = load()
    if not sig:
        return _HOWTO if not label else f"Signal file {label}."
    from .lookup import ambiguous, find_player
    P = await players()
    hits = find_player(P, player_name)
    if len(hits) != 1:
        return ambiguous(player_name, hits)
    pid, v = hits[0]
    entry = sig.get(pid)
    out = [f"{v.get('full_name')} — {v.get('position')} {v.get('team')}",
           f"  sleeper_id  {pid}",
           f"  status      {v.get('injury_status') or 'healthy'}"
           f"   depth_chart {v.get('depth_chart_order')}", ""]
    if not entry:
        out.append(f"  {label} has NO entry for this player.")
        out.append("  That is not a negative signal — it is an absence. Do not "
                   "read it as one.")
        return "\n".join(out)
    out.append(f"  {label}")
    for k in ("score", "evidence", "trend", "updated", "note"):
        if entry.get(k) is not None:
            out.append(f"    {k:10} {entry[k]}")
    return "\n".join(out)


@mcp.tool()
async def trade_targets(min_evidence: int = 1, limit: int = 12,
                        league_id_: str = "", roster_id_: int = 0) -> str:
    """Mispriced players on RIVAL rosters, using revealed preference.

    This works with ANY signal source, including a plain ranking.

    The insight: a rival's LINEUP is his own valuation of his players, stated
    every week for free. Setting that against a source he does not have gives
    two archetypes —

      BUY LOW    he BENCHED someone your signal rates highly. He is not using
                 the asset, so it is cheap to ask about.
      SELL HIGH  he is STARTING someone your signal rates poorly. His price is
                 at its peak precisely because he believes in him.

    An INJURED player benched is listed separately and is NOT evidence of
    mispricing: the bench is explained by the injury. Buying an injured asset
    can still be right, but it is a bet on the injury rather than on the owner
    being wrong, and conflating the two dresses up the most obvious fact in the
    league as an edge.

    Early-season caution: a week-1 bench reflects draft-day opinion, not
    anything observed. This gets meaningful once managers have seen their teams
    play.

    Args:
        min_evidence: Ignore thin entries. Default 1.
        limit: Rows per category. Default 12.
    """
    sig, label = load()
    if not sig:
        return _HOWTO if not label else f"Signal file {label}."
    lg, mine_rid = league_id(league_id_ or None), roster_id(roster_id_ or None)
    P = await players()
    users = await rest(f"/league/{lg}/users")
    rosters = await rest(f"/league/{lg}/rosters")
    who = {u["user_id"]: (u.get("display_name") or u.get("username"))
           for u in users}
    owner = {r["roster_id"]: who.get(r.get("owner_id"), "?") for r in rosters}

    scores = {p: float(e.get("score", 0)) for p, e in sig.items()
              if float(e.get("evidence", 1) or 1) >= min_evidence}
    if not scores:
        return f"No entries in {label} with evidence >= {min_evidence}."
    by_pos: dict[str, dict] = {}
    for pid, sc in scores.items():
        pos = (P.get(pid) or {}).get("position")
        if pos:
            by_pos.setdefault(pos, {})[pid] = sc
    pct = {}
    for pos, group in by_pos.items():
        if len(group) >= 3:
            pct.update(_pct_within(group))

    buy, sell, hurt = [], [], []
    for r in rosters:
        rid = r["roster_id"]
        if rid == mine_rid:
            continue
        starters = {str(p) for p in (r.get("starters") or [])}
        for pid in (str(p) for p in (r.get("players") or [])):
            p = pct.get(pid)
            if p is None:
                continue
            v = P.get(pid) or {}
            inj = (v.get("injury_status") or "").upper()
            row = (p, v.get("position"), v.get("full_name") or pid,
                   v.get("team"), owner.get(rid, "?"), sig[pid], inj)
            if pid not in starters and p >= 75:
                (hurt if inj in ("OUT", "DOUBTFUL", "IR", "PUP", "SUS", "NA")
                 else buy).append(row)
            elif pid in starters and p <= 25:
                sell.append(row)

    buy.sort(key=lambda r: -r[0])
    sell.sort(key=lambda r: r[0])
    hurt.sort(key=lambda r: -r[0])

    def block(rows, verb):
        lines = []
        for p, pos, name, team, own, entry, inj in rows[:limit]:
            tag = f"  [{inj}]" if inj else ""
            extra = f"  trend {entry['trend']}" if entry.get("trend") else ""
            lines.append(f"  {p:5.0f}%  {pos or '?':4} {name[:22]:22} "
                         f"{team or '-':4} {verb} {own[:14]}{tag}{extra}")
        return lines or ["  (none)"]

    out = [f"Trade targets — {label}", ""]
    out.append(f"BUY LOW ({len(buy)}) — benched HEALTHY, your signal rates high")
    out += block(buy, "held by")
    out += ["", f"BENCHED BUT INJURED ({len(hurt)}) — the injury explains the "
            f"bench, so this is NOT evidence of mispricing"]
    out += block(hurt, "held by")
    out += ["", f"SELL HIGH ({len(sell)}) — STARTING someone your signal rates low"]
    out += block(sell, "started by")
    out += ["", "  Percentages are your signal's percentile within position.",
            "  A rival's lineup is his valuation, published weekly for free. "
            "This checks it against a source he does not have."]
    return "\n".join(out)
