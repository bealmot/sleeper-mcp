"""A fake Sleeper, so the tool layer can be tested without a network.

WHY THIS IS WORTH THE TROUBLE. The pure modules here are near 100% covered and
the tool modules were at 0%, which is not an accident of laziness: a tool
fetches, joins and formats, and none of that can run without responses. So the
bugs that reached users were all in that layer — a format string on a None, a
synthetic row treated as a player, an endpoint that turned out to need a token.

The fixtures below are SHAPED LIKE REAL RESPONSES, including the parts nobody
would invent: the TEAM_<abbr> aggregate row that sits among the players, a
retired duplicate with no team, `is_keeper` on a draft pick, a traded draft
pick encoded as a comma-separated string, a trade that lists each player in
both adds and drops. Every one of those shipped a bug because a hand-written
fixture omitted it.

It is deliberately not a complete Sleeper. Anything unrecognised raises, so a
tool reaching for something unfixtured fails loudly here rather than being
handed a plausible empty list.
"""

from __future__ import annotations

import re

LEAGUE = "111"
PICKEM_LEAGUE = "222"
PICKEM_ROSTER = 1
PREV_LEAGUE = "110"
ROSTER = 1

PLAYERS = {
    "qb1": {"full_name": "Ant Quarterback", "position": "QB", "team": "AAA"},
    "rb1": {"full_name": "Bee Runner", "position": "RB", "team": "AAA"},
    "rb2": {"full_name": "Cat Runner", "position": "RB", "team": "BBB"},
    "wr1": {"full_name": "Doe Catcher", "position": "WR", "team": "AAA"},
    "wr2": {"full_name": "Elk Catcher", "position": "WR", "team": "BBB"},
    "wr3": {"full_name": "Fox Catcher", "position": "WR", "team": "CCC"},
    "te1": {"full_name": "Gnu Tight", "position": "TE", "team": "AAA"},
    "k1": {"full_name": "Hen Kicker", "position": "K", "team": "BBB"},
    "rb3": {"full_name": "Jay Runner", "position": "RB", "team": "CCC"},
    "AAA": {"full_name": "Aardvarks", "position": "DEF", "team": "AAA"},
    # A retired duplicate with no team — the Kenneth Walker shape.
    "rb1_old": {"full_name": "Bee Runner", "position": "RB", "team": None},
    # A coach, who must never be matchable.
    "hc1": {"full_name": "Ibis Coach", "position": "HC", "team": "AAA"},
}

ROSTERS = [
    {"roster_id": 1, "owner_id": "u1",
     "players": ["qb1", "rb1", "wr1", "te1", "k1", "AAA", "rb3"],
     "reserve": [], "keepers": ["rb1"], "player_trade_block": [],
     "settings": {"wins": 3, "losses": 1, "ties": 0, "fpts": 400,
                  "fpts_decimal": 50, "fpts_against": 380,
                  "fpts_against_decimal": 0}},
    {"roster_id": 2, "owner_id": "u2",
     "players": ["rb2", "wr2"], "reserve": [], "keepers": None,
     "settings": {"wins": 1, "losses": 3, "ties": 0, "fpts": 350,
                  "fpts_decimal": 0, "fpts_against": 400,
                  "fpts_against_decimal": 0}},
]

USERS = [{"user_id": "u1", "display_name": "Alder"},
         {"user_id": "u2", "display_name": "Birch"}]

LEAGUE_CFG = {
    "league_id": LEAGUE, "name": "Fake League", "season": "2026",
    "status": "in_season", "total_rosters": 2, "draft_id": "d1",
    "previous_league_id": PREV_LEAGUE,
    "roster_positions": ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF", "BN"],
    "scoring_settings": {"rec": 0.5, "pass_td": 4, "rush_td": 6, "rec_td": 6,
                         "pass_yd": 0.04, "rush_yd": 0.1, "rec_yd": 0.1},
    "settings": {"playoff_week_start": 15, "playoff_teams": 2,
                 "max_keepers": 1, "waiver_type": 2, "trade_review_days": 1},
}
PREV_CFG = {**LEAGUE_CFG, "league_id": PREV_LEAGUE, "season": "2025",
            "status": "complete", "draft_id": "d0",
            "previous_league_id": None}

DRAFT_PICKS = [
    {"pick_no": 1, "round": 1, "roster_id": 1, "player_id": "rb1",
     "is_keeper": True, "picked_by": "u1"},
    {"pick_no": 2, "round": 1, "roster_id": 2, "player_id": "wr2",
     "is_keeper": None, "picked_by": "u2"},
    {"pick_no": 3, "round": 2, "roster_id": 1, "player_id": "wr1",
     "is_keeper": None, "picked_by": "u1"},
    {"pick_no": 4, "round": 2, "roster_id": 2, "player_id": "qb1",
     "is_keeper": None, "picked_by": "u2"},
]

