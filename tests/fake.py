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

# The entry's week-5 tiebreaker is a MAP, as get_pickem_legs returns it — this
# fixture once said a bare 40, which `pickem_status` cannot render. Mutable,
# so `set_pickem_tiebreaker` can be verified by reading it back; `install`
# resets it for every test.
TIEBREAKER_DEFAULT = {"type": "total_points", "value": 40, "game_id": "g2",
                      "updated_at": 1757272204863}
TIEBREAKER: dict = {}

# The entry's week-5 picks, mutable for the same reason: `make_pickem_pick` is
# verified by reading the pick back. `LAST_PICK` records the mutation's
# variables so a test can see what was actually sent (pick_to_replace, leg).
PICKS_DEFAULT = {"g1": {"team": "AAA", "outcome": "win"}}
PICKS: dict = {}
LAST_PICK: dict = {}

# Every GraphQL request a tool sent, as (query, variables). conftest reads it
# after each test and fails the test on what `problems` finds.
SENT: list = []

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
    # TEAM DEFENCES HAVE NO full_name IN SLEEPER'S DICTIONARY — only first_name
    # ("Minnesota") and last_name ("Vikings"). This fixture once carried a
    # full_name, which made every name search look fine here and fail on real
    # data: no defence could be found by name, so waiver_claim could never add
    # one. Keep these shaped like the real thing. "BBB" is a FREE defence.
    "AAA": {"first_name": "Anytown", "last_name": "Aardvarks",
            "position": "DEF", "team": "AAA"},
    "BBB": {"first_name": "Bayside", "last_name": "Badgers",
            "position": "DEF", "team": "BBB"},
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

# YOUR OWN PENDING WAIVER CLAIM. Only authenticated GraphQL
# `league_transactions` returns it; the public REST feed omits it, so it is
# deliberately NOT in TRANSACTIONS. Shaped like the real row of 2026-10-23:
# no draft_picks, no waiver_budget, and the bid inside `settings`.
PENDING_CLAIM = {"transaction_id": "w9", "type": "waiver", "status": "pending",
                 "leg": 5, "created": 1757272300000, "roster_ids": [1],
                 "adds": {"wr3": 1}, "drops": {"k1": 1},
                 "settings": {"waiver_bid": 1}}

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


def _needs_token(op: str, auth: bool) -> None:
    # What Sleeper sends a tokenless call to an authenticated operation: a
    # GraphQL error reading "Unauthorized", which client.gql raises as this.
    if not auth:
        raise RuntimeError(f"{op}: Unauthorized")


# THE ONLY STRINGS A QUERY'S TEXT MAY HOLD. Every other value travels in the
# variables. A value spliced into the text can change the query itself: a
# league id of `1"){...} x:matchup_legs(league_id:"1` adds fields to a query
# that runs with the user's token. Real Sleeper runs a spliced query, so only
# a test can refuse one.
LITERALS = {"nfl", "regular", "proj"}

# How Sleeper coerces a variable. Snowflake is sent as a string, as the
# writes that work live send it.
_SCALARS = {"Int": int, "String": str, "Snowflake": str}


def _fits(kind: str, value) -> bool:
    kind = kind.rstrip("!")
    if value is None:
        return True
    if kind.startswith("["):
        return isinstance(value, list) and all(_fits(kind[1:-1], v)
                                               for v in value)
    want = _SCALARS.get(kind)
    return want is None or (isinstance(value, want)
                            and not isinstance(value, bool))


def problems(query: str, variables: dict | None) -> list[str]:
    """What is wrong with one request: a string spliced into the text, or a
    variable that Sleeper refuses (declared and unused, used and undeclared,
    sent and undeclared, required and missing, or of the wrong type).

    Numbers in the text are not checked. A %d cannot carry a quote.
    """
    variables = variables or {}
    out = [f"spliced {s!r}" for s in
           sorted(set(re.findall(r'"([^"]*)"', query)) - LITERALS)]
    head = re.match(r"\s*(?:query|mutation)\s*\(([^)]*)\)", query)
    declared = (dict(re.findall(r"\$(\w+)\s*:\s*([\w\[\]!]+)", head.group(1)))
                if head else {})
    used = set(re.findall(r"\$(\w+)", query[head.end():] if head else query))
    out += [f"${v} declared, never used" for v in sorted(set(declared) - used)]
    out += [f"${v} used, never declared" for v in sorted(used - set(declared))]
    out += [f"${v} sent, never declared"
            for v in sorted(set(variables) - set(declared))]
    for v, kind in declared.items():
        if kind.endswith("!") and variables.get(v) is None:
            out.append(f"${v} is {kind} and has no value")
        elif not _fits(kind, variables.get(v)):
            out.append(f"${v} is {kind}, got {variables[v]!r}")
    # An id argument gets an id this fake knows, so a variable bound to the
    # wrong value (a player id as the league) is caught.
    known = {"league_id": {LEAGUE, PICKEM_LEAGUE, PREV_LEAGUE},
             "parent_id": {LEAGUE}, "player_id": set(PLAYERS),
             "player_ids": set(PLAYERS)}
    for arg, v in re.findall(r"\b(\w+):\$(\w+)", query):
        got = variables.get(v)
        for g in (got if isinstance(got, list) else [got]):
            if arg in known and g is not None and g not in known[arg]:
                out.append(f"{arg} is {g!r}, which is not a known id")
    return out


