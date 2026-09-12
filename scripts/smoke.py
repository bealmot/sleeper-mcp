#!/usr/bin/env python3
"""Call every tool once against a real league and report what survives.

WHY THIS EXISTS SEPARATELY FROM check.py. The pure modules here are well
covered — the maths is tested without a network. The TOOL layer had no test at
all, and the failures it produces are ones no static check can see: a name used
but never imported, a format string on a None, an endpoint that needs a token
when nobody wrote down that it does.

The first run of this found `pickem_status` returning a bare "Unauthorized" for
every user, because it sent its query without a token. Nothing in the source
says an endpoint is authenticated until Sleeper refuses it.

It is NOT part of check.py, because it needs the network, a configured league
and — for some tools — a token, none of which a pre-push hook should require.
Run it before a release, or after touching the fetch layer:

    python scripts/smoke.py            # reads only
    python scripts/smoke.py --season 2025   # tools needing a finished season

Writes are never called. Every write tool dry-runs by default, but this does
not go near them: a smoke test must not be able to change anybody's roster.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def cases(season: str):
    from sleeper_mcp import drafts, keepers, lineups, playoffs, reads, usage
    past = {"season": season} if season else {}
    return [
        ("roster", reads.roster, {}),
        ("matchup", reads.matchup, {}),
        ("standings", reads.standings, {}),
        ("transactions", reads.transactions, {}),
        ("transaction_search", reads.transaction_search, {"limit": 3}),
        ("player_news", reads.player_news, {"player_name": "Josh Allen"}),
        ("player_outlook", reads.player_outlook, {"player_name": "Josh Allen"}),
        ("trending", reads.trending, {}),
        ("pending", reads.pending, {}),
        ("draft_picks", reads.draft_picks, {}),
        ("chat", reads.chat, {}),
        ("watched_players", reads.watched_players, {}),
        ("player_history", reads.player_history,
         {"player_name": "Josh Allen"}),
        ("pickem_status", reads.pickem_status, {}),
        ("pickem_consensus", reads.pickem_consensus, {}),
        ("league_info", __import__("sleeper_mcp.discovery",
                                   fromlist=["x"]).league_info, {}),
        ("auth_status", __import__("sleeper_mcp.discovery",
                                   fromlist=["x"]).auth_status, {}),
        ("waiver_targets", lineups.waiver_targets, {}),
        ("bye_outlook", lineups.bye_outlook, {}),
        ("playoff_odds", playoffs.playoff_odds, {"trials": 500}),
        ("matchup_odds", playoffs.matchup_odds, {}),
        ("schedule_strength", playoffs.schedule_strength, {}),
        ("playoff_bracket", playoffs.playoff_bracket, {}),
        ("standings_trend", playoffs.standings_trend, {"previous": True}),
        ("keepers", keepers.keepers, {}),
        ("usage", usage.usage, {"player_name": "Josh Allen", **past}),
        ("breakouts", usage.breakouts, {"weeks": 4, **past}),
        ("season_leaders", usage.season_leaders, {"position": "WR",
                                                  "limit": 3, **past}),
        ("draft_board", drafts.draft_board, {"round_": 1}),
        ("draft_review", drafts.draft_review, {"limit": 3, **past}),
        ("traded_picks", drafts.traded_picks, {}),
    ]


async def run(season: str) -> int:
    bad = []
    for name, fn, kw in cases(season):
        t = time.monotonic()
        try:
            out = await getattr(fn, "fn", fn)(**kw)
            secs = time.monotonic() - t
            if not isinstance(out, str) or not out.strip():
                bad.append((name, "returned nothing"))
                print(f"  {RED}EMPTY{OFF} {name:20} {secs:5.1f}s")
            else:
                print(f"  {GREEN}ok{OFF}    {name:20} {secs:5.1f}s  "
                      f"{DIM}{len(out.splitlines())} lines{OFF}")
        except Exception as e:
            bad.append((name, f"{e.__class__.__name__}: {e}"))
            print(f"  {RED}FAIL{OFF}  {name:20} {time.monotonic() - t:5.1f}s  "
                  f"{e.__class__.__name__}: {str(e)[:70]}")
    print()
    if bad:
        print(f"  {RED}{len(bad)} tool(s) failed{OFF}")
        for name, why in bad:
            print(f"    {name}: {why}")
        return 1
    print(f"  {GREEN}all {len(cases(season))} tools ran{OFF}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", default="",
                    help="a finished season for the tools that need one")
    args = ap.parse_args()
    try:
        from sleeper_mcp.client import DEFAULT_LEAGUE
    except Exception as e:
        print(f"  cannot import the package: {e}")
        return 1
    if not DEFAULT_LEAGUE:
        print("  SLEEPER_LEAGUE_ID is not set. This calls real endpoints "
              "against a real league;\n  there is nothing to call without one.")
        return 1
    return asyncio.run(run(args.season))


if __name__ == "__main__":
    sys.exit(main())
