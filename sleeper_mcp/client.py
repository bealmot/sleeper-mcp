"""HTTP, auth and league configuration for the Sleeper MCP server.

Sleeper has two APIs and they behave differently:

    REST      https://api.sleeper.app/v1   public, unauthenticated, CACHED
    GraphQL   https://sleeper.com/graphql  undocumented, some fields need auth

THE SINGLE MOST IMPORTANT RULE HERE: **Sleeper returns 403 if you do not send
a User-Agent.** Not a friendly error — a flat refusal that looks like a block.
Every request in this module sends one.

The second: **REST responses are Cloudflare-cached.** A read of your roster can
return minutes-old data. It is fine for player dictionaries and league config;
it must NEVER be used to confirm that a write landed. Verify writes over
GraphQL.

Configuration, all optional, from the environment or the config file
(environment wins; see config.py for why there are two):

    SLEEPER_LEAGUE_ID      default league for tools that take one
    SLEEPER_ROSTER_ID      your roster in that league (an int, 1..N)
    SLEEPER_PICKEM_LEAGUE  pick'em lobby id, if you play pick'em
    SLEEPER_PICKEM_ROSTER  your entry in that lobby
    SLEEPER_TOKEN          JWT, WRITES ONLY — reads never need it
    SLEEPER_ENABLE_WRITES  must be "1" for any mutation to be attempted

`sleeper-mcp setup` verifies a token and writes the file. Do not know your
ids? Call `find_my_leagues("<your username>")`. Everything it needs is public.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import time

import httpx

from . import config as _config
from .boundaries import check as _policy_check
from .lookup import fill_defense_names

# SUPPORTS BOTH MAJOR VERSIONS OF THE MCP SDK.
#
# mcp 2.0 renamed FastMCP to MCPServer. The surface this server actually uses
# is unchanged across the rename — same @tool() decorator, same synchronous
# run(transport=...), same async list_tools(), same leading constructor args —
# so a two-line shim covers both rather than stranding users on one major
# version. Verified against mcp 1.x and 2.2.0.
#
# Prefer the v2 name: a v1 install has no MCPServer, so the fallback is the
# one that fires, and new installs get the current class without a deprecation
# path to maintain.
#
# mcp 2.0 also moved the protocol types into their own package. ToolAnnotations
# is the only one this server builds itself. Both imports hang off the SAME
# test, the server class: a downgrade from 2.x can leave mcp_types installed
# beside mcp 1.x, and probing mcp_types on its own would then mix the two.
# annotations= on @tool() arrived in mcp 1.7.0, hence the floor in
# pyproject.toml.
try:                                    # mcp >= 2
    from mcp.server import MCPServer as _Server
    from mcp_types import ToolAnnotations
except ImportError:                     # mcp 1.x
    from mcp.server import FastMCP as _Server
    from mcp.types import ToolAnnotations

# httpx logs full request URLs at INFO. Nothing here puts a secret in a URL,
# but a token in a header is one refactor away from being one, so keep the
# request log quiet by default.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# This server's own warnings: a 429 retry, a stale player dictionary. They go
# to stderr, which an MCP client keeps in its server log; stdout is the
# protocol and must never carry them.
_log = logging.getLogger("sleeper_mcp")

mcp = _Server("sleeper")

# Tool annotations: what a client reads to decide when to ask the user before
# a call. Every tool reaches Sleeper, so every tool is open-world. A write's
# hints describe the call with confirm=True; the default dry run changes
# nothing, but the hint has to cover the worst case of the call, not the usual
# one. tests/test_annotations.py fails on a tool registered without them.
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def write(*, destructive: bool, idempotent: bool) -> ToolAnnotations:
    """Hints for a tool that changes state.

    destructive: it can remove or overwrite something (a starter, a rostered
        player, a pick), not only add.
    idempotent: the same call twice leaves the same state as once.
    """
    return ToolAnnotations(readOnlyHint=False, destructiveHint=destructive,
                           idempotentHint=idempotent, openWorldHint=True)

GQL = "https://sleeper.com/graphql"
REST = "https://api.sleeper.app/v1"

# Identify the client honestly. Sleeper 403s without any User-Agent; sending a
# real one is both required and polite.
UA = {"User-Agent": "sleeper-mcp (+https://github.com/bealmot/sleeper-mcp)"}


def _env(name: str, default: str = "") -> str:
    """Environment first, then the config file, then the default."""
    return _config.resolve(name, default)


DEFAULT_LEAGUE = _env("SLEEPER_LEAGUE_ID")
DEFAULT_PICKEM_LEAGUE = _env("SLEEPER_PICKEM_LEAGUE")
TOKEN = _env("SLEEPER_TOKEN")
WRITES_ENABLED = _config.truthy(_env("SLEEPER_ENABLE_WRITES"))


def _int_env(name: str) -> int | None:
    raw = _env(name)
    try:
        return int(raw)
    except ValueError:
        return None


DEFAULT_ROSTER = _int_env("SLEEPER_ROSTER_ID")
DEFAULT_PICKEM_ROSTER = _int_env("SLEEPER_PICKEM_ROSTER")


class ConfigError(RuntimeError):
    """A required id or credential is missing, with instructions to fix it."""


class WritesDisabled(RuntimeError):
    """A mutation was attempted while writes are switched off."""


class AuthError(RuntimeError):
    """Sleeper rejected the token. The message says what is wrong with it
    (shape, source, likely expiry) without revealing it."""


def league_id(explicit: str | None = None) -> str:
    lg = (explicit or DEFAULT_LEAGUE).strip()
    if not lg:
        raise ConfigError(
            "No league id. Pass league_id=..., or set SLEEPER_LEAGUE_ID. "
            "Run find_my_leagues('<your username>') to look yours up — it "
            "needs no credentials.")
    return lg


def roster_id(explicit: int | None = None) -> int:
    rid = explicit if explicit is not None else DEFAULT_ROSTER
    if rid is None:
        raise ConfigError(
            "No roster id. Pass roster_id=..., or set SLEEPER_ROSTER_ID. "
            "find_my_leagues('<your username>') reports it for every league "
            "you are in.")
    return int(rid)


def require_writes(action: str) -> None:
    """Gate every mutation. Writes are OFF unless deliberately enabled.

    This exists because the failure mode is social, not technical: a mis-sent
    trade proposal arrives in front of a real person in someone's league, and
    an accepted trade may execute with no veto window. Opting in should be a
    conscious act, not a default.
    """
    if not WRITES_ENABLED:
        raise WritesDisabled(
            f"Writes are disabled, so {action} was not attempted. Set "
            f"SLEEPER_ENABLE_WRITES=1 (or run `sleeper-mcp setup`) to allow "
            f"mutations. Every write tool also has its own dry-run default on "
            f"top of this.")
    if not TOKEN:
        raise ConfigError(
            f"{action} needs SLEEPER_TOKEN. Run `sleeper-mcp setup`, or set it: "
            f"it is the JWT in the Sleeper web app under localStorage key "
            f"'token' (DevTools > Application > Local Storage > sleeper.com). "
            f"It is account-scoped and lasts about a year. Reads need no token "
            f"at all.")


# 429: Sleeper asks the caller to slow down. A 429 means the request was NOT
# processed, so sending it again is safe, a mutation included. Retry a few
# times and honour Retry-After, but cap each wait: a tool call that sleeps
# for ten minutes looks like a hang, and an error the user can see is better.
# Retry-After in the HTTP-date form is not parsed; it falls back to doubling.
# Nothing else is retried. A 500 or a timeout is reported as it happened.
_RETRIES = 3
_MAX_WAIT = 30.0
_sleep = asyncio.sleep                  # replaced in tests


def _http(timeout: float) -> httpx.AsyncClient:
    """Every request's client. A seam, so tests can give it a MockTransport."""
    return httpx.AsyncClient(timeout=timeout)


