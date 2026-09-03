"""Pandera schema for the VIX futures term structure (`docs/DATA_CONTRACTS.md` #11).

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

One row is one contract's settlement on one trading day. The dataset is
long-format, keyed by ``(contract_expiry, trade_date)`` rather than one
dataset per contract-month, mirroring ``contracts/cboe_strategy.py``: the
whole listed curve is fetched together and read together as a panel (the
constant-maturity term structure a future ``transforms/`` consumer builds),
not one contract at a time.

``contract_expiry`` -- not a ticker or month code -- is the join key. VX
contract months are otherwise ambiguous punctuation (Cboe's own CSVs label a
contract ``"U (Sep 2026)"``, a free-text field this schema does not carry);
the settlement date is the one unambiguous identity a contract has.
"""

from __future__ import annotations

from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series

#: Bounds for a VX settlement price. VIX itself has never printed above ~90
#: (2008/2020 peaks) or traded at exactly zero, and a futures settle tracks
#: the same underlying index -- generous headroom above the historical peak
#: catches unit/feed errors without expressing a view on how high VIX could
#: someday spike.
SETTLE_MIN = 0.0
SETTLE_MAX = 300.0

#: The single bronze dataset the whole listed curve lands in.
DATASET = "vix_futures"


class VxFuturesRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver VX futures rows.

    ``open``/``high``/``low``/``close`` are nullable: Cboe zero-fills them on
    a day the contract did not trade (a real futures price is never exactly
    $0.00), so the adapter maps that sentinel to a typed absence before this
    schema ever sees the row -- the same convention `ingestion/option_chain.py`
    already uses for its greeks. ``settle`` is the exchange's own computed
    settlement price and is never zero-filled, so it stays non-nullable.
    """

    contract_expiry: Series[pa.Timestamp] = pa.Field(nullable=False)
    trade_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    open: Series[float] = pa.Field(nullable=True, gt=SETTLE_MIN, le=SETTLE_MAX)
    high: Series[float] = pa.Field(nullable=True, gt=SETTLE_MIN, le=SETTLE_MAX)
    low: Series[float] = pa.Field(nullable=True, gt=SETTLE_MIN, le=SETTLE_MAX)
    close: Series[float] = pa.Field(nullable=True, gt=SETTLE_MIN, le=SETTLE_MAX)
    settle: Series[float] = pa.Field(nullable=False, gt=SETTLE_MIN, le=SETTLE_MAX)
    volume: Series[int] = pa.Field(nullable=False, ge=0)
    open_interest: Series[int] = pa.Field(nullable=False, ge=0)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["contract_expiry", "trade_date"]


VxFuturesSchema = VxFuturesRowSchema.to_schema()