def arg(query: str, variables: dict, name: str):
    """The value of argument `name`, read through the variable bound to it."""
    return variables[re.search(rf"\b{name}:\$(\w+)", query).group(1)]


async def gql(query: str, variables=None, auth: bool = False):
    q = " ".join(query.split())
    SENT.append((q, variables))
    if "weekly_stats" in q:
        return {"weekly_stats": _stat_rows(arg(q, variables, "week"))}
    if "season_stats" in q:
        return {"season_stats": [{**r, "week": None} for r in _stat_rows(1)]}
    if "roster_standings" in q:
        rnd = arg(q, variables, "round")
        return {"roster_standings": [
            {"roster_id": 1, "rank": 1, "wins": rnd, "losses": 0, "ties": 0,
             "points": 100.0 * rnd, "record": "W" * rnd},
            {"roster_id": 2, "rank": 2, "wins": 0, "losses": rnd, "ties": 0,
             "points": 90.0 * rnd, "record": "L" * rnd}]}
    if "league_transactions_by_player" in q:
        return {"league_transactions_by_player": TRANSACTIONS}
    if "league_transactions_filtered" in q:
        return {"league_transactions_filtered": TRANSACTIONS}
    if "league_transactions(" in q:
        _needs_token("league_transactions", auth)
        return {"league_transactions": TRANSACTIONS + [PENDING_CLAIM]}
    if "roster_draft_picks_by_owner" in q:
        return {"roster_draft_picks_by_owner": [
            {"season": "2027", "round": 6, "roster_id": 2}]}
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
    if "get_player_outlook" in q:
        return {"get_player_outlook": {
            "source": "fake wire", "published": 1757272204863,
            "metadata": {"title": "Season outlook",
                         "analysis": "He should see the field."}}}
    if "stats_for_players_in_week" in q:
        return {"stats_for_players_in_week": [
            {"player_id": "wr3", "stats": {"rec": 4, "rec_yd": 50,
                                           "rec_td": 1}}]}
    if "trending_players" in q:
        return {"trending_players": [{"player_id": "wr3", "count": 900}]}
    if "scores(sport" in q:
        # The GraphQL scoreboard carries the same games as the REST one.
        board = await rest("/scores/nfl/regular/2026/5")
        return {"scores": [{"game_id": g["game_id"], "metadata": g["metadata"]}
                           for g in board]}
    if "update_matchup_leg" in q:
        _needs_token("update_matchup_leg", auth)
        return {"update_matchup_leg": {"roster_id": variables["rid"],
                                       "starters": variables["s"]}}
    if "_league_player_trade_block" in q:
        op = re.search(r"\w+_league_player_trade_block", q).group(0)
        _needs_token(op, auth)
        return {op: {"roster_id": ROSTER}}
    # Not stateful: the watchlist holds wr3 whatever is sent, so a test
    # watches wr3 or unwatches someone else.
    if "watch_player" in q:
        _needs_token("watch_player", auth)
        return ({"unwatch_player": True} if "unwatch_player" in q
                else {"watch_player": {"player_id": "wr3"}})
    if "watched_players" in q:
        _needs_token("watched_players", auth)
        return {"watched_players": [{"player_id": "wr3"}]}
    if "set_pickem_tiebreaker" in q:
        _needs_token("set_pickem_tiebreaker", auth)
        TIEBREAKER.clear()
        TIEBREAKER.update({**variables["tb"], "updated_at": 1757272300000})
        return {"set_pickem_tiebreaker": {"leg_id": variables["leg"]}}
    if "make_pickem_pick" in q:
        _needs_token("make_pickem_pick", auth)
        LAST_PICK.clear()
        LAST_PICK.update(variables)
        p = variables["p"]
        PICKS[p["game_id"]] = {"team": p["team"], "outcome": p["outcome"]}
        return {"make_pickem_pick": {"leg_id": variables["leg"]}}
    if "get_pickem_legs" in q:
        # Pick'em reads are authenticated, and this returns EVERY week's leg
        # with the earliest first — so a tool that takes legs[0] reads last
        # week, and the week-4 leg below is here to make that visible.
        _needs_token("get_pickem_legs", auth)
        return {"get_pickem_legs": [
            {"leg_id": "v1:regular:4", "status": "complete",
             "num_expected_picks": 2,
             "picks": {"g9": {"team": "CCC", "outcome": "win"}},
             "tiebreaker": {"type": "total_points", "value": 33,
                            "game_id": "g9", "updated_at": 1756667404863}},
            {"leg_id": "v1:regular:5", "status": "open",
             "num_expected_picks": 2,
             "picks": {g: dict(p) for g, p in PICKS.items()},
             "tiebreaker": dict(TIEBREAKER)}]}
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
    TIEBREAKER.clear()
    TIEBREAKER.update(TIEBREAKER_DEFAULT)
    PICKS.clear()
    PICKS.update({g: dict(p) for g, p in PICKS_DEFAULT.items()})
    LAST_PICK.clear()
    client.cache_clear()
