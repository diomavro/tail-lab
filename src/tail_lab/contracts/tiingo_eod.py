"""Pandera schema for Tiingo's end-of-day rows (`docs/DATA_CONTRACTS.md` #14).

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

One row is one symbol's session as Tiingo serves it. Bronze keeps the
source's own shape -- the as-traded ``close``, Tiingo's dividend-and-split
adjusted ``adj_close``, the cash dividend paid on that ex-date and the split
factor effective that day -- so every derived number (the dividend yield
``q``, a total-return index) is computed at read time from what the vendor
actually said, never from a stored derivation.

Why as-traded ``close`` matters: a dividend is paid per share *as traded on
its ex-date*. Dividing it by this dataset's own as-traded close keeps the
yield on one basis without reconciling against another dataset's split
adjustments (bronze OHLCV is split-adjusted as of its fetch date, so a
cross-dataset ratio is off by the split ratio around every split).

The whole universe lands in one dataset, keyed ``(symbol, trade_date)``,
following the chain sweep's one-partition-for-all-symbols precedent rather
than the per-symbol ``ohlcv_<sym>`` layout: a weekly run fetches every symbol
together, and an as-of read takes exactly one partition, so a per-symbol
write would let one failed symbol hide the rest.
"""

from __future__ import annotations

from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series

#: The single bronze dataset the whole universe lands in.
DATASET = "tiingo_eod"


class TiingoEodRowSchema(pa.DataFrameModel):
    """Shape of one bronze Tiingo EOD row.

    ``div_cash`` is zero on every day that is not an ex-date (Tiingo's own
    convention) and is never negative. ``split_factor`` is 1.0 on every day
    without a split; a 4:1 split is 4.0. Prices are strictly positive: a
    zero or negative print is a feed error, never a quote, and so is an
    infinite one (JSON allows ``Infinity``; an infinite close would read as a
    silent ``q = 0``).
    """

    symbol: Series[str] = pa.Field(nullable=False, str_matches=r"^[a-z][a-z0-9.\-]*$")
    trade_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    close: Series[float] = pa.Field(nullable=False, gt=0.0, lt=float("inf"))
    adj_close: Series[float] = pa.Field(nullable=False, gt=0.0, lt=float("inf"))
    div_cash: Series[float] = pa.Field(nullable=False, ge=0.0, lt=float("inf"))
    split_factor: Series[float] = pa.Field(nullable=False, gt=0.0, lt=float("inf"))

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["symbol", "trade_date"]


TiingoEodSchema = TiingoEodRowSchema.to_schema()
