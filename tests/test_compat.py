"""The SDK compatibility shim. Pure — no network.

These assert the CONTRACT the shim relies on, so if a future SDK breaks it the
test says which assumption died rather than leaving an ImportError to decode.
"""

import inspect

import pytest

# Unlike the rest of the suite, these NEED the SDK installed — they are about
# the SDK. Skip rather than error where it is absent, so the pure tests still
# run on a bare interpreter. `check.py --fresh` is what actually proves the
# shim against a real install.
pytest.importorskip("mcp", reason="MCP SDK not installed; run check.py --fresh")

from sleeper_mcp.client import _Server, mcp  # noqa: E402


def test_a_server_class_was_resolved():
    """Either name is fine; having neither is not."""
    assert _Server.__name__ in ("MCPServer", "FastMCP")


def test_the_surface_we_actually_use_exists():
    for attr in ("tool", "run", "list_tools"):
        assert hasattr(_Server, attr), f"SDK no longer exposes {attr}"


def test_run_is_sync_and_list_tools_is_async():
    """True on both mcp 1.x and 2.x. If either flips, server.py breaks."""
    assert not inspect.iscoroutinefunction(_Server.run)
    assert inspect.iscoroutinefunction(_Server.list_tools)


def test_the_shared_instance_is_that_class():
    assert isinstance(mcp, _Server)
