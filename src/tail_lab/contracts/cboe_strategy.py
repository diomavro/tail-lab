"""Pandera schema for the Cboe option-strategy benchmark indices
(`docs/DATA_CONTRACTS.md` #7, `docs/DATA_SOURCING.md` §9.1).

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

These indices are the platform's *real-quote* benchmark for a put-buying
tail strategy. Cboe's published methodology prices each roll at the
volume-weighted average of actual OPRA transaction prices (falling back
to the last reported ask when the strike does not trade), so an index
level here is the marked value of a real, executed option program — not
model output. That is what makes them the yardstick the model-priced
backtester (`docs/adr/0004`) is scored against.

The shape mirrors ``contracts/vix.py``'s vol complex: one long-format
dataset keyed by ``(index_symbol, trade_date)`` rather than one dataset
per ticker, because the whole family is fetched together, used together
as a panel, and shares an identical two-column source format.
"""

from __future__ import annotations

from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series

#: Bounds for a strategy-index level. These are NAV-style levels rebased to
#: 100 at inception, so the floor is a hard 0 (a non-positive index level is
#: always a garbled row) while the ceiling is deliberately loose: it exists
#: to catch unit errors and feed garbage, not to express a view on how far
#: a compounding index may run. PPUT sat at ~2,200 in Aug 2026 after 40
#: years, so 1e6 is many multiples above anything reachable in practice.
INDEX_LEVEL_MIN = 0.0
INDEX_LEVEL_MAX = 1_000_000.0

#: The catalogue of Cboe strategy/benchmark indices this adapter knows how
#: to ingest, mapped to a human label. Every entry was probed live on
#: 2026-08-21 (HTTP 200, current through 2026-08-20) — see
#: `docs/DATA_SOURCING.md` §9.1 for the measured first-observation dates.
#: Extending this dict is the whole cost of ingesting another index: the
#: source format is identical across the family.
STRATEGY_INDEX_CATALOGUE: dict[str, str] = {
    # --- long-put / tail-hedge programs: the direct benchmarks for S1 ---
    "PPUT": "Cboe S&P 500 5% Put Protection Index (from 1986-06-30)",
    "PPUT3M": "Cboe S&P 500 Tail Risk Index (from 2004-03-19)",
    "VXTH": "Cboe VIX Tail Hedge Index (from 2006-03-31)",
    "LTV": "Cboe S&P 500 Left Tail Volatility Index (from 2006-01-03)",
    # --- collars: long put financed by a short call, the cost-reduced cousin ---
    # CLL's CDN file starts 2008-08-26 even though the index itself is quoted
    # back to 1986 elsewhere; use CLLZ when pre-crisis collar history matters.
    "CLL": "Cboe S&P 500 95-110 Collar Index (CDN file from 2008-08-26)",
    "CLL3M": "Cboe S&P 500 3-Month Collar 95-110 Index (from 2004-03-19)",
    "CLLZ": "Cboe S&P 500 Zero-Cost Put Spread Collar Index (from 1986-06-20)",
    "CLLR": "Cboe Russell 2000 Zero-Cost Put Spread Collar Index (from 2001-01-31)",
    # --- putwrite: the short-put side, i.e. what the hedger pays away ---
    "PUT": "Cboe S&P 500 PutWrite Index (CDN file from 1991-03-04)",
    "PUTY": "Cboe S&P 500 2% OTM PutWrite Index (from 1986-06-30)",
    # --- the unhedged benchmark every program above is measured against ---
    "SPX": "S&P 500 Index, price return (from 1975-01-02)",
}

#: What a plain ``make ingest-cboe-strategy`` pulls. Deliberately the
#: tail-relevant subset plus SPX rather than the whole catalogue: each
#: ticker is one HTTP request against a public CDN, and the Russell collar
#: (CLLR) is a different underlying that no current research question uses.
DEFAULT_TICKERS: tuple[str, ...] = (
    "PPUT",
    "PPUT3M",
    "VXTH",
    "LTV",
    "CLL",
    "CLL3M",
    "CLLZ",
    "PUT",
    "PUTY",
    "SPX",
)

#: The single bronze dataset the whole family lands in.
DATASET = "cboe_strategy"


class CboeStrategyRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver strategy-index rows."""

    index_symbol: Series[str] = pa.Field(nullable=False)
    trade_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    close: Series[float] = pa.Field(nullable=False, gt=INDEX_LEVEL_MIN, le=INDEX_LEVEL_MAX)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["index_symbol", "trade_date"]


CboeStrategySchema = CboeStrategyRowSchema.to_schema()
