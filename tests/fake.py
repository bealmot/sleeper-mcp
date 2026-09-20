"""A fake Sleeper, so the tool layer can be tested without a network.

WHY THIS IS WORTH THE TROUBLE. The pure modules here are near 100% covered and
the tool modules were at 0%, which is not an accident of laziness: a tool
fetches, joins and formats, and none of that can run without responses. So the
bugs that reached users were all in that layer — a format string on a None, a
synthetic row treated as a player, an endpoint that turned out to need a token.

The fixtures below are SHAPED LIKE REAL RESPONSES, including the parts nobody
would invent: the TEAM_<abbr> aggregate row that sits among the players, a
retired duplicate with no team, a team defence with NO full_name (its id is the
team code and it carries first_name/last_name), a player whose `position` is
DB but whose `fantasy_positions` include WR, `is_keeper` on a draft pick, a
traded draft pick encoded as a comma-separated string over GraphQL and as an
object over REST, a trade that lists each player in both adds and drops, a
pending waiver claim that the public REST feed does not carry at all, and a
pick'em tiebreaker that is a Map rather than a number. Every one of those
shipped a bug because a hand-written fixture omitted it.

THREE THINGS THIS FAKE ENFORCES that a real Sleeper enforces silently:

  * AUTHENTICATION. Queries Sleeper refuses without a token (and every
    mutation) raise AuthError here when sent with auth=False. A tool missing
    auth=True used to pass every test and fail live with a bare "Unauthorized".
  * THE REAL-MONEY BOUNDARY. gql() and rest() run boundaries.check() first, so
    a production query template that trips the guard fails in the suite.
  * OPERATION ROUTING BY NAME. Responses are chosen by the top-level operation
    name, not by substring — "roster_draft_picks" must not answer a query for
    roster_draft_picks_by_owner with a plausible nothing.

MUTATIONS ARE STATEFUL. A write updates STATE and the corresponding read-back
reflects it, so the verify path of every write tool can run. Every mutation is
also recorded in SENT so a test can pin the exact variables a tool sends —
the k_/v_ arrays, the waiver_budget encoding — which is the part that reaches
a real person's league and cannot be tested live.

It is deliberately not a complete Sleeper. Anything unrecognised raises, so a
tool reaching for something unfixtured fails loudly here rather than being
handed a plausible empty list.
"""

from __future__ import annotations

import json
import re

LEAGUE = "111"
PICKEM_LEAGUE = "222"
PICKEM_ROSTER = 1
PREV_LEAGUE = "110"
ROSTER = 1
SEASON = "2026"
WEEK = 5

PLAYERS = {
    "qb1": {"full_name": "Ant Quarterback", "first_name": "Ant",
            "last_name": "Quarterback", "position": "QB",
            "fantasy_positions": ["QB"], "team": "AAA", "active": True},
    "qb2": {"full_name": "Owl Quarterback", "first_name": "Owl",
            "last_name": "Quarterback", "position": "QB",
            "fantasy_positions": ["QB"], "team": "CCC", "active": True},
    "rb1": {"full_name": "Bee Runner", "first_name": "Bee", "last_name": "Runner",
            "position": "RB", "fantasy_positions": ["RB"], "team": "AAA",
            "active": True},
    "rb2": {"full_name": "Cat Runner", "first_name": "Cat", "last_name": "Runner",
            "position": "RB", "fantasy_positions": ["RB"], "team": "BBB",
            "active": True},
    "rb3": {"full_name": "Jay Runner", "first_name": "Jay", "last_name": "Runner",
            "position": "RB", "fantasy_positions": ["RB"], "team": "CCC",
            "active": True},
    "wr1": {"full_name": "Doe Catcher", "first_name": "Doe", "last_name": "Catcher",
            "position": "WR", "fantasy_positions": ["WR"], "team": "AAA",
            "active": True, "news_updated": 1758000000000},
    "wr2": {"full_name": "Elk Catcher", "first_name": "Elk", "last_name": "Catcher",
            "position": "WR", "fantasy_positions": ["WR"], "team": "BBB",
            "active": True},
    "wr3": {"full_name": "Fox Catcher", "first_name": "Fox", "last_name": "Catcher",
            "position": "WR", "fantasy_positions": ["WR"], "team": "CCC",
            "active": True},
    # On IR: appears in BOTH players and reserve on the roster below.
    "wr_ir": {"full_name": "Moth Catcher", "first_name": "Moth",
              "last_name": "Catcher", "position": "WR",
              "fantasy_positions": ["WR"], "team": "AAA", "active": True,
              "injury_status": "IR"},
    # Active but currently unsigned: team is None, like a player cut this week.
    "wr_fa": {"full_name": "Newt Catcher", "first_name": "Newt",
              "last_name": "Catcher", "position": "WR",
              "fantasy_positions": ["WR"], "team": None, "active": True},
    "te1": {"full_name": "Gnu Tight", "first_name": "Gnu", "last_name": "Tight",
            "position": "TE", "fantasy_positions": ["TE"], "team": "AAA",
            "active": True},
    "k1": {"full_name": "Hen Kicker", "first_name": "Hen", "last_name": "Kicker",
           "position": "K", "fantasy_positions": ["K"], "team": "BBB",
           "active": True},
    # A two-way player: listed at DB, eligible at WR. The Travis Hunter shape.
    "db1": {"full_name": "Kit Twoway", "first_name": "Kit", "last_name": "Twoway",
            "position": "DB", "fantasy_positions": ["DB", "WR"], "team": "CCC",
            "active": True},
    # A fullback: position FB, fantasy_positions RB.
    "fb1": {"full_name": "Lox Fullback", "first_name": "Lox",
            "last_name": "Fullback", "position": "FB",
            "fantasy_positions": ["RB"], "team": "BBB", "active": True},
    # TEAM DEFENCES HAVE NO full_name. The id is the team code; the city and
    # nickname sit in first_name/last_name. Real: DET = Detroit / Lions.
    "AAA": {"first_name": "Aard", "last_name": "Varks", "position": "DEF",
            "fantasy_positions": ["DEF"], "team": "AAA", "active": True},
    "BBB": {"first_name": "Bumble", "last_name": "Bees", "position": "DEF",
            "fantasy_positions": ["DEF"], "team": "BBB", "active": True},
    # A retired duplicate with no team — the Kenneth Walker shape.
    "rb1_old": {"full_name": "Bee Runner", "first_name": "Bee",
                "last_name": "Runner", "position": "RB",
                "fantasy_positions": ["RB"], "team": None, "active": False},
    # A coach, who must never be matchable.
    "hc1": {"full_name": "Ibis Coach", "first_name": "Ibis", "last_name": "Coach",
            "position": "HC", "fantasy_positions": None, "team": "AAA"},
}

