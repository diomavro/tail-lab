"""Pandera schemas for the VIX dataset.

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

The same shape (date, close) is validated at both the bronze and silver
stage. Bronze validation is used by ``ingestion`` to split incoming rows
into "valid" (committed to the lake) and "quarantined" (written to a
separate quarantine partition, never silently dropped).
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.typing import Series

#: Sane bounds for the VIX index. VIX has never closed below ~9 or above
#: ~90 in its history (the all-time intraday high was ~89.5 in Oct 2008 /
#: Mar 2020); a wide-but-finite band catches unit errors (e.g. a price fed
#: in cents) and garbled feed rows without being so tight it rejects a
#: genuine future spike.
VIX_MIN = 0.0
VIX_MAX = 200.0


class VixRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver (cleaned) VIX rows."""

    date: Series[pa.Timestamp] = pa.Field(nullable=False, unique=True)
    close: Series[float] = pa.Field(nullable=False, ge=VIX_MIN, le=VIX_MAX)

    class Config:
        coerce = True
        strict = True


VixSchema = VixRowSchema.to_schema()
