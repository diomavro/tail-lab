"""Pandera schema for the FRED credit-spread dataset (`docs/DATA_CONTRACTS.md` #4).

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

Same ``(series_id, obs_date, vintage_date)`` long-format shape as
``contracts/rates.py`` — kept as a separate contract, not a shared schema,
because the two datasets are consumed by different research code and may
diverge in validation constraints later (`docs/DATA_CONTRACTS.md` #4): an
option-adjusted spread is non-negative by construction, a Treasury yield
is not, so the two schemas already disagree on their floor.
"""

from __future__ import annotations

from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series

#: Bounds for an option-adjusted spread, expressed in percent. The floor is
#: 0.0 -- an OAS is non-negative by construction, unlike a Treasury yield
#: (`docs/DATA_CONTRACTS.md` #4) -- so a negative print here is a unit/parse
#: error, not a real value. The ceiling is far above the 2008 HY OAS peak
#: (~19.9%) and exists to catch unit errors (e.g. a spread fed in bps)
#: rather than to express a view on how wide spreads could blow out.
SPREAD_MIN = 0.0
SPREAD_MAX = 50.0

#: The FRED series this adapter knows how to ingest: HY OAS and IG OAS
#: (`docs/DATA_CONTRACTS.md` #4).
DEFAULT_SERIES_IDS: tuple[str, ...] = (
    "BAMLH0A0HYM2",
    "BAMLC0A0CM",
)

#: The single bronze dataset the whole family lands in.
DATASET = "credit"


class CreditRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver credit rows."""

    series_id: Series[str] = pa.Field(nullable=False)
    obs_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    value: Series[float] = pa.Field(nullable=False, ge=SPREAD_MIN, le=SPREAD_MAX)
    vintage_date: Series[pa.Timestamp] = pa.Field(nullable=False)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["series_id", "obs_date", "vintage_date"]


CreditSchema = CreditRowSchema.to_schema()
