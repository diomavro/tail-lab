"""Pandera schema for the live options-expiration-dates dataset
(``docs/END_STATE.md`` §1.1/§1.2 — the OOM-put candidates need an accurate
listing cadence per underlying; ``AGENT_TODO.md``'s "live options-expiry
cadence adapter" item).

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

One row per (symbol, expiration_date) the underlying's chain lists as of the
ingest date -- the raw material ``transforms/options_expiry.py`` derives a
listing-cadence classification from. This is a distinct dataset from
``contracts/options_calendar.py``'s hand-maintained screening-universe table
(name, cadence label, curated tradable set) -- that table is *config*, not
ingested data; this schema is the *live, per-symbol chain snapshot* a future
increment can derive the same cadence label from.
"""

from __future__ import annotations

from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series


class OptionsExpiryRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver options-expiry rows."""

    symbol: Series[str] = pa.Field(nullable=False)
    expiration_date: Series[pa.Timestamp] = pa.Field(nullable=False)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["symbol", "expiration_date"]


OptionsExpirySchema = OptionsExpiryRowSchema.to_schema()


def dataset_id(symbol: str) -> str:
    """The bronze dataset id for ``symbol``'s expiration-date chain -- one
    dataset per symbol, mirroring ``contracts/ohlcv.py``'s per-symbol shape."""
    return f"options_expiry_{symbol.lower()}"
