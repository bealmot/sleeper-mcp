"""Draft tools: the board, what each pick returned, and traded picks.

Analysis lives in picks.py, pure and tested. This module fetches and formats.

WHAT IS AND IS NOT HERE. Reading a completed draft works any time, and so does
scoring it against what the players went on to do. Driving a LIVE draft —
making a pick, setting a queue, nominating in an auction — is deliberately
absent: those endpoints only do anything while a draft is running, which is one
week a year, so anything built now would ship unexercised and sit untested
until next August. See EXTENDING.md if you want them before then.
"""

from __future__ import annotations

import asyncio

from .client import gql, league, league_id, mcp, players, rest
from .picks import by_roster, value
from .reads import _owners


async def _league_for_season(lg: str, season: str) -> dict | None:
    """Walk the previous_league_id chain to the league for a given season."""
    cfg = await league(lg)
    seen = 0
    while cfg and seen < 12:
        if not season or str(cfg.get("season")) == str(season):
            return cfg
        prev = cfg.get("previous_league_id")
        if not prev or str(prev) in ("0", ""):
            return None
        cfg = await league(str(prev))
        seen += 1
    return None


async def _draft_picks(cfg: dict) -> list[dict]:
    did = cfg.get("draft_id")
    if not did:
        return []
    picks = await rest(f"/draft/{did}/picks") or []
    return [{"pick_no": p.get("pick_no"), "round": p.get("round"),
             "roster_id": p.get("roster_id"), "player_id": p.get("player_id"),
             "is_keeper": p.get("is_keeper")} for p in picks]


@mcp.tool()
async def draft_board(season: str = "", round_: int = 0,
                      league_id_: str = "") -> str:
    """The draft as it happened, with keepers marked.

    Args:
        season: e.g. "2025". Defaults to the league you are configured for.
        round_: Show one round only. 0 means all.
        league_id_: Override the configured league.
    """
    lg = league_id(league_id_ or None)
    cfg = await _league_for_season(lg, season)
    if not cfg:
        return f"  No league found for season {season!r} in this chain."
    P, picks, owner = await asyncio.gather(
        players(), _draft_picks(cfg), _owners(str(cfg["league_id"])))
    if not picks:
        return f"  No draft picks published for {cfg.get('season')}."

    rounds = sorted({p["round"] for p in picks if p["round"]})
    if round_:
        rounds = [r for r in rounds if r == round_] or rounds[:1]
    out = [f"  {cfg.get('name')} {cfg.get('season')} draft — "
           f"{len(picks)} picks, {max(rounds)} rounds shown"
           if not round_ else
           f"  {cfg.get('name')} {cfg.get('season')} draft — round {round_}"]
    for rnd in rounds:
        out += ["", f"  Round {rnd}"]
        for p in sorted((x for x in picks if x["round"] == rnd),
                        key=lambda x: x["pick_no"] or 0):
            v = P.get(str(p["player_id"])) or {}
            tag = "  (keeper)" if p["is_keeper"] else ""
            out.append(f"    {p['pick_no']:>3}  "
                       f"{owner.get(p['roster_id'], '?')[:18]:18} "
                       f"{v.get('position', '?'):>3} "
                       f"{(v.get('full_name') or p['player_id'])[:24]}{tag}")
    return "\n".join(out)


