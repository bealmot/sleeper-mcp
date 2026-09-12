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
  names         pyflakes: a name used but never imported is a NameError
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
# Directories that are never ours: a virtualenv inside the checkout is full of
# other people's paths (pytest and pydantic ship files mentioning /home/...).
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist",
             "build", ".tox", ".mypy_cache", ".pytest_cache"}


def check_privacy() -> bool:
    hits = []
    for f in sorted(ROOT.rglob("*")):
        if (not f.is_file() or set(f.parts) & SKIP_DIRS
                or f.name in PRIVACY_EXEMPT):
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


def check_names() -> bool:
    """Undefined and unused names, via pyflakes.

    Syntax checking does not catch a name that was used but never imported —
    that is a NameError raised only when the line runs, so a tool nothing
    exercises ships green. `standings_trend` did exactly that: every check
    passed, and calling it raised NameError on `gql`.
    """
    py = _test_python()
    r = subprocess.run([py, "-m", "pyflakes", str(PKG)],
                       capture_output=True, text=True, cwd=ROOT)
    if "No module named" in (r.stderr or ""):
        return fail("names", [
            "pyflakes is not installed, so undefined names are not checked.",
            "  A name used but never imported raises only when the line runs.",
            "",
            "    uv pip install -e '.[dev]'",
        ])
    # pyflakes has no noqa support — that is flake8. This package marks its
    # deliberate side-effect imports with `# noqa: F401` (importing a tool
    # module IS the registration), so honour the marker the code already uses
    # rather than rewriting working imports to satisfy a checker.
    lines = []
    for line in (r.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line.split(":", 3)
        if len(parts) >= 3:
            try:
                src = pathlib.Path(parts[0]).read_text(
                    encoding="utf-8").splitlines()[int(parts[1]) - 1]
                if "noqa" in src:
                    continue
            except (OSError, ValueError, IndexError):
                pass
        lines.append(line)
    if lines:
        return fail("names", lines[:15])
    return ok("names (no undefined or unused)")


def check_docstrings() -> bool:
    """Every tool needs a docstring — it is the tool's interface.

    COUNTS FROM THE AST, NOT THE TEXT. This used to report the number of
    literal "@mcp.tool" strings in the package, which included the sentence in
    server.py explaining what the decorator does. The checker was counting its
    own documentation as a tool, and reported one too many from the day it was
    written — a reassuring number nobody re-derived. Parse the structure; do
    not grep the prose that describes the structure.
    """
    missing, n = [], 0
    for f in PKG.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            decorated = any(
                (isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool")
                or getattr(d, "attr", "") == "tool"
                for d in node.decorator_list)
            if not decorated:
                continue
            n += 1
            if not ast.get_docstring(node):
                missing.append(f"{f.name}:{node.lineno} {node.name}")
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


def _test_python() -> str:
    """The interpreter to run tests with.

    Prefers the project venv. The hook runs check.py under whatever `python3`
    is on PATH, and that interpreter does not have this package's dependencies
    — so every test module importing client was skipped at COLLECTION and the
    gate reported green over them. See check_tests.
    """
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def check_tests() -> bool:
    """Run the suite, and REFUSE TO PASS OVER TESTS THAT DID NOT RUN.

    A skipped module is not a neutral event here. tests/test_webauth.py covers
    a local listener that receives a bearer token; it needs httpx, and without
    it pytest skips the module and the summary still says "passed". The gate
    was running 114 of 142 tests and printing a confident green over the
    difference — the same shape as every other defect this project has hit:
    a check that reports success while silently not checking.
    """
    py = _test_python()
    r = subprocess.run([py, "-m", "pytest", "-q", "-rs", str(ROOT / "tests")],
                       capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
    last = lines[-1].strip() if lines else ""
    if r.returncode != 0:
        return fail("tests", lines[-15:])

    # "SKIPPED [1] tests/test_webauth.py:14: could not import 'httpx'"
    missing = sorted({m.group(1) for m in
                      (re.match(r"SKIPPED \[\d+\] ([^:]+):", l) for l in lines)
                      if m})
    if missing:
        return fail("tests", [
            f"{last}  — but these modules never ran:",
            *(f"    {m}" for m in missing),
            "",
            "  They are skipped because this interpreter lacks the package's",
            f"  dependencies ({py}).",
            "  A gate that skips its own security tests and prints green is",
            "  worse than no gate. Create the project venv once:",
            "",
            "    uv venv && uv pip install -e . pytest",
        ])
    where = "" if py == sys.executable else f"  [{pathlib.Path(py).parent.parent.name}]"
    return ok(f"tests — {last}{where}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fresh", action="store_true",
                    help="also resolve and import in a clean venv (slow, "
                         "downloads; catches broken dependency pins)")
    args = ap.parse_args()

    print(f"{DIM}sleeper-mcp local checks — {ROOT}{OFF}")
    results = [check_syntax(), check_names(), check_privacy(), check_secrets(),
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
