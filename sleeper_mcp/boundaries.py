"""What this server deliberately does NOT do, and why.

Sleeper's GraphQL API serves two products through one endpoint: fantasy
football, and a real-money betting business. Of roughly 240 queries, about 69
belong to the second — wagering, account balances, payment methods, tax
documents, responsible-gaming limits, and CFTC-regulated event contracts.

THIS SERVER EXPOSES NONE OF THEM, ON PURPOSE.

That is a design decision, not an oversight, so it is written down here rather
than left as an absence someone "fixes" later:

  * Event contracts are financial instruments. Placing one is executing a
    trade, not reading a stat.
  * Balances, payment methods and tax forms are financial account data. A
    fantasy tool has no business touching them, and a compromised or
    misconfigured MCP client would then have reach into them.
  * A tool that ranks or recommends wagers is a gambling-advice product. This
    is a fantasy football utility.

If you want those features, this is the wrong library and you should use
Sleeper's own app, which has the disclosures, limits and protections that
belong with them.

HOW THE GUARD WORKS. Two layers, because a deny-list alone was found to let
through the exact case its docstring names: the schema's `order_contract`
mutation contains none of the words a wagering deny-list looks for.

  1. AN ALLOW-LIST of the top-level operations this server issues, and of
     the REST routes it reads. Anything else is refused by default, so a new
     money endpoint Sleeper adds tomorrow is refused without anyone updating
     this file. Extending the server with a new fantasy query means adding
     its name to ALLOWED — a deliberate act, in a file that explains why.
  2. THE DENY-LIST, kept as a backstop, extended with the names the live
     schema actually uses, and applied to operation names rather than to the
     whole document — so a username containing "stripe" or "balance" does
     not trip it on the way to /user/<name>.

The guard is not security — anyone can edit a Python file. It exists so
that an accidental call fails loudly with an explanation, and so the intent
survives contact with a future contributor.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

# Every GraphQL operation this server issues. A query naming anything else
# is refused before it is sent.
ALLOWED = {
    # reads
    "me", "my_leagues", "rosters_by_user", "league_rosters", "league_players",
    "roster", "matchup_legs", "matchup_legs_related_to_roster",
    "league_transactions_filtered", "league_transactions_by_player",
    "roster_draft_picks", "roster_draft_picks_by_owner", "roster_standings",
    "get_pickem_legs", "get_pickem_picks_for_league",
    "get_pickem_scoring_settings", "get_player_news", "get_player_outlook",
    "stats_for_players_in_week", "weekly_stats", "season_stats",
    "trending_players", "scores", "messages", "watched_players",
    # writes
    "update_matchup_leg", "roster_update_reserve", "submit_waiver_claim",
    "update_waiver_claim", "cancel_waiver_claim", "propose_trade",
    "accept_trade", "reject_trade", "roster_set_keepers",
    "add_league_player_trade_block", "remove_league_player_trade_block",
    "make_pickem_pick", "remove_pickem_pick", "set_pickem_tiebreaker",
    "watch_player", "unwatch_player",
}

# The public REST routes this server reads. Path SHAPES, not content: a
# username is one segment and its letters are never inspected.
REST_ROUTES = re.compile(
    r"^/(players/nfl(/trending/(add|drop))?"
    r"|league/\d+(/(rosters|users|matchups/\d+|transactions/\d+"
    r"|winners_bracket|losers_bracket|drafts|traded_picks))?"
    r"|draft/\w+(/picks|/traded_picks)?"
    r"|scores/nfl/\w+/\d+/\d+"
    r"|state/nfl"
    r"|user/[^/?#]+(/leagues/nfl/\d+|/drafts/nfl/\d+)?)"
    r"(\?[\w&=.-]*)?$")

# Substrings that mark an operation as belonging to the money side, matched
# against OPERATION NAMES. Loose on purpose: a false positive costs one
# refused call, a false negative costs something worse. Extended from the
# names the live schema actually uses.
FORBIDDEN = re.compile(
    r"parlay|wager|bet_|_bet\b|payout|payment|balance|deposit|withdraw|"
    r"cftc|tax_form|kyc|wallet|responsible_gaming|contest_entry|"
    r"promo|bonus_cash|stripe|aeropay|interac|sportsbook|odds_|"
    r"order_contract|my_orders|active_positions|closed_positions|derby|"
    r"currenc|winnings|league_dues|matchup_challenge|socure|"
    r"user_exclusion|sweepstakes|tip_|purchase|verification_code|"
    r"event_contract|market_",
    re.IGNORECASE)

# Top-level field names of a GraphQL document: the first `{` opens the
# selection set, and each name at depth 1 is an operation. Aliases
# (`x: my_balances`) resolve to the field after the colon.
_TOP = re.compile(r"(?:^|[{\s,])(?:(\w+)\s*:\s*)?(\w+)\s*(?=[({\s,}]|$)")


def operations(document: str) -> list[str]:
    """Every top-level operation name in a GraphQL document."""
    doc = document or ""
    start = doc.find("{")
    if start < 0:
        return []
    depth, i, names, buf = 0, start, [], ""
    while i < len(doc):
        c = doc[i]
        if c == "{":
            depth += 1
            if depth == 2 and buf.strip():
                names.append(_field(buf))
            buf = ""
        elif c == "}":
            depth -= 1
            buf = ""
        elif c == "(" and depth == 1:
            if buf.strip():
                names.append(_field(buf))
            buf = ""
            # skip the argument list, which may contain braces in objects
            par = 1
            i += 1
            while i < len(doc) and par:
                par += {"(": 1, ")": -1}.get(doc[i], 0)
                i += 1
            continue
        elif depth == 1:
            if c in ",\n" or (c.isspace() and buf.strip() and
                              not buf.rstrip().endswith(":")):
                if buf.strip():
                    names.append(_field(buf))
                buf = ""
            else:
                buf += c
        i += 1
    if depth == 1 and buf.strip():
        names.append(_field(buf))
    return [n for n in names if n]


def _field(token: str) -> str:
    """'alias: field' -> 'field'; strips directives and stray punctuation."""
    t = token.strip().strip(",")
    if ":" in t:
        t = t.split(":", 1)[1].strip()
    m = re.match(r"\w+", t)
    return m.group(0) if m else ""


class RefusedByPolicy(RuntimeError):
    """Raised when an operation crosses this server's stated boundary."""


def _refuse(what: str, why: str) -> None:
    raise RefusedByPolicy(
        f"{what!r} {why}. sleeper-mcp is a fantasy football tool and "
        f"deliberately does not expose Sleeper's real-money surface — "
        f"wagering, balances, payments, tax documents or event contracts. "
        f"Use the Sleeper app, which carries the disclosures and protections "
        f"that belong with them. See boundaries.py.")


def check(operation: str) -> None:
    """Refuse anything that is not a known fantasy operation.

    Called before every GraphQL document and REST path this server sends.
    It will not stop a determined caller and is not meant to; it stops an
    accident, and it makes the boundary explicit at the point where it would
    otherwise be crossed.
    """
    op = operation or ""
    if op.startswith("/"):
        path = unquote(op)
        if not REST_ROUTES.match(path):
            _refuse(path, "is not a REST route this server reads")
        return
    names = operations(op)
    if not names:
        _refuse(op[:80], "is not a GraphQL document this server can vet")
    for name in names:
        if FORBIDDEN.search(name):
            _refuse(name, "is part of Sleeper's real-money surface")
        if name not in ALLOWED:
            _refuse(name, "is not an operation this server issues (add it to "
                          "boundaries.ALLOWED if it is fantasy football)")
