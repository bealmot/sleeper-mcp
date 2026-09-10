"""The signal-file contract. Pure — no network, no MCP."""

import pytest

from sleeper_mcp.optimizer import percentile_within as _pct_within


def test_percentile_is_scale_free():
    """Any units work: only the ORDER within a position matters."""
    counts = {"a": 2, "b": 9, "c": 40}
    ratings = {"a": 0.1, "b": 0.5, "c": 0.99}
    assert _pct_within(counts) == _pct_within(ratings)


def test_ties_share_a_rank():
    out = _pct_within({"a": 5, "b": 5, "c": 9})
    assert out["a"] == out["b"] < out["c"]


def test_endpoints_span_the_range():
    out = _pct_within({"low": 1, "mid": 2, "high": 3})
    assert out["low"] == 0.0 and out["high"] == 100.0


def test_single_entry_does_not_divide_by_zero():
    assert _pct_within({"only": 7}) == {"only": 0.0}


def test_empty_is_empty():
    assert _pct_within({}) == {}