# Roster 1 is "me". IR IS A SUBSET OF THE ROSTER: wr_ir is in both lists.
ROSTERS = [
    {"roster_id": 1, "owner_id": "u1", "co_owners": None,
     "players": ["qb1", "rb1", "wr1", "te1", "k1", "AAA", "rb3", "wr_ir", "db1"],
     "starters": ["qb1", "rb1", "wr1", "te1", "rb3", "k1", "AAA"],
     "reserve": ["wr_ir"], "taxi": None, "keepers": ["rb1"],
     "settings": {"wins": 3, "losses": 1, "ties": 0, "fpts": 400,
                  "fpts_decimal": 50, "fpts_against": 380,
                  "fpts_against_decimal": 0, "waiver_budget_used": 11}},
    {"roster_id": 2, "owner_id": "u2", "co_owners": ["u3"],
     "players": ["rb2", "wr2", "fb1"], "starters": ["rb2", "wr2"],
     "reserve": [], "taxi": None, "keepers": None,
     "settings": {"wins": 1, "losses": 3, "ties": 0, "fpts": 350,
                  "fpts_decimal": 0, "fpts_against": 400,
                  "fpts_against_decimal": 0, "waiver_budget_used": 40}},
]

USERS = [{"user_id": "u1", "display_name": "Alder"},
         {"user_id": "u2", "display_name": "Birch"}]

# Scoring mirrors a real league where the per-reception bonus is keyed PER
# POSITION (bonus_rec_rb/wr/te) while the league-level `rec` is 0 — a shape a
# naive "rec" reader calls zero-PPR.
LEAGUE_CFG = {
    "league_id": LEAGUE, "name": "Fake League", "season": SEASON,
    "season_type": "regular",
    "status": "in_season", "total_rosters": 2, "draft_id": "d1",
    "previous_league_id": PREV_LEAGUE,
    "roster_positions": ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF", "BN", "IR"],
    "scoring_settings": {"rec": 0.0, "bonus_rec_rb": 0.5, "bonus_rec_wr": 0.5,
                         "bonus_rec_te": 0.5, "rec_fd": 0.5, "rush_fd": 0.5,
                         "pass_fd": 0.25, "pass_td": 4, "rush_td": 6,
                         "rec_td": 6, "pass_yd": 0.04, "rush_yd": 0.1,
                         "rec_yd": 0.1, "fgm": 3, "xpm": 1, "def_td": 6,
                         "sack": 1, "pts_allow_0": 10, "pts_allow_35p": -4},
    "settings": {"type": 1, "playoff_week_start": 15, "playoff_teams": 2,
                 "playoff_round_type": 0, "max_keepers": 1, "waiver_type": 2,
                 "waiver_budget": 100, "waiver_clear_days": 2,
                 "waiver_day_of_week": 2, "trade_review_days": 1,
                 "trade_deadline": 11, "disable_trades": 0,
                 "reserve_slots": 1, "reserve_allow_out": 1,
                 "reserve_allow_ir": 1, "reserve_allow_doubtful": 0,
                 "taxi_slots": 0, "best_ball": 0, "league_average_match": 0,
                 "leg": WEEK, "last_scored_leg": WEEK - 1},
    "metadata": {"latest_cloned_to": "113"},
}
PREV_CFG = {**LEAGUE_CFG, "league_id": PREV_LEAGUE, "season": "2025",
            "status": "complete", "draft_id": "d0",
            "previous_league_id": None, "metadata": None,
            "settings": {**LEAGUE_CFG["settings"], "leg": 17,
                         "last_scored_leg": 17}}
# A clone of the league (Sleeper's guillotine/"chopped" format): no playoffs,
# trades disabled, a big FAAB budget.
CLONE_CFG = {**LEAGUE_CFG, "league_id": "113", "name": "Fake League",
             "previous_league_id": None,
             "metadata": {"cloned_from": LEAGUE},
             "settings": {**LEAGUE_CFG["settings"], "type": 3,
                          "playoff_week_start": 0, "playoff_teams": None,
                          "disable_trades": 1, "waiver_budget": 1000}}

PICKEM_CFG = {"league_id": PICKEM_LEAGUE, "name": "Fake Pool",
              "sport": "pickem:nfl", "season": SEASON, "status": "in_season",
              "total_rosters": 3, "roster_positions": None,
              "settings": {"pickem_type": 1, "num_teams": 3},
              "scoring_settings": {"v1:regular:4": 1.0, "v1:regular:5": 1.0}}