# A trade lists every player in BOTH adds and drops, because he moves between
# rosters — the shape that once printed each player twice.
TRANSACTIONS = [
    {"transaction_id": "t1", "type": "trade", "status": "complete", "leg": 3,
     "created": 1758644097060, "roster_ids": [1, 2],
     "adds": {"wr1": 1, "rb2": 2}, "drops": {"wr1": 2, "rb2": 1},
     "draft_picks": ["2,2027,6,1,2"], "waiver_budget": None},
    {"transaction_id": "t2", "type": "waiver", "status": "complete", "leg": 2,
     "created": 1757272204863, "roster_ids": [1],
     "adds": {"wr3": 1}, "drops": {"k1": 1}, "draft_picks": None,
     "waiver_budget": None},
]

STATS = {
    "wr1": {"rec_tgt": 30.0, "rec": 20.0, "rec_yd": 250.0, "off_snp": 200.0,
            "tm_off_snp": 300.0, "pts_half_ppr": 60.0, "gp": 4.0,
            "rush_att": 0.0, "rec_rz_tgt": 3.0},
    "wr2": {"rec_tgt": 20.0, "rec": 12.0, "rec_yd": 150.0, "off_snp": 150.0,
            "tm_off_snp": 300.0, "pts_half_ppr": 40.0, "gp": 4.0},
    "rb1": {"rush_att": 60.0, "rec_tgt": 10.0, "off_snp": 180.0,
            "tm_off_snp": 300.0, "pts_half_ppr": 55.0, "gp": 4.0},
    # THE AGGREGATE ROW. Not a player; the denominator for every share.
    "TEAM_AAA": {"rec_tgt": 100.0, "rush_att": 80.0, "gp": 4.0},
    "TEAM_BBB": {"rec_tgt": 90.0, "rush_att": 70.0, "gp": 4.0},
}


def _stat_rows(week):
    out = []
    for pid, st in STATS.items():
        team = "AAA" if pid.endswith("AAA") or pid in ("wr1", "rb1") else "BBB"
        out.append({"player_id": pid, "team": team, "opponent": "BBB",
                    "week": week, "stats": st})
    return out


class Unfixtured(RuntimeError):
    """Something asked for a response this fake does not have.

    Raised rather than returning an empty list, so a tool reaching for
    unfixtured data fails here instead of being handed a plausible nothing.
    """


async def rest(path: str):
    if path == "/players/nfl":
        return PLAYERS
    if re.fullmatch(r"/league/\d+", path):
        return PREV_CFG if path.endswith(PREV_LEAGUE) else LEAGUE_CFG
    if re.fullmatch(r"/league/\d+/rosters", path):
        return ROSTERS
    if re.fullmatch(r"/league/\d+/users", path):
        return USERS
    if m := re.fullmatch(r"/league/\d+/matchups/(\d+)", path):
        wk = int(m.group(1))
        return [{"roster_id": 1, "matchup_id": 1,
                 "points": 100.0 if wk < 5 else 0,
                 "starters": ["qb1", "rb1"]},
                {"roster_id": 2, "matchup_id": 1,
                 "points": 90.0 if wk < 5 else 0,
                 "starters": ["rb2", "wr2"]}]
    if re.fullmatch(r"/league/\d+/transactions/\d+", path):
        return TRANSACTIONS
    if re.fullmatch(r"/league/\d+/(winners|losers)_bracket", path):
        return [{"m": 1, "r": 1, "t1": 1, "t2": 2, "w": 1, "l": 2, "p": 1}]
    if re.fullmatch(r"/draft/\w+/picks", path):
        return DRAFT_PICKS
    if re.fullmatch(r"/draft/\w+", path):
        return {"draft_id": "d1", "type": "snake", "status": "complete",
                "season": "2026", "settings": {"rounds": 2}}
    if re.fullmatch(r"/scores/nfl/regular/\d+/\d+", path):
        # Teams and scores sit in `metadata`; `status` is top level. Observed
        # values are "complete" and "pre_game". g2 is deliberately unfinished,
        # because a game in progress must stay pending rather than be scored
        # from a partial lead.
        return [
            {"game_id": "g1", "status": "complete",
             "metadata": {"away_team": "AAA", "home_team": "BBB",
                          "away_score": 24, "home_score": 17}},
            {"game_id": "g2", "status": "pre_game",
             "metadata": {"away_team": "CCC", "home_team": "AAA",
                          "away_score": None, "home_score": None}},
        ]
    if path == "/state/nfl":
        return {"week": 5, "season": "2026", "season_type": "regular"}
    if re.fullmatch(r"/user/\w+", path):
        return {"user_id": "u1", "display_name": "Alder"}
    if re.fullmatch(r"/user/\w+/leagues/nfl/\d+", path):
        return [LEAGUE_CFG]
    raise Unfixtured(f"REST {path}")


