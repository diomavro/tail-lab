"""Pandera schema for point-in-time S&P 500 constituents
(`docs/DATA_CONTRACTS.md` #9, `docs/adr/0010`).

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

Today's screening universe is "current constituents," which is
survivorship-biased by construction — it omits exactly the names that blew
up and delisted, the most tail-sensitive assets of all (`docs/adr/0010`,
`docs/END_STATE.md` §2.3). This dataset closes that gap at membership
granularity: for any historical date, which tickers were actually in the
index.

**Shape.** One row per observation date, carrying the *full* membership list
for that date as a single comma-joined string — the source's own shape
(`fja05680/sp500`'s "Historical Components & Changes" CSV), not exploded to
one row per (date, ticker). Observation dates are irregular (roughly weekly,
sometimes months apart when nothing changed) rather than one per trading
day: the source republishes the full list only when it moves, so consecutive
identical rows are not guaranteed to exist. A caller resolving membership
"as of" some historical date takes the *latest observation date on or before
it* and splits that row's ``tickers`` field — mirroring how
:meth:`tail_lab.lake.store.LakeStore.read_bronze_as_of` already resolves the
latest *ingest* snapshot on or before a request date, one level up. Exploding
to long format would cost ~1.3M rows for ~500 tickers x ~2,700 observation
dates with no benefit before a research consumer exists to justify it.
"""

from __future__ import annotations

from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series

#: The single bronze dataset this adapter writes.
DATASET = "sp500_constituents"


class Sp500ConstituentsRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver constituent rows."""

    obs_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    tickers: Series[str] = pa.Field(nullable=False, str_length={"min_value": 1})

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["obs_date"]


Sp500ConstituentsSchema = Sp500ConstituentsRowSchema.to_schema()