# The pool's own scoring sits in roster metadata as a JSON STRING.
PICKEM_ROSTERS = [
    {"roster_id": 1, "owner_id": "u1",
     "metadata": {"points_by_leg": json.dumps({"v1:regular:4": 1.0})}},
    {"roster_id": 2, "owner_id": "u2",
     "metadata": {"points_by_leg": json.dumps({"v1:regular:4": 2.0})}},
    {"roster_id": 3, "owner_id": "u4", "metadata": {}},
]

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
# rosters — the shape that once printed each player twice. REST carries the
# traded pick as an OBJECT; GraphQL carries the same pick as a STRING.
_TRADE = {"transaction_id": "t1", "type": "trade", "status": "complete",
          "leg": 3, "created": 1758644097060, "roster_ids": [1, 2],
          "adds": {"wr1": 1, "rb2": 2}, "drops": {"wr1": 2, "rb2": 1},
          "waiver_budget": [{"sender": 2, "receiver": 1, "amount": 5}],
          "settings": None, "creator": "u1", "consenter_ids": [1, 2]}
_WAIVER = {"transaction_id": "t2", "type": "waiver", "status": "complete",
           "leg": 2, "created": 1757272204863, "roster_ids": [1],
           "adds": {"wr3": 1}, "drops": {"k1": 1}, "draft_picks": None,
           "waiver_budget": None, "settings": {"waiver_bid": 7, "seq": 1},
           "creator": "u1", "consenter_ids": [1]}
REST_TRANSACTIONS = [
    {**_TRADE, "draft_picks": [{"roster_id": 2, "season": "2027", "round": 6,
                                "owner_id": 1, "previous_owner_id": 2}]},
    _WAIVER,
]
# GraphQL: strings for picks, `league_id` per row, and the rows REST hides —
# a cancelled trade and the caller's own PENDING waiver claim.
GQL_TRANSACTIONS = [
    {**_TRADE, "league_id": LEAGUE, "draft_picks": ["2,2027,6,1,2"]},
    {**_WAIVER, "league_id": LEAGUE},
    {"transaction_id": "t3", "type": "waiver", "status": "pending", "leg": WEEK,
     "created": 1758900000000, "roster_ids": [1], "league_id": LEAGUE,
     "adds": {"wr3": 1}, "drops": {"rb3": 1}, "draft_picks": None,
     "waiver_budget": None, "settings": {"waiver_bid": 12, "seq": 3},
     "creator": "u1", "consenter_ids": [1]},
    {"transaction_id": "t4", "type": "trade", "status": "cancelled", "leg": 4,
     "created": 1758800000000, "roster_ids": [1, 2], "league_id": LEAGUE,
     "adds": {"te1": 2, "wr2": 1}, "drops": {"te1": 1, "wr2": 2},
     "draft_picks": [], "waiver_budget": None, "settings": None,
     "creator": "u2", "consenter_ids": [2]},
    # A previous-season row: roster 1 was somebody else then.
    {"transaction_id": "t0", "type": "draft_pick", "status": "complete",
     "leg": 1, "created": 1725000000000, "roster_ids": [1],
     "league_id": PREV_LEAGUE, "adds": {"wr1": 1}, "drops": None,
     "draft_picks": [], "waiver_budget": None, "settings": None,
     "creator": "u9", "consenter_ids": [1]},
]
TRANSACTIONS = REST_TRANSACTIONS      # the name older tests import

PREV_USERS = [{"user_id": "u9", "display_name": "Yew"},
              {"user_id": "u2", "display_name": "Birch"}]
PREV_ROSTERS = [{"roster_id": 1, "owner_id": "u9", "players": ["wr1"],
                 "reserve": [], "keepers": None,
                 "settings": {"wins": 8, "losses": 6, "ties": 0, "fpts": 1500,
                              "fpts_decimal": 0, "fpts_against": 1400,
                              "fpts_against_decimal": 0}},
                {"roster_id": 2, "owner_id": "u2", "players": ["wr2"],
                 "reserve": [], "keepers": None,
                 "settings": {"wins": 6, "losses": 8, "ties": 0, "fpts": 1400,
                              "fpts_decimal": 0, "fpts_against": 1500,
                              "fpts_against_decimal": 0}}]

# Season totals per player. Weekly rows are DERIVED from these so that usage
# varies by week: wr1 is a riser, wr2 a faller, and wr3 carries targets but no
# snap keys at all (Sleeper's late-season rows often lack them).
STATS = {
    "wr1": {"rec_tgt": 30.0, "rec": 20.0, "rec_yd": 250.0, "off_snp": 200.0,
            "tm_off_snp": 300.0, "pts_half_ppr": 60.0, "gp": 4.0,
            "rush_att": 0.0, "rec_rz_tgt": 3.0, "rec_fd": 8.0,
            "bonus_rec_wr": 20.0},
    "wr2": {"rec_tgt": 20.0, "rec": 12.0, "rec_yd": 150.0, "off_snp": 150.0,
            "tm_off_snp": 300.0, "pts_half_ppr": 40.0, "gp": 4.0,
            "bonus_rec_wr": 12.0},
    "wr3": {"rec_tgt": 16.0, "rec": 10.0, "rec_yd": 120.0, "pts_half_ppr": 30.0,
            "gp": 4.0},
    "rb1": {"rush_att": 60.0, "rec_tgt": 10.0, "off_snp": 180.0,
            "tm_off_snp": 300.0, "pts_half_ppr": 55.0, "gp": 4.0,
            "rush_yd": 280.0, "rush_fd": 14.0},
    # THE AGGREGATE ROW. Not a player; the denominator for every share.
    "TEAM_AAA": {"rec_tgt": 100.0, "rush_att": 80.0, "gp": 4.0,
                 "off_snp": 300.0, "tm_off_snp": 300.0},
    "TEAM_BBB": {"rec_tgt": 90.0, "rush_att": 70.0, "gp": 4.0},
    "TEAM_CCC": {"rec_tgt": 80.0, "rush_att": 60.0, "gp": 4.0},
}
_TEAM_OF = {"wr1": "AAA", "rb1": "AAA", "wr2": "BBB", "wr3": "CCC",
            "TEAM_AAA": "AAA", "TEAM_BBB": "BBB", "TEAM_CCC": "CCC"}


