"""Where settings come from, and why there is more than one place.

Every setting resolves in this order; the first non-empty value wins:

    1. environment variable      SLEEPER_TOKEN, SLEEPER_LEAGUE_ID, ...
    2. config file               ~/.config/sleeper-mcp/config.json
    3. nothing                   (the tool that needs it says what to set)

The file exists because environment delivery is the most common way this
server fails in the field, and it fails silently. All of these produce a bare
401 from Sleeper with no further clue:

  * Some MCP clients expand `${VAR}` in their config and some pass the literal
    text through, so Sleeper receives the string "${SLEEPER_TOKEN}".
  * Login shells commonly `return` early for non-interactive sessions, so an
    `export` in ~/.bashrc never reaches a server launched by a desktop app.
  * A token pasted with its surrounding quotes, or with a "Bearer " prefix.

`sleeper-mcp setup` checks the token against Sleeper before writing it to the
file with 0600 permissions, after which the client config needs no `env` at
all. `auth_status` (a tool) and `sleeper-mcp status` (the CLI) report where
each setting came from and what is wrong with it — without ever printing the
token.
"""

from __future__ import annotations

import json
import os
import pathlib
import re

# environment variable -> key in the config file
KEYS = {
    "SLEEPER_TOKEN": "token",
    "SLEEPER_ENABLE_WRITES": "enable_writes",
    "SLEEPER_LEAGUE_ID": "league_id",
    "SLEEPER_ROSTER_ID": "roster_id",
    "SLEEPER_PICKEM_LEAGUE": "pickem_league",
    "SLEEPER_PICKEM_ROSTER": "pickem_roster",
}

_SOURCE: dict[str, str] = {}


def config_path() -> pathlib.Path:
    """SLEEPER_MCP_CONFIG if set, else $XDG_CONFIG_HOME/sleeper-mcp/config.json."""
    explicit = (os.environ.get("SLEEPER_MCP_CONFIG") or "").strip()
    if explicit:
        return pathlib.Path(explicit).expanduser()
    base = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    root = pathlib.Path(base) if base else pathlib.Path.home() / ".config"
    return root / "sleeper-mcp" / "config.json"


def load_file() -> dict:
    """The config file's contents, or {} if it is missing or unreadable."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve(env_name: str, default: str = "") -> str:
    """First non-empty of: environment, config file, default.

    Records where the value came from so diagnostics can say so.
    """
    value = (os.environ.get(env_name) or "").strip()
    if value:
        _SOURCE[env_name] = "environment"
        return value
    from_file = load_file().get(KEYS.get(env_name, env_name.lower()))
    if from_file is not None and str(from_file).strip():
        _SOURCE[env_name] = "config file"
        return str(from_file).strip()
    _SOURCE[env_name] = "unset"
    return default


def source(env_name: str) -> str:
    """'environment', 'config file' or 'unset' — for the last resolve() of it."""
    return _SOURCE.get(env_name, "unset")


def truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def save(updates: dict) -> pathlib.Path:
    """Merge `updates` into the config file.

    The file is CREATED 0600 and then renamed into place, so a half-written
    file is never left behind and the token is never world-readable, not even
    briefly. That last part used to be untrue: the temp file was written with
    `write_text` and chmod'd afterwards, which measurably leaves it at 0644
    with the token already in it. Permission must be set at creation, not
    after, or there is a window regardless of how short the code looks.

    The parent directory is tightened ONLY when this function created it.
    SLEEPER_MCP_CONFIG can point anywhere, and chmodding its parent
    unconditionally meant that pointing it at ~/.sleeperrc silently set the
    user's HOME to 0700 — a tool must not restrict a directory it was merely
    passed.
    """
    path = config_path()
    created = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if created:
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    data = load_file()
    data.update({k: v for k, v in updates.items() if v is not None})
    tmp = path.with_name(path.name + ".tmp")
    body = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)          # belt and braces: umask cannot widen it
    os.replace(tmp, path)
    return path


_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def diagnose_token(token: str) -> list[str]:
    """What is wrong with a token, described WITHOUT revealing it.

    An empty list means it is at least the right shape; whether Sleeper accepts
    it is a separate, live question.
    """
    if not token:
        return ["no token is set"]
    problems = []
    if "${" in token:
        problems.append("it is the literal text of an environment reference "
                        "such as ${SLEEPER_TOKEN} — your MCP client did not "
                        "expand it")
    if token[0] in "\"'" or token[-1] in "\"'":
        problems.append("it is wrapped in quotes — paste the value without them")
    if token.lower().startswith("bearer "):
        problems.append("it starts with 'Bearer ' — Sleeper wants the bare token")
    if any(c.isspace() for c in token):
        problems.append("it contains whitespace")
    if not problems and not _JWT.match(token):
        problems.append(f"it is not JWT-shaped (three dot-separated parts; this "
                        f"has {token.count('.')} dots and {len(token)} characters)")
    return problems


def describe_token(token: str) -> str:
    """One safe line about the token: length, shape, source. Never the value."""
    if not token:
        return "absent"
    shape = "JWT-shaped" if _JWT.match(token) else "NOT JWT-shaped"
    return f"{len(token)} chars, {shape}, from {source('SLEEPER_TOKEN')}"


FIX = ("Run `sleeper-mcp setup` to paste and verify a fresh token, or copy it "
       "again from the Sleeper web app: DevTools > Application > Local Storage "
       "> sleeper.com > key 'token', without the quotes.")


def explain_401(token: str) -> str:
    """The message a caller should see when Sleeper answers 401."""
    problems = diagnose_token(token)
    if problems:
        return ("Sleeper rejected the token (401): " + "; ".join(problems)
                + ". " + FIX)
    return (f"Sleeper rejected the token (401). It is {describe_token(token)} "
            f"and looks well-formed, so it has most likely expired or been "
            f"invalidated by a newer login. " + FIX)