async def _send(c: httpx.AsyncClient, what: str, method: str, url: str,
                **kw) -> httpx.Response:
    for attempt in range(_RETRIES + 1):
        r = await c.request(method, url, **kw)
        if r.status_code != 429 or attempt == _RETRIES:
            return r
        try:
            wait = float(r.headers.get("retry-after", ""))
        except ValueError:
            wait = 2.0 * 2 ** attempt
        wait = min(max(wait, 0.0), _MAX_WAIT)
        _log.warning("Sleeper answered 429 to %s; retry %d of %d in %.0f s",
                     what, attempt + 1, _RETRIES, wait)
        await _sleep(wait)
    raise AssertionError("unreachable")


async def gql(query: str, variables: dict | None = None,
              auth: bool = False) -> dict:
    """POST to Sleeper's GraphQL.

    `auth=True` is needed for more than just mutations — `matchup_legs` and
    `messages` are authenticated READS, unlike most league queries.

    VALUES GO IN `variables`, NEVER IN THE QUERY TEXT. A league id spliced
    into the text, such as `1"){...} x:matchup_legs(league_id:"1`, adds fields
    to a query that runs with the user's token. tests/conftest.py fails any
    test that sends a spliced string.
    """
    # Refuse Sleeper's real-money surface before the request is built. See
    # boundaries.py — this server is fantasy-only, deliberately.
    _policy_check(query)
    headers = {"content-type": "application/json", **UA}
    if auth:
        if not TOKEN:
            raise ConfigError(
                "This query is authenticated and SLEEPER_TOKEN is not set. "
                "Most reads work without it; this one does not. "
                "`sleeper-mcp setup` saves one.")
        headers["authorization"] = TOKEN
    body: dict = {"query": query}
    if variables:
        body["variables"] = variables
    async with _http(45) as c:
        r = await _send(c, "a GraphQL query", "POST", GQL, json=body,
                        headers=headers)
        if r.status_code == 401:
            # The bare 401 is the single most common support question, and
            # the cause is almost always the token's delivery, not Sleeper.
            raise AuthError(_config.explain_401(TOKEN))
        r.raise_for_status()
        d = r.json()
    if d.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?") for e in d["errors"]))
    return d.get("data") or {}