def _stat_rows(week):
    """One week's rows. Player shares move with the week; team rows do not."""
    out = []
    for pid, st in STATS.items():
        team = _TEAM_OF[pid]
        weekly = {k: (v if k in ("gp", "tm_off_snp") else v / 4) for k, v in st.items()}
        weekly["gp"] = 1.0
        if pid == "wr1":                       # role grows: 5 -> 6 -> 7 -> 8 targets
            weekly["rec_tgt"] = 4.0 + week
            weekly["off_snp"] = 40.0 + 5 * week
        elif pid == "wr2":                     # role shrinks
            weekly["rec_tgt"] = max(1.0, 9.0 - week)
        out.append({"player_id": pid, "team": team, "opponent": "BBB",
                    "week": week, "stats": weekly})
    return out


# Projection COMPONENTS per player per week, so scored() has something real to
# dot against scoring_settings. A team on a bye has no row at all.
PROJ = {
    "qb1": {"pass_yd": 250, "pass_td": 2, "pass_fd": 12},          # 10+8+3 = 21
    "qb2": {"pass_yd": 300, "pass_td": 3, "pass_fd": 14},          # 12+12+3.5 = 27.5
    "rb1": {"rush_yd": 80, "rush_td": 1, "rush_fd": 4, "rec": 2,
            "bonus_rec_rb": 2},                                    # 8+6+2+1 = 17
    "rb2": {"rush_yd": 50, "rush_fd": 3},                          # 5+1.5 = 6.5
    "rb3": {"rush_yd": 30, "rush_fd": 1, "rec": 2, "bonus_rec_rb": 2},  # 3+.5+1 = 4.5
    "wr1": {"rec": 6, "bonus_rec_wr": 6, "rec_yd": 80, "rec_fd": 4},   # 3+8+2 = 13
    "wr2": {"rec": 4, "bonus_rec_wr": 4, "rec_yd": 50, "rec_fd": 2},   # 2+5+1 = 8
    "wr3": {"rec": 5, "bonus_rec_wr": 5, "rec_yd": 70, "rec_fd": 3},   # 2.5+7+1.5 = 11
    "db1": {"rec": 3, "bonus_rec_wr": 3, "rec_yd": 40, "rec_fd": 2},   # 1.5+4+1 = 6.5
    "fb1": {"rush_yd": 5},                                         # 0.5
    "te1": {"rec": 3, "bonus_rec_te": 3, "rec_yd": 30, "rec_fd": 1},   # 1.5+3+.5 = 5
    "k1": {"fgm": 2, "xpm": 3},                                    # 9
    "AAA": {"sack": 3, "def_td": 0, "pts_allow_0": 0},             # 3
    "BBB": {"sack": 2},                                            # 2
    "wr_ir": None,                                                 # no projection
}
# Which teams play in which week. Week 6: CCC on bye. Week 7: AAA plays but
# wr1 has no projection row (the UNKNOWN case).
BYES = {6: {"CCC"}}
MISSING = {7: {"wr1"}}


def _proj_rows(week, ids):
    rows = []
    for pid in ids:
        v = PLAYERS.get(pid) or {}
        st = PROJ.get(pid)
        if st is None or v.get("team") in BYES.get(week, set()) \
                or pid in MISSING.get(week, set()):
            continue
        rows.append({"player_id": pid, "stats": dict(st)})
    return rows


def _games(week):
    # Game 1 is complete through the CURRENT week (a Thursday game is final
    # while the rest of the week is still to play); game 2 has not started.
    done = week <= WEEK
    games = [{"game_id": f"g{week}1", "status": "complete" if done else "pre_game",
              "start_time": 1758400000000 + week * 604800000,
              "metadata": {"away_team": "AAA", "home_team": "BBB",
                           "away_score": 24 if done else None,
                           "home_score": 17 if done else None,
                           "date_time": "2026-09-14T17:00:00Z"}}]
    if "CCC" not in BYES.get(week, set()):
        games.append({"game_id": f"g{week}2", "status": "pre_game",
                      "start_time": 1758400000000 + week * 604800000 + 3600000,
                      "metadata": {"away_team": "CCC", "home_team": "AAA",
                                   "away_score": None, "home_score": None,
                                   "date_time": "2026-09-14T20:00:00Z"}})
    return games


# --- mutable state -------------------------------------------------------------

STATE: dict = {}
SENT: list = []
FAIL: set = set()


def reset():
    STATE.clear()
    STATE.update({
        "starters": {},             # (roster_id, week) -> [ids]
        "reserve": {r["roster_id"]: list(r["reserve"] or []) for r in ROSTERS},
        "keepers": {r["roster_id"]: list(r["keepers"] or []) for r in ROSTERS},
        "otb": {"rb3": 1},          # player_id -> roster that listed him
        "watch": set(),
        "picks": {"v1:regular:4": {"g41": "AAA", "g42": "CCC"},
                  "v1:regular:5": {"g51": "AAA"}},
        "claims": [],               # extra LeagueTransaction rows (pending)
        "status": {},               # transaction_id -> overridden status
    })
    SENT.clear()
    FAIL.clear()


