#!/usr/bin/env python3
"""Local CI. Standard library only — no linters, no network, no CI service.

Run it directly, or let the pre-push hook run it:

    python3 scripts/check.py
    git config core.hooksPath .githooks    # once, to enable the hook

The checks are chosen for THIS repo's actual failure modes rather than for
general tidiness:

  syntax        every module parses
  privacy       no personal identifiers leaked back in  <- the important one
  secrets       no tokens or keys committed
  docstrings    every tool has one, because it is what the model reads
  boundaries    the real-money guard still refuses what it claims to
  tests         pytest
  install       --fresh only: resolve and import in a CLEAN venv

PRIVACY IS THE ONE THAT EARNS ITS KEEP. This server was extracted from a
private homelab server that is still in use, and the natural way to add a
feature is to copy a working tool across. That tool will be full of one
person's league ids, team names and league-mates. This check fails the push.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "sleeper_mcp"

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def ok(msg: str) -> bool:
    print(f"  {GREEN}pass{OFF}  {msg}")
    return True


def fail(msg: str, detail: list[str] | None = None) -> bool:
    print(f"  {RED}FAIL{OFF}  {msg}")
    for d in (detail or [])[:20]:
        print(f"        {d}")
    return False


def py_files() -> list[pathlib.Path]:
    return sorted(list(PKG.glob("*.py")) + list((ROOT / "tests").glob("*.py"))
                  + list((ROOT / "scripts").glob("*.py")))


def check_syntax() -> bool:
    bad = []
    for f in py_files():
        try:
            ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            bad.append(f"{f.relative_to(ROOT)}:{e.lineno} {e.msg}")
    return fail("syntax", bad) if bad else ok(f"syntax ({len(py_files())} files)")


# Identifiers from the private server this was extracted from. Anything here
# appearing in a committed file means someone's league leaked into a public
# repo. Numeric ids are matched exactly; names case-insensitively.
PRIVATE = [
    r"\b1389357403081801728\b", r"\b1367220194950270976\b",
    r"\b1128178810970558464\b", r"\b1257085181978755072\b",
    r"\b24749\b",
    r"scatbacks", r"cave dwellers", r"king of iron fist",
    r"supacreamy", r"androux", r"aegis", r"privateer",
    r"myfantasyleague", r"joshhayden",
    r"/home/[a-z]+/", r"~/Projects/sleeper\b",
]
# Files allowed to contain these strings.
#
#   API-NOTES.md   documents the extraction's history in prose
#   check.py       IS the pattern list, so it always matches itself. This is
#                  the classic self-match: a scanner containing the strings it
#                  scans for finds itself every time, exactly like `pgrep -f`
#                  matching its own command line. Exempting the checker is the
#                  honest fix; obfuscating the patterns to dodge it would make
#                  the list unreadable for no gain.
PRIVACY_EXEMPT = {"API-NOTES.md", "check.py"}


def check_privacy() -> bool:
    hits = []
    for f in sorted(ROOT.rglob("*")):
        if not f.is_file() or ".git/" in str(f) or f.name in PRIVACY_EXEMPT:
            continue
        if f.suffix not in (".py", ".md", ".toml", ".json", ".txt", ".sh", ""):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat in PRIVATE:
            for m in re.finditer(pat, text, re.IGNORECASE):
                line = text[:m.start()].count("\n") + 1
                hits.append(f"{f.relative_to(ROOT)}:{line}  {m.group(0)!r}")
    if hits:
        return fail("privacy — private identifiers found in committed files",
                    hits + ["", "This repo was extracted from a private server.",
                            "Strip league ids, team names and personal paths."])
    return ok("privacy (no private identifiers)")


SECRETS = [
    (r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}", "a JWT"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "a GitHub token"),
    (r"(?i)(api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"]{12,}", "a literal credential"),
]


def check_secrets() -> bool:
    hits = []
    for f in py_files() + list(ROOT.glob("*.md")) + list(ROOT.glob("*.toml")):
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat, what in SECRETS:
            for m in re.finditer(pat, text):
                line = text[:m.start()].count("\n") + 1
                hits.append(f"{f.relative_to(ROOT)}:{line}  looks like {what}")
    return fail("secrets", hits) if hits else ok("secrets (none committed)")


def check_docstrings() -> bool:
    """Every @mcp.tool needs a docstring — it is the tool's interface."""
    missing = []
    for f in PKG.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            decorated = any(
                (isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool")
                or getattr(d, "attr", "") == "tool"
                for d in node.decorator_list)
            if decorated and not ast.get_docstring(node):
                missing.append(f"{f.name}:{node.lineno} {node.name}")
    n = sum(1 for f in PKG.glob("*.py")
            for _ in re.finditer(r"@mcp\.tool", f.read_text(encoding="utf-8")))
    return (fail("docstrings", missing) if missing
            else ok(f"docstrings ({n} tools, all documented)"))


def check_boundaries() -> bool:
    """The real-money guard must still refuse what it claims to."""
    sys.path.insert(0, str(ROOT))
    try:
        from sleeper_mcp.boundaries import RefusedByPolicy, check
    except Exception as e:                       # noqa: BLE001
        return fail(f"boundaries — cannot import ({e.__class__.__name__})")
    must_refuse = ["{my_balances{x}}", "{parlay(id:\"1\"){x}}",
                   "mutation{place_wager(a:1)}", "{my_payment_methods{id}}",
                   "{download_cftc_monthly_statement}", "{tax_forms{y}}"]
    must_allow = ["{roster(league_id:\"1\")}", "/league/1/rosters",
                  "mutation{update_matchup_leg(round:1)}"]
    bad = []
    for q in must_refuse:
        try:
            check(q)
            bad.append(f"ALLOWED a money query: {q}")
        except RefusedByPolicy:
            pass
    for q in must_allow:
        try:
            check(q)
        except RefusedByPolicy:
            bad.append(f"REFUSED a fantasy query: {q}")
    return (fail("boundaries", bad) if bad
            else ok(f"boundaries ({len(must_refuse)} refused, "
                    f"{len(must_allow)} allowed)"))


def check_fresh_install() -> bool:
    """Resolve dependencies into a CLEAN venv and import the package.

    THE ONE FAILURE MODE EVERY OTHER CHECK IS BLIND TO. Every check above runs
    against whatever is already installed here — so a dependency that no longer
    resolves for a NEW user passes them all while the published package is
    unusable.

    That is not hypothetical. The first outside contributor hit exactly this:
    `mcp` released a 2.x that renamed FastMCP to MCPServer, a fresh
    `pip install` pulled it, and the server died on import. Nothing local could
    have caught it, because this machine had a working 1.x venv from months
    earlier.

    Slow (it downloads), so it is opt-in rather than part of the pre-push hook.
    """
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="sleeper-mcp-fresh-")
    try:
        r = subprocess.run([sys.executable, "-m", "venv", f"{tmp}/venv"],
                           capture_output=True, text=True)
        if r.returncode:
            return fail("install — could not create a venv",
                        r.stderr.splitlines()[-5:])
        pip, py = f"{tmp}/venv/bin/pip", f"{tmp}/venv/bin/python"
        r = subprocess.run([pip, "install", "-q", str(ROOT)],
                           capture_output=True, text=True)
        if r.returncode:
            return fail("install — dependencies do not resolve",
                        (r.stderr or r.stdout).splitlines()[-8:])
        r = subprocess.run(
            # Public async list_tools() — present in BOTH mcp 1.x and 2.x.
            # The private _tool_manager is not a contract and differs.
            [py, "-c", "import asyncio, sleeper_mcp.server as s; "
                       "print(len(asyncio.run(s.mcp.list_tools())))"],
            capture_output=True, text=True)
        if r.returncode:
            return fail("install — resolves but does not import",
                        (r.stderr or "").strip().splitlines()[-8:]
                        + ["", "A dependency's new major version probably "
                              "changed its API. Pin it."])
        return ok(f"install (clean venv resolves and imports; "
                  f"{r.stdout.strip()} tools)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_tests() -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q",
                        str(ROOT / "tests")],
                       capture_output=True, text=True, cwd=ROOT)
    last = [l for l in r.stdout.strip().splitlines() if l.strip()][-1:] or [""]
    return ok(f"tests — {last[0].strip()}") if r.returncode == 0 else \
        fail("tests", r.stdout.strip().splitlines()[-15:])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fresh", action="store_true",
                    help="also resolve and import in a clean venv (slow, "
                         "downloads; catches broken dependency pins)")
    args = ap.parse_args()

    print(f"{DIM}sleeper-mcp local checks — {ROOT}{OFF}")
    results = [check_syntax(), check_privacy(), check_secrets(),
               check_docstrings(), check_boundaries(), check_tests()]
    if args.fresh:
        results.append(check_fresh_install())
    else:
        print(f"  {DIM}skip{OFF}  install — run with --fresh to resolve "
              f"dependencies in a clean venv")
    failed = results.count(False)
    print()
    if failed:
        print(f"  {RED}{failed} check(s) failed{OFF}")
        return 1
    print(f"  {GREEN}all {len(results)} checks passed{OFF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