async def rest(path: str):
    """GET Sleeper's public REST API.

    CACHED BY CLOUDFLARE. Good for player dictionaries and league config,
    useless for confirming a write — it has been observed returning a stale
    lineup for minutes after a successful mutation.
    """
    _policy_check(path)
    async with _http(30) as c:
        r = await _send(c, path, "GET", f"{REST}{path}", headers=UA)
        r.raise_for_status()
        return r.json()


# In-process caches. Sleeper's player dictionary is ~5 MB and is fetched by
# nearly every tool; league config is fetched almost as often. Without this a
# single conversation that calls three tools downloads 15 MB and waits for it
# three times.
#
# TTLs reflect how fast each thing actually changes. The player dictionary
# updates on news (injury tags, depth charts) — 15 minutes is well inside any
# useful reaction window. League CONFIG (scoring, roster_positions) does not
# change during a season at all; an hour is conservative.
#
# NOTHING MUTABLE IS CACHED HERE. Rosters, matchups and transactions are
# deliberately absent: they change when a manager acts, and the reason you are
# reading one is usually that somebody just did. A stale roster would also make
# a write's verification read meaningless.
_CACHE: dict = {}
_TTL = {"players": 900.0, "league": 3600.0}


def cache_clear() -> int:
    """Drop cached reads. Call after anything that could invalidate them."""
    n = len(_CACHE)
    _CACHE.clear()
    return n


async def _cached(key: str, kind: str, fetch):
    import time as _time
    hit = _CACHE.get(key)
    if hit and (_time.monotonic() - hit[0]) < _TTL.get(kind, 0):
        return hit[1]
    value = await fetch()
    _CACHE[key] = (_time.monotonic(), value)
    return value


# The player dictionary ON DISK. The memory cache dies with the process, and
# an MCP server is a process per client session, so every new session
# downloaded 5 MB before its first answer. The disk copy uses the same TTL as
# memory: freshness does not change, only who pays for the download.
#
# It is also the fallback when Sleeper fails: a copy up to _STALE_LIMIT old is
# used, with a warning that gives its age. Player names and positions barely
# move in a week; injury tags do, which is why the age is stated and why an
# older copy is refused rather than served.
_STALE_LIMIT = 7 * 86400.0


def cache_dir() -> pathlib.Path:
    """SLEEPER_MCP_CACHE if set, else $XDG_CACHE_HOME/sleeper-mcp."""
    explicit = (os.environ.get("SLEEPER_MCP_CACHE") or "").strip()
    if explicit:
        return pathlib.Path(explicit).expanduser()
    base = (os.environ.get("XDG_CACHE_HOME") or "").strip()
    root = pathlib.Path(base) if base else pathlib.Path.home() / ".cache"
    return root / "sleeper-mcp"


def _disk_read(name: str) -> tuple[float, dict] | None:
    """(age in seconds, contents), or None if missing or unreadable."""
    path = cache_dir() / f"{name}.json"
    try:
        age = time.time() - path.stat().st_mtime
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return (age, value) if isinstance(value, dict) else None


def _disk_write(name: str, value: dict) -> None:
    """Replace the copy atomically. A failed write costs a download, so it is
    logged, never raised: it must not fail the read that triggered it."""
    path = cache_dir() / f"{name}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        _log.warning("could not save %s to %s: %s", name, path, e)