def fail_next(op: str):
    """Make the next call of `op` raise, to exercise a failed verification."""
    FAIL.add(op)


class Unfixtured(RuntimeError):
    """Something asked for a response this fake does not have.

    Raised rather than returning an empty list, so a tool reaching for
    unfixtured data fails here instead of being handed a plausible nothing.
    """


def _policy(op: str):
    from sleeper_mcp.boundaries import check
    check(op)


def _league_players():
    rows = [{"player_id": "wr3", "metadata": None,
             "settings": {"waiver_clears_at": 4102444800}},      # year 2100: ON WAIVERS
            {"player_id": "qb2", "metadata": None,
             "settings": {"last_added": 1758000000}},
            {"player_id": "0", "metadata": None,
             "settings": {"last_substituted_as_starter": 1758000000000}}]
    for pid, rid in STATE["otb"].items():
        rows.append({"player_id": pid, "metadata": None,
                     "settings": {"otb": rid, "otb_added_at": 1758500000000}})
    return rows


def _gql_transactions(q):
    rows = [dict(r) for r in GQL_TRANSACTIONS] + [dict(r) for r in STATE["claims"]]
    for r in rows:
        if r["transaction_id"] in STATE["status"]:
            r["status"] = STATE["status"][r["transaction_id"]]
    m = re.search(r"status_filters:\[([^\]]*)\]", q)
    if m:
        want = set(re.findall(r'"(\w+)"', m.group(1)))
        rows = [r for r in rows if r["status"] in want]
    m = re.search(r"type_filters:\[([^\]]*)\]", q)
    if m:
        want = set(re.findall(r'"(\w+)"', m.group(1)))
        rows = [r for r in rows if r["type"] in want]
    m = re.search(r"leg_filters:\[([^\]]*)\]", q)
    if m:
        want = {int(x) for x in re.findall(r"\d+", m.group(1))}
        rows = [r for r in rows if r["leg"] in want]
    m = re.search(r"roster_id_filters:\[([^\]]*)\]", q)
    if m:
        want = {int(x) for x in re.findall(r"\d+", m.group(1))}
        rows = [r for r in rows if set(r["roster_ids"]) & want]
    return rows


async def rest(path: str):
    _policy(path)
    if path == "/players/nfl":
        return PLAYERS
    if m := re.fullmatch(r"/players/nfl/trending/(add|drop)(\?.*)?", path):
        return [{"player_id": "wr3", "count": 900}, {"player_id": "qb2", "count": 400}]
    if re.fullmatch(r"/league/\d+", path):
        lid = path.rsplit("/", 1)[1]
        return {LEAGUE: LEAGUE_CFG, PREV_LEAGUE: PREV_CFG, "113": CLONE_CFG,
                PICKEM_LEAGUE: PICKEM_CFG}.get(lid) or _raise(path)
    if m := re.fullmatch(r"/league/(\d+)/rosters", path):
        lid = m.group(1)
        if lid == PICKEM_LEAGUE:
            return PICKEM_ROSTERS
        if lid == PREV_LEAGUE:
            return PREV_ROSTERS
        out = []
        for r in ROSTERS:
            r = dict(r)
            r["reserve"] = list(STATE["reserve"].get(r["roster_id"], []))
            r["keepers"] = list(STATE["keepers"].get(r["roster_id"], [])) or None
            out.append(r)
        return out
    if m := re.fullmatch(r"/league/(\d+)/users", path):
        return PREV_USERS if m.group(1) == PREV_LEAGUE else USERS
    if m := re.fullmatch(r"/league/\d+/matchups/(\d+)", path):
        wk = int(m.group(1))
        done = wk < WEEK
        # In the CURRENT week the AAA-BBB game is final (see _games), so the
        # AAA and BBB players have banked points and everyone else sits at 0.
        cur = wk == WEEK
        pp1 = ({"qb1": 21.0, "rb1": 17.0, "wr1": 13.0, "te1": 5.0, "rb3": 4.5,
                "k1": 9.0, "AAA": 3.0, "wr_ir": 0.0, "db1": 6.5} if done else
               {"qb1": 24.0, "rb1": 15.0, "wr1": 11.0, "te1": 6.0, "rb3": 0.0,
                "k1": 8.0, "AAA": 4.0, "wr_ir": 0.0, "db1": 0.0} if cur else
               {p: 0.0 for p in ROSTERS[0]["players"]})
        pp2 = ({"rb2": 6.5, "wr2": 8.0, "fb1": 0.5} if done else
               {"rb2": 7.0, "wr2": 9.0, "fb1": 0.0} if cur else
               {"rb2": 0.0, "wr2": 0.0, "fb1": 0.0})
        st1 = STATE["starters"].get((1, wk), ["qb1", "rb1", "wr1", "te1", "rb3", "k1", "AAA"])
        return [{"roster_id": 1, "matchup_id": 1,
                 "points": round(sum(pp1.get(p, 0.0) for p in st1), 2),
                 "starters": st1,
                 "starters_points": [pp1.get(p, 0.0) for p in st1],
                 "players": ROSTERS[0]["players"], "players_points": pp1},
                {"roster_id": 2, "matchup_id": 1,
                 "points": round(pp2["rb2"] + pp2["wr2"], 2),
                 "starters": ["rb2", "wr2"],
                 "starters_points": [pp2["rb2"], pp2["wr2"]],
                 "players": ROSTERS[1]["players"], "players_points": pp2}]
    if re.fullmatch(r"/league/\d+/transactions/\d+", path):
        # REST NEVER CARRIES A PENDING WAIVER CLAIM, even at origin.
        return REST_TRANSACTIONS
    if re.fullmatch(r"/league/\d+/(winners|losers)_bracket", path):
        return [{"m": 1, "r": 1, "t1": 1, "t2": 2, "w": 1, "l": 2, "p": 1}]
    if re.fullmatch(r"/draft/\w+/picks", path):
        return DRAFT_PICKS
    if re.fullmatch(r"/draft/\w+", path):
        return {"draft_id": "d1", "type": "snake", "status": "complete",
                "season": SEASON, "settings": {"rounds": 2}}
    if m := re.fullmatch(r"/scores/nfl/regular/\d+/(\d+)", path):
        return _games(int(m.group(1)))
    if path == "/state/nfl":
        return {"week": WEEK, "season": SEASON, "season_type": "regular",
                "league_season": SEASON}
    if m := re.fullmatch(r"/user/([\w%.-]+)", path):
        if m.group(1) in ("alder", "Alder", "u1"):
            return {"user_id": "u1", "display_name": "Alder", "username": "alder"}
        return None
    if re.fullmatch(r"/user/\w+/leagues/nfl/\d+", path):
        return [CLONE_CFG, LEAGUE_CFG]
    raise Unfixtured(f"REST {path}")