@mcp.tool()
async def draft_review(season: str = "", limit: int = 10,
                       league_id_: str = "") -> str:
    """What each pick actually returned, scored against where it was taken.

    A pick is compared with the players it was actually chosen ahead of AT THE
    SAME POSITION: the eleventh quarterback off the board who finishes as QB3
    is +8. There is no model of "expected points at pick 37" here, because one
    league does not contain enough drafts to fit one and a fitted curve mostly
    encodes whoever fitted it.

    Within position matters more than it sounds. Ranking every position
    together by raw points looks reasonable and is badly wrong — quarterbacks
    outscore the field in most formats, so every late quarterback scores as an
    enormous steal and every early receiver as a bust.

    IT SCORES OUTCOMES, NOT DECISIONS. A pick that worked out and a pick that
    got lucky look identical from here, and nothing that reads only results can
    tell them apart.

    Args:
        season: e.g. "2025". Defaults to the most recent COMPLETED season,
            since scoring a draft needs a season to have happened.
        limit: How many steals and busts to list. Default 10.
        league_id_: Override the configured league.
    """
    from .usage import _season_rows

    lg = league_id(league_id_ or None)
    cfg = await _league_for_season(lg, season)
    if not cfg:
        return f"  No league found for season {season!r} in this chain."
    szn = str(cfg.get("season"))
    if str(cfg.get("status")) != "complete" and not season:
        prev = cfg.get("previous_league_id")
        if prev and str(prev) not in ("0", ""):
            cfg = await league(str(prev))
            szn = str(cfg.get("season"))

    P, picks, owner, rows = await asyncio.gather(
        players(), _draft_picks(cfg), _owners(str(cfg["league_id"])),
        _season_rows(szn))
    if not picks:
        return f"  No draft picks published for {szn}."
    if not rows:
        return (f"  No {szn} season stats, so there is nothing to score the "
                f"{szn} draft against.")

    points = {str(r.get("player_id")): float((r.get("stats") or {})
                                             .get("pts_half_ppr") or 0.0)
              for r in rows}
    # Positions are what keep this from ranking quarterbacks against running
    # backs, which makes every late QB look like a steal.
    positions = {str(p["player_id"]): (P.get(str(p["player_id"])) or {}).get(
        "position", "?") for p in picks if p.get("player_id")}
    scored = value(picks, points, positions)
    named = lambda r: (P.get(r["player_id"]) or {}).get(
        "full_name", r["player_id"])

    out = [f"  {cfg.get('name')} {szn} draft review — {len(scored)} picks",
           "  value = (Nth taken at his position) - (his finish at that "
           "position).",
           "  Within position on purpose: pooling them ranks quarterbacks "
           "against running backs", "  and makes every late QB a steal.",
           "", f"  BEST ({min(limit, len(scored))})",
           f"    {'player':24} {'pos':>3} {'pick':>5} {'taken':>6} "
           f"{'fin':>5} {'value':>6} {'pts':>7}  manager"]
    for r in scored[:limit]:
        out.append(f"    {named(r)[:24]:24} {r['position']:>3} "
                   f"{r['pick_no']:>5} {r['pos_taken']:>6} {r['finish']:>5} "
                   f"{r['value']:>+6} {r['points']:>7.1f}  "
                   f"{owner.get(r['roster_id'], '?')}"
                   + ("  (keeper)" if r["is_keeper"] else ""))
    out += ["", f"  WORST ({min(limit, len(scored))})",
            f"    {'player':24} {'pos':>3} {'pick':>5} {'taken':>6} "
            f"{'fin':>5} {'value':>6} {'pts':>7}  manager"]
    for r in list(reversed(scored))[:limit]:
        out.append(f"    {named(r)[:24]:24} {r['position']:>3} "
                   f"{r['pick_no']:>5} {r['pos_taken']:>6} {r['finish']:>5} "
                   f"{r['value']:>+6} {r['points']:>7.1f}  "
                   f"{owner.get(r['roster_id'], '?')}"
                   + ("  (keeper)" if r["is_keeper"] else ""))

    out += ["", f"  {'manager':20} {'picks':>6} {'total':>8} {'mean':>7}"]
    for rid, n, total, mean in by_roster(scored):
        out.append(f"  {owner.get(rid, f'roster {rid}')[:20]:20} "
                   f"{n:>6} {total:>+8} {mean:>+7.1f}")
    out.append("  Ranked by MEAN — total rewards having more picks.")
    return "\n".join(out)


@mcp.tool()
async def traded_picks(season: str = "", league_id_: str = "") -> str:
    """Future draft picks that are not with the team they belong to.

    NEEDS A TOKEN. Sleeper lists only the EXCEPTIONS — picks that have changed
    hands — so an empty result means nothing has been traded, not that the
    query failed.

    Args:
        season: Restrict to one season, e.g. "2027". Blank means all known.
        league_id_: Override the configured league.
    """
    lg = league_id(league_id_ or None)
    arg = f',season:"{season}"' if season else ""
    rows = (await gql('{roster_draft_picks(league_id:"%s"%s)'
                      '{season round roster_id owner_id previous_owner_id}}'
                      % (lg, arg), auth=True)).get("roster_draft_picks") or []
    if not rows:
        return ("  No draft picks have changed hands"
                + (f" for {season}" if season else "") +
                ". Sleeper lists only traded picks, so this is an empty "
                "exception list, not a failure.")
    owner = await _owners(lg)
    out = [f"  {len(rows)} traded pick(s)", "",
           f"  {'season':>7} {'round':>6}  {'originally':20} {'now held by':20}"]
    for r in sorted(rows, key=lambda x: (str(x.get("season")),
                                         x.get("round") or 0)):
        out.append(f"  {str(r.get('season')):>7} {r.get('round'):>6}  "
                   f"{owner.get(r.get('roster_id'), '?')[:20]:20} "
                   f"{owner.get(r.get('owner_id'), '?')[:20]:20}")
    return "\n".join(out)