async def gql(query: str, variables=None, auth: bool = False):
    q = " ".join(query.split())
    if "weekly_stats" in q:
        wk = int(re.search(r"week:(\d+)", q).group(1))
        return {"weekly_stats": _stat_rows(wk)}
    if "season_stats" in q:
        return {"season_stats": [{**r, "week": None} for r in _stat_rows(1)]}
    if "roster_standings" in q:
        rnd = int(re.search(r"round:(\d+)", q).group(1))
        return {"roster_standings": [
            {"roster_id": 1, "rank": 1, "wins": rnd, "losses": 0, "ties": 0,
             "points": 100.0 * rnd, "record": "W" * rnd},
            {"roster_id": 2, "rank": 2, "wins": 0, "losses": rnd, "ties": 0,
             "points": 90.0 * rnd, "record": "L" * rnd}]}
    if "league_transactions_by_player" in q:
        return {"league_transactions_by_player": TRANSACTIONS}
    if "league_transactions_filtered" in q:
        return {"league_transactions_filtered": TRANSACTIONS}
    if "roster_draft_picks" in q:
        return {"roster_draft_picks": [
            {"season": "2027", "round": 6, "roster_id": 2, "owner_id": 1,
             "previous_owner_id": 2}]}
    if "matchup_legs" in q:
        return {"matchup_legs": [
            {"roster_id": 1, "matchup_id": 1, "proj_points": 101.0,
             "starters": ["qb1", "rb1"]},
            {"roster_id": 2, "matchup_id": 1, "proj_points": 88.0,
             "starters": ["rb2", "wr2"]}]}
    if "get_player_news" in q:
        # metadata is a DICT, verified against the live endpoint — the first
        # draft of this fixture guessed a JSON string and the tool raised
        # AttributeError, which is the fake being wrong rather than the tool.
        return {"get_player_news": [
            {"source": "fake wire", "published": 1757272204863,
             "metadata": {"title": "A thing happened",
                          "description": "He did a thing.",
                          "analysis": "It may matter.", "topic_id": "1"}}]}
    if "stats_for_players_in_week" in q:
        return {"stats_for_players_in_week": [
            {"player_id": "wr3", "stats": {"rec": 4, "rec_yd": 50,
                                           "rec_td": 1}}]}
    if "trending_players" in q:
        return {"trending_players": [{"player_id": "wr3", "count": 900}]}
    if "get_pickem_legs" in q:
        return {"get_pickem_legs": [
            {"leg_id": "v1:regular:5", "status": "open",
             "num_expected_picks": 2,
             "picks": {"g1": {"team": "AAA", "outcome": "win"}},
             "tiebreaker": 40}]}
    if "get_pickem_picks_for_league" in q:
        return {"get_pickem_picks_for_league": {
            "1": {"picks": {"g1": {"team": "AAA", "outcome": "win"},
                            "g2": {"team": "BBB", "outcome": "win"}},
                  "tiebreaker": 40},
            "2": {"picks": {"g1": {"team": "CCC", "outcome": "win"},
                            "g2": {"team": "BBB", "outcome": "win"}},
                  "tiebreaker": 44},
            "3": {"picks": {}, "tiebreaker": None}}}
    if "get_pickem_scoring_settings" in q:
        return {"get_pickem_scoring_settings": {"v1:regular:5": 1.0}}
    if "matchup_messages" in q or "messages" in q:
        return {"messages": []}
    if "watch_list" in q or "my_watch" in q:
        return {"watch_list": []}
    if "{ me {" in q or "me {" in q:
        return {"me": {"user_id": "u1", "display_name": "Alder"}}
    raise Unfixtured(f"GQL {q[:90]}")


# Tool modules do `from .client import gql, rest`, so each holds its OWN
# reference and patching client alone reaches none of them. Every module that
# imported a transport has to be patched by name.
TOOL_MODULES = ("client", "discovery", "drafts", "keepers", "lineups",
                "playoffs", "reads", "signals", "usage", "writes")


def install(monkeypatch):
    """Point every module's transport at this fake, for one test."""
    import importlib

    from sleeper_mcp import client
    for name in TOOL_MODULES:
        mod = importlib.import_module(f"sleeper_mcp.{name}")
        for attr, repl in (("rest", rest), ("gql", gql)):
            if hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, repl)
    monkeypatch.setattr(client, "DEFAULT_LEAGUE", LEAGUE)
    monkeypatch.setattr(client, "DEFAULT_ROSTER", ROSTER)
    monkeypatch.setattr(client, "DEFAULT_PICKEM_LEAGUE", PICKEM_LEAGUE)
    monkeypatch.setattr(client, "DEFAULT_PICKEM_ROSTER", PICKEM_ROSTER)
    monkeypatch.setattr(client, "TOKEN", "fake.fake.fake")
    monkeypatch.setattr(client, "WRITES_ENABLED", False)
    client.cache_clear()
