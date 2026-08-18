"""Pandera schema for the underlying OHLCV dataset (`docs/DATA_CONTRACTS.md` #1).

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

The same shape is validated at both the bronze and silver stage, mirroring
``contracts/vix.py``. Bronze validation is used by ``ingestion`` to split
incoming rows into "valid" (committed to the lake) and "quarantined"
(written to a separate quarantine partition, never silently dropped).
"""

from __future__ import annotations

from typing import ClassVar, cast

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series


class OhlcvRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver (cleaned) OHLCV rows."""

    symbol: Series[str] = pa.Field(nullable=False)
    trade_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    open: Series[float] = pa.Field(nullable=False, gt=0)
    high: Series[float] = pa.Field(nullable=False, gt=0)
    low: Series[float] = pa.Field(nullable=False, gt=0)
    close: Series[float] = pa.Field(nullable=False, gt=0)
    volume: Series[int] = pa.Field(nullable=False, ge=0)
    adj_close: Series[float] = pa.Field(nullable=False, gt=0)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["symbol", "trade_date"]

    @pa.dataframe_check
    @classmethod
    def low_is_the_days_floor(cls, df: pd.DataFrame) -> Series[bool]:
        """``low <= open, close <= high`` for every bar — a bar where the
        low/high don't bracket the open/close is a garbled feed row, not a
        legitimate (if unusual) trading day."""
        mask = (
            (df["low"] <= df["open"])
            & (df["low"] <= df["close"])
            & (df["high"] >= df["open"])
            & (df["high"] >= df["close"])
            & (df["low"] <= df["high"])
        )
        return cast(Series[bool], mask)


OhlcvSchema = OhlcvRowSchema.to_schema()


def dataset_id(symbol: str) -> str:
    """The bronze dataset id for ``symbol`` — one dataset per symbol.

    Part of the OHLCV dataset's contract (its lake identity), so it lives in
    this leaf layer where both the writer (``ingestion``) and the readers
    (``research``) can import it — ``research`` may not import ``ingestion``
    (``pyproject.toml`` forbidden contract / ``ARCHITECTURE.md``)."""
    return f"ohlcv_{symbol.lower()}"