async def players() -> dict:
    """The full NFL player dictionary — about 5 MB, ~11,000 entries.

    Cached for 15 minutes, in memory and on disk (see cache_dir): it is
    fetched by nearly every tool and changes only when news lands. When
    Sleeper fails, a disk copy up to a week old is used, and the log says so.

    CAUTION: names are NOT unique and the dictionary includes retired players.
    "Kenneth Walker" matches two entries, one of them inactive. Resolving a
    name against the whole dictionary and taking the first hit will eventually
    submit an ineligible player, which Sleeper rejects with "no longer eligible
    for the slot they are assigned to". Resolve against a ROSTER where possible,
    and always prefer ids.
    """
    async def load() -> dict:
        disk = _disk_read("players")
        if disk and disk[0] < _TTL["players"]:
            raw = disk[1]
        else:
            try:
                raw = await rest("/players/nfl")
            except (httpx.HTTPError, ValueError) as e:
                if not disk or disk[0] > _STALE_LIMIT:
                    raise
                _log.warning("Sleeper failed (%s); using the player "
                             "dictionary cached %.0f h ago", e,
                             disk[0] / 3600)
                raw = disk[1]
            else:
                _disk_write("players", raw)
        # Sleeper gives a team defence no full_name; without one no tool can
        # find it by name. See lookup.fill_defense_names.
        return fill_defense_names(raw)

    return await _cached("players", "players", load)


async def league(lg: str | None = None) -> dict:
    """League configuration. Cached for an hour — scoring settings and roster
    positions do not change mid-season."""
    lid = league_id(lg)
    return await _cached(f"league:{lid}", "league",
                         lambda: rest(f"/league/{lid}"))


async def state() -> dict:
    return await rest("/state/nfl")


async def current_week() -> int:
    return int((await state()).get("week") or 1)


async def teams_playing(week: int) -> set[str]:
    """The NFL teams with a game in this regular-season week.

    A team NOT in the set is on a BYE. The scoreboard is the schedule, so no
    bye table is kept here and none can go stale.

    AN EMPTY SET DOES NOT MEAN "NOBODY IS ON A BYE". It means the scoreboard
    has no games — the off-season, or a week Sleeper has not published — and
    nothing is known. A caller must say that its bye check did not run.

    Do not get the full team list from the player dictionary to invert this:
    the dictionary still carries stale teams (OAK, observed 2026-10-23).
    """
    season = (await state()).get("season")
    d = await gql("query($yr:String!,$wk:Int){"
                  'scores(sport:"nfl",season:$yr,season_type:"regular",'
                  "week:$wk){game_id metadata}}", {"yr": season, "wk": week})
    teams: set[str] = set()
    for g in d.get("scores") or []:
        m = g.get("metadata") or {}
        teams |= {m.get("away_team"), m.get("home_team")}
    return teams - {None, ""}


async def starting_slots(lg: str | None = None) -> list[str]:
    """This league's starting slots, IN ORDER.

    set_lineup takes starters positionally, so this must come from the league
    rather than be assumed. Layouts vary enormously — superflex, 3-WR,
    TE-premium, no kicker — and a wrong order does not error, it quietly starts
    players in the wrong slots.

    `roster_positions` includes bench and reserve entries; only the real
    starting slots are returned here.
    """
    lgd = await league(lg)
    return [p for p in (lgd.get("roster_positions") or [])
            if p not in ("BN", "IR", "TAXI")]


async def owners(lg: str) -> dict:
    """roster_id -> manager display name, for one league.

    Lives here rather than in a tool module because seven of them need it and
    it is league metadata, like league() and players(). It used to be `owners`
    inside reads.py, which meant every other tool module imported a private
    name out of a sibling — an underscore that six files ignored.
    """
    users = await rest(f"/league/{lg}/users")
    rosters = await rest(f"/league/{lg}/rosters")
    who = {u["user_id"]: (u.get("display_name") or u.get("username"))
           for u in (users or [])}
    return {r["roster_id"]: who.get(r.get("owner_id"), "?")
            for r in (rosters or [])}


def scored(stats: dict, scoring: dict) -> float:
    """Dot a projection's components against a league's own scoring settings.

    Sleeper's `pts_ppr` and `pts_std` are generic presets and are wrong for any
    league that deviates — half-PPR, per-first-down scoring, 6-point passing
    touchdowns, TE premium. `league.scoring_settings` is the league's actual
    rulebook, so weekly value is computed from raw components against it.

    Pass a projection through `firstdowns.fd_from_yards()` first. Sleeper
    projects every first down as yards / 10, and this dot product would score
    that stub as faithfully as it scores everything else.
    """
    return round(sum(scoring.get(k, 0) * v for k, v in stats.items()
                     if isinstance(v, (int, float))), 2)