def _raise(what):
    raise Unfixtured(f"REST {what}")


# Operations Sleeper refuses without a token. Every mutation is authenticated.
AUTHED = {
    "matchup_legs", "matchup_legs_related_to_roster", "messages",
    "roster_standings", "get_pickem_legs", "get_pickem_picks_for_league",
    "get_pickem_scoring_settings", "league_transactions_filtered",
    "league_transactions_by_player", "roster_draft_picks",
    "roster_draft_picks_by_owner", "watched_players", "me", "my_leagues",
    "league_rosters",
}

_OP = re.compile(r"^\s*(?:(query|mutation)\b[^{]*)?\{\s*(\w+)")


def op_name(query: str) -> tuple[str, str]:
    """('query'|'mutation', top-level field name) of a GraphQL document."""
    m = _OP.match(query)
    if not m:
        raise Unfixtured(f"GQL unparseable: {query[:80]}")
    return (m.group(1) or "query"), m.group(2)


def _rosters_gql():
    out = []
    for r in ROSTERS:
        out.append({"roster_id": r["roster_id"], "owner_id": r["owner_id"],
                    "players": list(r["players"]),
                    "starters": list(r["starters"]),
                    "reserve": list(STATE["reserve"].get(r["roster_id"], [])),
                    "taxi": list(r.get("taxi") or []),
                    "keepers": list(STATE["keepers"].get(r["roster_id"], [])),
                    "settings": r["settings"], "metadata": None,
                    "co_owners": r.get("co_owners")})
    return out


