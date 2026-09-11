"""`sleeper-mcp setup` and `sleeper-mcp status` — onboarding from a terminal.

setup   prompts for the token with hidden input, checks it against Sleeper
        BEFORE saving, looks up your leagues, and writes the config file with
        0600 permissions. Afterwards the MCP client config needs no `env`.
status  the same report as the `auth_status` tool, for a shell.

Neither prints the token. Both are plain functions so they stay testable
without a terminal.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import sys

import httpx

from . import config
from .client import GQL, UA

_ME = "{ me { user_id display_name } }"


def verify_token(token: str) -> tuple[bool, str]:
    """Ask Sleeper who this token belongs to. (ok, message) — never the token."""
    try:
        r = httpx.post(GQL, json={"query": _ME}, timeout=30,
                       headers={"authorization": token,
                                "content-type": "application/json", **UA})
    except httpx.HTTPError as e:
        return False, f"could not reach Sleeper ({e.__class__.__name__})"
    if r.status_code == 401:
        return False, "Sleeper rejected it (401)"
    if r.status_code != 200:
        return False, f"Sleeper answered HTTP {r.status_code}"
    me = ((r.json().get("data") or {}).get("me")) or {}
    if not me.get("user_id"):
        return False, "Sleeper accepted it but returned no user"
    return True, f"{me.get('display_name') or me['user_id']} (user {me['user_id']})"


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def setup() -> int:
    path = config.config_path()
    print(f"sleeper-mcp setup — writes {path}")
    print()
    print("Token: in the Sleeper web app, DevTools > Application > Local Storage")
    print("> sleeper.com > key 'token'. Paste the value WITHOUT the quotes.")
    print("It is account-scoped and lasts about a year; reads never need it.")
    print("Leave blank to stay read-only.")
    try:
        token = getpass.getpass("token: ").strip()
    except EOFError:
        token = ""
    updates: dict = {}
    if token:
        problems = config.diagnose_token(token)
        if problems:
            print("  not saved — the token " + "; ".join(problems))
            return 1
        ok, msg = verify_token(token)
        if not ok:
            print(f"  not saved — {msg}")
            return 1
        print(f"  verified — {msg}")
        updates["token"] = token
        ans = _ask("enable writes (lineups, waivers, trades)? [y/N]: ")
        updates["enable_writes"] = "1" if ans.lower() in ("y", "yes") else "0"
        if updates["enable_writes"] == "1":
            print("  writes ON. Every write tool still dry-runs unless confirm=True.")
    print()
    username = _ask("Sleeper username, to look up your league and roster ids "
                    "(blank to skip): ")
    if username:
        from .discovery import find_my_leagues
        print()
        print(asyncio.run(find_my_leagues(username)))
    league = _ask("default league id (blank to skip): ")
    if league:
        updates["league_id"] = league
    roster = _ask("default roster id in that league (blank to skip): ")
    if roster:
        updates["roster_id"] = roster
    saved = config.save(updates)
    print()
    print(f"saved {saved} (mode 0600)")
    print("Your MCP client config can now be just:")
    print(json.dumps({"mcpServers": {"sleeper": {"command": "sleeper-mcp"}}},
                     indent=2))
    print("Environment variables still win over the file if you set them.")
    return 0


def status() -> int:
    from .discovery import auth_status
    print(asyncio.run(auth_status()))
    return 0


USAGE = """usage: sleeper-mcp            run the MCP server (stdio)
       sleeper-mcp setup      save a verified token + default ids to the config file
       sleeper-mcp status     show where settings come from and whether the token works
"""


def dispatch(argv: list[str]) -> int | None:
    """Handle CLI subcommands. Returns None when the server should run."""
    if not argv:
        return None
    cmd = argv[0]
    if cmd == "setup":
        return setup()
    if cmd == "status":
        return status()
    if cmd in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0
    sys.stderr.write(f"unknown command {cmd!r}\n{USAGE}")
    return 2
