"""Every tool tells the client whether it changes anything.

A client reads these hints to decide when to ask the user before a call. A
missing hint is the client's guess, and a wrong readOnlyHint on a write tool
lets a lineup change through without a prompt. The docstring convention
("WRITE." first) is what a human reads; this test keeps the two in step.
"""

import pytest

pytest.importorskip("mcp", reason="MCP SDK not installed; run check.py --fresh")

import sleeper_mcp.server  # noqa: E402,F401  (registers every tool)
from sleeper_mcp.client import mcp  # noqa: E402

# Change state but do not start their docstring with "WRITE.": trade_block
# reads when called with no arguments, setup_token starts a local page that
# saves a token.
ALSO_WRITES = {"trade_block", "setup_token"}


@pytest.fixture(scope="module")
async def tools():
    return await mcp.list_tools()


def hints(t):
    """The wire names. mcp 1.x fields are camelCase, 2.x snake_case with
    camelCase aliases; by_alias gives the same keys from both."""
    return t.annotations.model_dump(by_alias=True)


def is_write(t):
    return (t.description or "").startswith("WRITE.") or t.name in ALSO_WRITES


async def test_every_tool_has_annotations(tools):
    missing = [t.name for t in tools if t.annotations is None]
    assert not missing, f"no annotations: {missing}"


async def test_every_tool_talks_to_sleeper(tools):
    assert all(hints(t)["openWorldHint"] is True for t in tools)


async def test_reads_say_they_are_read_only(tools):
    wrong = [t.name for t in tools
             if not is_write(t) and hints(t)["readOnlyHint"] is not True]
    assert not wrong, f"read tools not marked readOnlyHint: {wrong}"


async def test_writes_say_what_kind_of_write(tools):
    wrong = [t.name for t in tools if is_write(t) and (
        hints(t)["readOnlyHint"] is not False
        or hints(t).get("destructiveHint") is None
        or hints(t).get("idempotentHint") is None)]
    assert not wrong, f"write tools with incomplete hints: {wrong}"


async def test_the_write_list_is_not_stale(tools):
    names = {t.name for t in tools}
    assert ALSO_WRITES <= names, f"gone: {ALSO_WRITES - names}"