async def gql(query: str, variables=None, auth: bool = False):
    from sleeper_mcp import client
    from sleeper_mcp.client import AuthError, ConfigError
    _policy(query)
    q = " ".join(query.split())
    kind, op = op_name(q)
    if (op in AUTHED or kind == "mutation") and not auth:
        raise AuthError(f"Unauthorized (fake): {op} needs auth=True")
    if auth and not client.TOKEN:
        # What the real client does: refuse before any request is built.
        raise ConfigError("This query is authenticated and SLEEPER_TOKEN is not set.")
    if op in FAIL:
        FAIL.discard(op)
        raise RuntimeError(f"fake: injected failure for {op}")
    v = variables or {}

    # --- reads --------------------------------------------------------------
    if op == "weekly_stats":
        wk = int(re.search(r"week:(\d+)", q).group(1))
        return {"weekly_stats": _stat_rows(wk)}
    if op == "season_stats":
        return {"season_stats": [{"player_id": pid, "team": _TEAM_OF[pid],
                                  "week": None, "stats": st}
                                 for pid, st in STATS.items()]}
    if op == "roster_standings":
        rnd = int(re.search(r"round:(\d+)", q).group(1))
        return {"roster_standings": [
            {"roster_id": 1, "rank": 1, "wins": rnd, "losses": 0, "ties": 0,
             "points": 100.0 * rnd, "record": "W" * rnd},
            {"roster_id": 2, "rank": 2, "wins": 0, "losses": rnd, "ties": 0,
             "points": 90.0 * rnd, "record": "L" * rnd}]}
    if op == "league_transactions_by_player":
        pid = re.search(r'player_id:"(\w+)"', q).group(1)
        rows = [r for r in GQL_TRANSACTIONS
                if pid in (r.get("adds") or {}) or pid in (r.get("drops") or {})]
        return {"league_transactions_by_player": rows}
    if op == "league_transactions_filtered":
        return {"league_transactions_filtered": _gql_transactions(q)}
    if op == "roster_draft_picks":
        return {"roster_draft_picks": [
            {"season": "2027", "round": 6, "roster_id": 2, "owner_id": 1,
             "previous_owner_id": 2}]}
    if op == "roster_draft_picks_by_owner":
        rid = int(re.search(r'owner_roster_id:"?(\d+)', q).group(1))
        return {"roster_draft_picks_by_owner": (
            [{"season": "2027", "round": 6, "roster_id": 2, "owner_id": 1,
              "previous_owner_id": 2}] if rid == 1 else [])}
    if op == "matchup_legs":
        wk = int(re.search(r"round:(\d+)", q).group(1))
        done = wk < WEEK
        return {"matchup_legs": [
            {"roster_id": 1, "matchup_id": 1, "round": wk,
             "points": 100.0 if done else None, "proj_points": 101.0,
             "max_points": 110.0 if done else None,
             "starters": STATE["starters"].get((1, wk), ["qb1", "rb1", "wr1", "te1", "rb3", "k1", "AAA"])},
            {"roster_id": 2, "matchup_id": 1, "round": wk,
             "points": 90.0 if done else None, "proj_points": 88.0,
             "max_points": 95.0 if done else None,
             "starters": STATE["starters"].get((2, wk), ["rb2", "wr2"])}]}
    if op == "matchup_legs_related_to_roster":
        rid = int(re.search(r"roster_id:(\d+)", q).group(1))
        a, b = (int(x) for x in re.search(r"start_round:(\d+),end_round:(\d+)", q).groups())
        return {"matchup_legs_related_to_roster": [
            {"round": w, "matchup_id": 1, "roster_id": rid,
             "points": (100.0 if rid == 1 else 90.0) if w < WEEK else None,
             "proj_points": 101.0 if rid == 1 else 88.0,
             "max_points": (110.0 if rid == 1 else 95.0) if w < WEEK else None,
             "starters": ROSTERS[rid - 1]["starters"]} for w in range(a, b + 1)]}
    if op == "get_player_news":
        # metadata is a DICT, verified against the live endpoint — the first
        # draft of this fixture guessed a JSON string and the tool raised
        # AttributeError, which is the fake being wrong rather than the tool.
        return {"get_player_news": [
            {"source": "fake wire", "published": 1757272204863,
             "metadata": {"title": "A thing happened",
                          "description": "He did a thing. " * 30,
                          "analysis": "It may matter.", "topic_id": "1"}}]}
    if op == "get_player_outlook":
        return {"get_player_outlook": {"source": "fake wire", "published": 1757272204863,
                                       "metadata": {"title": "Outlook",
                                                    "analysis": "Fine season ahead."}}}
    if op == "stats_for_players_in_week":
        wk = int(re.search(r"week:(\d+)", q).group(1))
        ids = re.findall(r'"(\w+)"', re.search(r"player_ids:\[([^\]]*)\]", q).group(1))
        return {"stats_for_players_in_week": _proj_rows(wk, ids)}
    if op == "trending_players":
        return {"trending_players": [{"player_id": "wr3", "count": 900}]}
    if op == "get_pickem_legs":
        return {"get_pickem_legs": [
            {"leg_id": "v1:regular:4", "status": "complete",
             "num_expected_picks": 2,
             "picks": {g: {"team": t, "outcome": "win", "game_id": g}
                       for g, t in STATE["picks"]["v1:regular:4"].items()},
             "tiebreaker": {"type": "total_points", "value": 41, "game_id": "g41"},
             "leg_scoring_result": {"g41": 1.0, "g42": 0.0}},
            {"leg_id": "v1:regular:5", "status": "open",
             "num_expected_picks": 2,
             "picks": {g: {"team": t, "outcome": "win", "game_id": g}
                       for g, t in STATE["picks"]["v1:regular:5"].items()},
             "tiebreaker": {"type": "total_points", "value": 40, "game_id": "g51"},
             "leg_scoring_result": {}}]}
    if op == "get_pickem_picks_for_league":
        return {"get_pickem_picks_for_league": {
            "1": {"picks": {"g51": {"team": "AAA", "outcome": "win"},
                            "g52": {"team": "BBB", "outcome": "win"}},
                  "tiebreaker": {"type": "total_points", "value": 40}},
            "2": {"picks": {"g51": {"team": "CCC", "outcome": "win"},
                            "g52": {"team": "BBB", "outcome": "win"}},
                  "tiebreaker": {"type": "total_points", "value": 44}},
            "3": {"picks": {}, "tiebreaker": None}}}
    if op == "get_pickem_scoring_settings":
        return {"get_pickem_scoring_settings": {"v1:regular:4": 1.0,
                                                "v1:regular:5": 1.0}}
    if op == "messages":
        # Two pages: without `before` the newest 50, with it the older ones.
        before = re.search(r'before:"?(\w+)', q)
        if before and before.group(1) == "m0":
            return {"messages": []}
        if before:
            return {"messages": [{"message_id": "m0", "created": 1757000000000,
                                  "author_display_name": "Yew", "text": "old trade talk",
                                  "pinned": False, "attachment": None}]}
        return {"messages": [{"message_id": "m2", "created": 1758300000000,
                              "author_display_name": "Alder", "text": "who wants a trade",
                              "pinned": True, "attachment": None},
                             {"message_id": "m1", "created": 1758200000000,
                              "author_display_name": "Birch", "text": "",
                              "pinned": False,
                              "attachment": {"type": "gif", "url": "x"}}]}
    if op == "watched_players":
        return {"watched_players": [{"player_id": p} for p in sorted(STATE["watch"])]}
    if op == "me":
        return {"me": {"user_id": "u1", "display_name": "Alder"}}
    if op == "my_leagues":
        return {"my_leagues": [
            {"league_id": LEAGUE, "name": "Fake League", "sport": "nfl",
             "season": SEASON, "status": "in_season", "total_rosters": 2},
            {"league_id": PICKEM_LEAGUE, "name": "Fake Pool ", "sport": "pickem:nfl",
             "season": SEASON, "status": "in_season", "total_rosters": 3}]}
    if op == "rosters_by_user":
        return {"rosters_by_user": [{"league_id": PICKEM_LEAGUE, "roster_id": 1},
                                    {"league_id": LEAGUE, "roster_id": 1}]}
    if op == "league_players":
        return {"league_players": _league_players()}
    if op == "league_rosters":
        return {"league_rosters": _rosters_gql()}
    if op == "scores":
        wk = int(re.search(r"week:(\d+)", q).group(1))
        return {"scores": _games(wk)}

    # --- mutations (stateful) -------------------------------------------------
    if kind == "mutation":
        SENT.append((op, dict(v) if v else q))
    if op == "update_matchup_leg":
        STATE["starters"][(int(v["rid"]), int(v["leg"]))] = list(v["s"])
        return {"update_matchup_leg": {"roster_id": v["rid"], "starters": list(v["s"])}}
    if op == "roster_update_reserve":
        STATE["reserve"][int(v["rid"])] = list(v["r"])
        return {"roster_update_reserve": {"roster_id": v["rid"], "reserve": list(v["r"])}}
    if op == "submit_waiver_claim":
        tid = f"w{len(STATE['claims']) + 1}"
        bid = (v.get("vs") or [0])[0]
        STATE["claims"].append({
            "transaction_id": tid, "type": "waiver", "status": "pending",
            "leg": WEEK, "created": 1758950000000, "league_id": LEAGUE,
            "roster_ids": sorted({*(v.get("va") or []), *(v.get("vd") or [])}),
            "adds": dict(zip(v.get("ka") or [], v.get("va") or [])) or None,
            "drops": dict(zip(v.get("kd") or [], v.get("vd") or [])) or None,
            "draft_picks": None, "waiver_budget": None,
            "settings": {"waiver_bid": bid}, "creator": "u1",
            "consenter_ids": list(v.get("va") or [])})
        return {"submit_waiver_claim": {"transaction_id": tid, "status": "pending"}}
    if op == "update_waiver_claim":
        for c in STATE["claims"]:
            if c["transaction_id"] == v["tx"]:
                c["settings"]["waiver_bid"] = (v.get("vs") or [0])[0]
        return {"update_waiver_claim": {"transaction_id": v["tx"], "status": "pending"}}
    if op == "cancel_waiver_claim":
        STATE["status"][v["tx"]] = "cancelled"
        return {"cancel_waiver_claim": {"transaction_id": v["tx"], "status": "cancelled"}}
    if op == "propose_trade":
        tid = f"tr{len(STATE['claims']) + 1}"
        STATE["claims"].append({
            "transaction_id": tid, "type": "trade", "status": "pending",
            "leg": WEEK, "created": 1758950000000, "league_id": LEAGUE,
            "roster_ids": sorted({*(v.get("va") or []), *(v.get("vd") or [])}),
            "adds": dict(zip(v.get("ka") or [], v.get("va") or [])) or None,
            "drops": dict(zip(v.get("kd") or [], v.get("vd") or [])) or None,
            "draft_picks": list(v.get("dp") or []), "waiver_budget": None,
            "settings": None, "creator": "u1", "consenter_ids": [1]})
        return {"propose_trade": {"transaction_id": tid, "status": "pending"}}
    if op in ("accept_trade", "reject_trade"):
        STATE["status"][v["tx"]] = "complete" if op == "accept_trade" else "rejected"
        return {op: {"transaction_id": v["tx"], "status": STATE["status"][v["tx"]]}}
    if op == "roster_set_keepers":
        k = v.get("k")
        if isinstance(k, str):
            k = json.loads(k)
        STATE["keepers"][int(v["rid"])] = list(k or [])
        return {"roster_set_keepers": {"roster_id": v["rid"], "keepers": list(k or [])}}
    if op == "add_league_player_trade_block":
        pid = re.search(r'player_id:"(\w+)"', q).group(1)
        STATE["otb"][pid] = 1
        return {"add_league_player_trade_block": {"player_id": pid, "settings": {"otb": 1}}}
    if op == "remove_league_player_trade_block":
        pid = re.search(r'player_id:"(\w+)"', q).group(1)
        STATE["otb"].pop(pid, None)
        return {"remove_league_player_trade_block": {"player_id": pid, "settings": None}}
    if op == "make_pickem_pick":
        leg = v["leg"]
        STATE["picks"].setdefault(leg, {})[v["p"]["game_id"]] = v["p"]["team"]
        return {"make_pickem_pick": {"leg_id": leg}}
    if op == "set_pickem_tiebreaker":
        return {"set_pickem_tiebreaker": {"leg_id": v["leg"]}}
    if op == "watch_player":
        pid = re.search(r'player_id:"(\w+)"', q).group(1)
        STATE["watch"].add(pid)
        return {"watch_player": {"player_id": pid}}
    if op == "unwatch_player":
        pid = re.search(r'player_id:"(\w+)"', q).group(1)
        STATE["watch"].discard(pid)
        return {"unwatch_player": True}
    raise Unfixtured(f"GQL {q[:90]}")


# Tool modules do `from .client import gql, rest`, so each holds its OWN
# reference and patching client alone reaches none of them. Every module that
# imported a transport has to be patched by name.
TOOL_MODULES = ("client", "discovery", "drafts", "keepers", "lineups",
                "playoffs", "reads", "signals", "usage", "writes", "pickem")


def install(monkeypatch):
    """Point every module's transport at this fake, for one test."""
    import importlib

    from sleeper_mcp import client
    reset()
    for name in TOOL_MODULES:
        try:
            mod = importlib.import_module(f"sleeper_mcp.{name}")
        except ModuleNotFoundError:
            continue
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


def writes_on(monkeypatch):
    """Flip the account-holder switch, for tests of the send+verify paths."""
    from sleeper_mcp import client
    monkeypatch.setattr(client, "WRITES_ENABLED", True)


def no_token(monkeypatch):
    from sleeper_mcp import client
    monkeypatch.setattr(client, "TOKEN", "")
