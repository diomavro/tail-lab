"""Pandera schema for the FRED rates dataset (`docs/DATA_CONTRACTS.md` #3).

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

One long-format dataset keyed by ``(series_id, obs_date, vintage_date)`` --
mirrors ``contracts/cboe_strategy.py``'s shape, because the whole series
family is fetched together and read together as a panel. ``vintage_date``
is FRED's own ``realtime_start`` for the observation, not the
``ingest_date`` Delta partition every dataset already carries: Treasury/
SOFR/fed-funds levels are practically never revised, but a backfill that
pulls full ALFRED vintage history in one ingest could still carry more
than one row per ``obs_date`` for a series that *was* revised, and only a
per-row ``vintage_date`` -- not the physical ``ingest_date`` the whole
partition shares -- lets an as-of read pick the value actually known on a
given simulation date (`docs/adr/0009`).
"""

from __future__ import annotations

from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series

#: Bounds for a rate level, expressed in percent (FRED's native units for
#: these series, e.g. 4.33 for 4.33%). The floor allows for the rare
#: genuine negative print (short-term repo/SOFR stress) without being
#: unbounded; the ceiling is far above the early-1980s fed funds peak
#: (~20%) and exists to catch unit errors (e.g. a rate fed in bps) rather
#: than to express a view on how high a rate could go.
RATE_MIN = -5.0
RATE_MAX = 25.0

#: The FRED series this adapter knows how to ingest: the Treasury curve,
#: SOFR, and the fed funds effective rate (`docs/DATA_CONTRACTS.md` #3).
DEFAULT_SERIES_IDS: tuple[str, ...] = (
    "DGS1MO",
    "DGS3MO",
    "DGS6MO",
    "DGS1",
    "DGS2",
    "DGS5",
    "DGS10",
    "DGS30",
    "SOFR",
    "DFF",
)

#: The single bronze dataset the whole family lands in.
DATASET = "rates"


class RatesRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver rates rows."""

    series_id: Series[str] = pa.Field(nullable=False)
    obs_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    value: Series[float] = pa.Field(nullable=False, ge=RATE_MIN, le=RATE_MAX)
    vintage_date: Series[pa.Timestamp] = pa.Field(nullable=False)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["series_id", "obs_date", "vintage_date"]


RatesSchema = RatesRowSchema.to_schema()
