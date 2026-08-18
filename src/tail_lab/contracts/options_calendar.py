"""Static options-listing cadence per symbol (``docs/END_STATE.md`` §1.2 —
"how long between expiries").

A LEAF module: imports nothing else from ``tail_lab`` (the import-linter
layers contract keeps ``contracts`` at the bottom). Every other layer may
import it.

This is a hand-maintained table *for now*. The listing cadence of an option
chain (weeklies vs monthlies-only, average gap between expiries) is a slow-
moving, public property of each product, so a small static table is honest
and useful today. A live keyless adapter that reads Yahoo's
``/v7/finance/options`` expiration dates and derives these gaps is a queued
follow-up (``AGENT_TODO.md``); until then the values below are the contract,
and `cadence_for` falls back to a conservative weekly default for anything
not listed rather than inventing a number.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Cadence = Literal["weekly", "monthly"]


class OptionsCadence(BaseModel):
    """The expiry cadence of one underlying's listed option chain."""

    symbol: str
    cadence: Cadence
    avg_gap_days: float
    label: str
    detail: str


# Keyed by upper-case ticker. Gaps are the *effective* average spacing of
# listed expiries a retail chain shows, not a promise about any single week.
_CALENDAR: dict[str, OptionsCadence] = {
    "SPY": OptionsCadence(
        symbol="SPY",
        cadence="weekly",
        avg_gap_days=2.5,
        label="Weeklies + monthlies",
        detail="Mon/Wed/Fri weeklies plus monthly & quarterly",
    ),
    "QQQ": OptionsCadence(
        symbol="QQQ",
        cadence="weekly",
        avg_gap_days=3.5,
        label="Weeklies",
        detail="Every Friday plus monthlies",
    ),
    "IWM": OptionsCadence(
        symbol="IWM",
        cadence="weekly",
        avg_gap_days=3.5,
        label="Weeklies",
        detail="Every Friday plus monthlies",
    ),
    "TSLA": OptionsCadence(
        symbol="TSLA",
        cadence="weekly",
        avg_gap_days=3.5,
        label="Weeklies",
        detail="Every Friday, deep liquid chain",
    ),
    "GLD": OptionsCadence(
        symbol="GLD",
        cadence="monthly",
        avg_gap_days=7.0,
        label="Monthlies",
        detail="Third-Friday monthlies, some weeklies",
    ),
    "EEM": OptionsCadence(
        symbol="EEM",
        cadence="monthly",
        avg_gap_days=11.0,
        label="Monthlies",
        detail="Third-Friday monthlies, thin weeklies",
    ),
}

#: Fallback for a symbol not in the table — a conservative weekly default,
#: flagged as such so a caller never mistakes it for a verified listing.
_DEFAULT = OptionsCadence(
    symbol="?",
    cadence="weekly",
    avg_gap_days=3.5,
    label="Weeklies (assumed)",
    detail="No verified listing on file; weekly default assumed",
)


def cadence_for(symbol: str) -> OptionsCadence:
    """Listing cadence for ``symbol`` (case-insensitive), or a flagged weekly
    default if it isn't in the static table."""
    found = _CALENDAR.get(symbol.upper())
    if found is not None:
        return found
    return _DEFAULT.model_copy(update={"symbol": symbol.upper()})
