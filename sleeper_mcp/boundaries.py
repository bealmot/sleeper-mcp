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

The guard below is not security — anyone can edit a Python file. It exists so
that an accidental call fails loudly with an explanation, and so the intent
survives contact with a future contributor.
"""

from __future__ import annotations

import re

# Substrings that mark an operation as belonging to the money side. Matched
# loosely on purpose: a false positive costs one refused call, a false negative
# costs something worse.
FORBIDDEN = re.compile(
    r"parlay|wager|bet_|_bet\b|payout|payment|balance|deposit|withdraw|"
    r"cftc|tax_form|kyc|wallet|responsible_gaming|contest_entry|"
    r"promo|bonus_cash|stripe|aeropay|interac|sportsbook|odds_",
    re.IGNORECASE)


class RefusedByPolicy(RuntimeError):
    """Raised when an operation crosses this server's stated boundary."""


def check(operation: str) -> None:
    """Refuse real-money operations by name.

    Called before every GraphQL operation this server issues. It will not stop
    a determined caller and is not meant to; it stops an accident, and it makes
    the boundary explicit at the point where it would otherwise be crossed.
    """
    if FORBIDDEN.search(operation or ""):
        raise RefusedByPolicy(
            f"'{operation}' is part of Sleeper's real-money surface — "
            f"wagering, balances, payments, tax documents or event contracts. "
            f"sleeper-mcp is a fantasy football tool and deliberately does not "
            f"expose these. Use the Sleeper app, which carries the disclosures "
            f"and protections that belong with them. See boundaries.py.")
