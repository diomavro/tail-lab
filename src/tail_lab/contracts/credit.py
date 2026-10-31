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

import datetime as dt
from typing import ClassVar

import numpy as np
import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

#: Bounds for an option-adjusted spread, expressed in percent. The floor is
#: 0.0 -- an OAS is non-negative by construction, unlike a Treasury yield
#: (`docs/DATA_CONTRACTS.md` #4) -- so a negative print here is a unit/parse
#: error, not a real value. The ceiling is far above the 2008 HY OAS peak
#: (above 20%) and exists to catch unit errors (e.g. a spread fed in bps)
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

#: The one-off, immutable HY OAS history (docs/adr/0034): same schema, its own
#: bronze dataset. A pinned Wayback capture of FRED's pre-window full history.
ARCHIVE_DATASET = "credit_archive"
#: HY OAS: the only series anything reads (IG is ingested, never consumed).
HY_SERIES_ID = "BAMLH0A0HYM2"
ARCHIVE_SERIES_ID = HY_SERIES_ID
ARCHIVE_URL = (
    "https://web.archive.org/web/20251104204105id_/"
    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2"
)
#: sha256 of the DECODED response body; the ingest accepts nothing else.
ARCHIVE_SHA256 = "d8506703bd3464bfdad7c56d3b4383ddb542c6897df5cceea7ae6331b0b43b49"
#: The capture date, stamped on every archive row as ``vintage_date``.
ARCHIVE_VINTAGE = dt.date(2025, 11, 4)

#: Bronze partitions to drop from every credit read, keyed
#: ``(dataset, ingest_date)`` so it covers ``credit`` and ``credit_archive``.
#: Recovering a bad partition is one reviewed PR adding it here; the lake is
#: never edited. Never ignore the archive partition without a replacement:
#: history would shrink back to the live union (docs/adr/0034).
IGNORED_BRONZE_PARTITIONS: frozenset[tuple[str, dt.date]] = frozenset()


def _newest_per_obs(frame: pd.DataFrame) -> pd.Series:
    """One value per ``obs_date``: the newest ``ingest_date`` wins and
    ``vintage_date`` breaks ties only within one partition. Sorted explicitly,
    never by trusting scan order."""
    ordered = frame.sort_values(["obs_date", "ingest_date", "vintage_date"], kind="stable")
    last = ordered.drop_duplicates(subset="obs_date", keep="last")
    return pd.Series(
        last["value"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(last["obs_date"]),
        name="hy_oas",
    )


def resolve_credit_history(live: pd.DataFrame, archive: pd.DataFrame) -> pd.Series:
    """The HY OAS series: the archive for ``obs_date <= archive_end``, live after.

    ``archive_end`` is the archive's maximum ``obs_date``, read from the data.
    Both frames carry ``obs_date, value, vintage_date, ingest_date`` and are
    reduced newest-partition-wins first. With no archive rows live serves
    everything. A live partition therefore cannot touch the archive's span.
    """
    live_series = _newest_per_obs(live)
    if archive.empty:
        return live_series
    archive_series = _newest_per_obs(archive)
    archive_end = archive_series.index.max()
    after = live_series.loc[live_series.index > archive_end]
    joined = pd.Series(
        np.concatenate([archive_series.to_numpy(), after.to_numpy()]),
        index=archive_series.index.append(after.index),
        name="hy_oas",
    )
    return joined.sort_index()


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
