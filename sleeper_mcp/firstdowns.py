"""Projected first downs. Sleeper's are a placeholder, so rebuild them.

Sleeper's weekly projections set every first-down count to yards / 10:

    rec  4.15   rec_yd 62.75   rec_fd 6.28     more first downs than catches
    rush_att 16.77   rush_yd 71.51   rush_fd 7.15

The match is exact to two decimals for 44,533 of 44,533 projected values,
2022 to 2026 (measured 2026-10-23). Real players convert about 0.04 first
downs per yard, so the stub runs about 2.4x the real rate. In a league that
pays per first down, it adds about 1.5 points to a back's projected week and 3
to a quarterback's, and more to the high-volume players a lineup is built on.

Actuals (`category:"stat"`) carry real counts. Only projections come here.

THE FIX IS ONE RATE PER YARD, BY POSITION. The rates are fit on 46,801
player-weeks of 2022-2025 actuals and tested on 2026 weeks 1-6, which the fit
never saw. They remove the bias and cut the error about in half. Three richer
models did no better on 2026: a rate per touch, yards plus touches, and each
player's own previous-season rate shrunk toward his position. The weekly error
comes from the projected VOLUME, and no conversion rate can fix that.

Only a value that matches the stub is replaced. If Sleeper ever ships real
first-down projections, they pass through and this module does nothing.

No imports: scripts outside the server use it without the MCP dependencies.
"""

from __future__ import annotations

# First downs per yard: sum(first downs) / sum(yards) over every 2022-2025
# player-week. A position with too few events to fit (a tight end's carries,
# a back's passes) uses the default for that kind.
PER_YARD = {
    "rec_fd": {"RB": 0.0389, "WR": 0.0415, "TE": 0.0434},
    "rush_fd": {"QB": 0.0685, "RB": 0.0448, "WR": 0.0461},
    "pass_fd": {"QB": 0.0415},
}
DEFAULT = {"rec_fd": 0.0415, "rush_fd": 0.0448, "pass_fd": 0.0415}
YARDS = {"rec_fd": "rec_yd", "rush_fd": "rush_yd", "pass_fd": "pass_yd"}

# Sleeper rounds to two decimals, so a stub is never more than 0.005 from
# yards / 10. The largest measured difference was 0.005 plus float noise.
_STUB_TOLERANCE = 0.006


def fd_from_yards(stats: dict, position: str | None) -> dict:
    """A copy of one projection row, with each stubbed first-down count rebuilt.

    A count is a stub when it equals its yards / 10. It becomes yards x the
    position's rate, and never less than zero (a quarterback can project
    negative rushing yards). Every other key passes through unchanged.
    """
    out = dict(stats)
    for fd, yd in YARDS.items():
        f, y = stats.get(fd), stats.get(yd)
        if not isinstance(f, (int, float)) or not isinstance(y, (int, float)):
            continue
        if abs(f - y / 10) > _STUB_TOLERANCE:
            continue
        out[fd] = max(0.0, y * PER_YARD[fd].get(position or "", DEFAULT[fd]))
    return out


def fd_note(scoring: dict) -> str:
    """One output line for a league that pays first downs, else ""."""
    if not any(scoring.get(k) for k in YARDS):
        return ""
    return ("  first downs: Sleeper projects them as yards/10, about 2.4x the "
            "real rate; these totals use yards x a measured rate per position")
