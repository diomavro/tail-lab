"""Pandera schema for the rest of the volatility complex
(`docs/DATA_CONTRACTS.md` #2) — VIX3M, VIX9D, VVIX and SKEW.

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

Spot VIX itself is `contracts/vix.py`'s own close-only dataset, ingested
first and already widely read (`research/vix_stretch.py`, the regime
timeline, the dashboard tile). This module deliberately does NOT touch
that dataset or its shape: it fills in the rest of dataset #2's family as
a second, independent bronze dataset, long-format and keyed by
``(series, trade_date)``, mirroring ``contracts/cboe_strategy.py`` — the
whole family is fetched from the same Cboe CDN host and read together as
one panel. Unifying spot VIX into this same dataset (and widening it to
OHLC) is a separate, larger increment that touches existing consumers
(`AGENT_TODO.md`); this one does not.

Unlike spot VIX's CSV, not every file in this family carries OHLC: Cboe
serves VIX3M/VIX9D as ``DATE,OPEN,HIGH,LOW,CLOSE`` but VVIX/SKEW as a bare
``DATE,<TICKER>`` (confirmed live 2026-09-07). ``open``/``high``/``low``
are therefore nullable; ``close`` is the one column every series always
has.
"""

from __future__ import annotations

from typing import ClassVar, cast

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

#: Bounds are deliberately per-series and wide: they exist to catch unit
#: errors (e.g. a level fed in cents) and garbled feed rows, not to express
#: a view on how far a genuine future print may run.
SERIES_BOUNDS: dict[str, tuple[float, float]] = {
    # VIX3M/VIX9D price the same underlying volatility surface as spot VIX
    # at different tenors and have historically shared its order of
    # magnitude, so they share spot VIX's own band (`contracts/vix.py`).
    "VIX3M": (0.0, 200.0),
    "VIX9D": (0.0, 200.0),
    # VVIX (vol-of-vol) has historically traded in a higher, spikier range
    # than the VIX family itself -- e.g. the Feb-2018 "volmageddon" spike --
    # so its ceiling is set higher.
    "VVIX": (0.0, 300.0),
    # SKEW is constructed to center near 100 and has historically ranged
    # roughly 100-170; the band is widened on both sides to avoid rejecting
    # a genuine tail print while still catching a garbled feed.
    "SKEW": (50.0, 250.0),
}

#: The series this dataset knows how to ingest -- deliberately excludes
#: "VIX" itself, which stays `contracts/vix.py`'s dataset.
SERIES_NAMES: tuple[str, ...] = tuple(SERIES_BOUNDS)

#: The single bronze dataset the whole family lands in.
DATASET = "vix_complex"


class VixComplexRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver vix-complex rows."""

    series: Series[str] = pa.Field(nullable=False, isin=SERIES_NAMES)
    trade_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    open: Series[float] = pa.Field(nullable=True)
    high: Series[float] = pa.Field(nullable=True)
    low: Series[float] = pa.Field(nullable=True)
    close: Series[float] = pa.Field(nullable=False)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["series", "trade_date"]

    @pa.dataframe_check
    @classmethod
    def close_within_series_bounds(cls, df: pd.DataFrame) -> Series[bool]:
        """Each row's ``close`` must sit inside its own series' band --
        one shared range would either reject a legitimate SKEW print
        (~100-170) or silently accept a VIX3M value ten times too large."""
        lo = df["series"].map(lambda s: SERIES_BOUNDS[s][0]).astype(float)
        hi = df["series"].map(lambda s: SERIES_BOUNDS[s][1]).astype(float)
        mask = (df["close"] >= lo) & (df["close"] <= hi)
        return cast(Series[bool], mask)


VixComplexSchema = VixComplexRowSchema.to_schema()


def empty_vix_complex_frame() -> pd.DataFrame:
    """A correctly-typed zero-row frame, so an empty source still validates."""
    return pd.DataFrame(
        {
            "series": pd.Series([], dtype="object"),
            "trade_date": pd.Series([], dtype="datetime64[ns]"),
            "open": pd.Series([], dtype="float64"),
            "high": pd.Series([], dtype="float64"),
            "low": pd.Series([], dtype="float64"),
            "close": pd.Series([], dtype="float64"),
        }
    )
