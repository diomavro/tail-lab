"""The one computed metric: VIX stretch — how extended the latest VIX close
is relative to its own trailing 20-day distribution.

This module is the orchestration point allowed to depend on both ``lake``
(to read bronze as-of a simulation date) and ``transforms`` (the pure
bronze -> silver -> gold functions) — see the layering rule in
``pyproject.toml``. The API layer calls only this function, never ``lake``
or ``transforms`` directly for VIX.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel

from tail_lab.lake.store import LakeStore
from tail_lab.transforms.vix import bronze_to_silver, silver_to_gold

DATASET = "vix"


class VixStretchResult(BaseModel):
    """The gold-layer VIX stretch metric for a single date."""

    date: dt.date
    close: float
    rolling_mean_20d: float
    rolling_std_20d: float
    z_score: float


def compute_vix_stretch(store: LakeStore, *, as_of: dt.date) -> VixStretchResult:
    """Point-in-time VIX stretch as of ``as_of``.

    Reads only the bronze snapshot known on or before ``as_of``
    (:meth:`LakeStore.read_bronze_as_of` enforces no-look-ahead), derives
    silver and gold from it, and returns the metric for the latest date
    present in that as-of read.

    Raises ``LookupError`` if no bronze snapshot exists on or before
    ``as_of``, or if fewer than the stretch window's worth of observations
    are available (the latest row's stats would be ``NaN``).
    """
    bronze = store.read_bronze_as_of(DATASET, as_of)
    silver = bronze_to_silver(bronze)
    gold = silver_to_gold(silver)

    latest = gold.iloc[-1]
    if bool(latest[["rolling_mean_20d", "rolling_std_20d"]].isna().any()):
        raise LookupError(
            "not enough trailing history as of "
            f"{as_of.isoformat()} to compute the VIX stretch metric"
        )
    if bool(latest[["z_score"]].isna().any()):
        # History is sufficient but the window is flat (std == 0) — the
        # z-score is genuinely undefined, not missing data.
        raise LookupError(
            "the VIX was flat over the trailing window as of "
            f"{as_of.isoformat()}; z-score is undefined (zero variance)"
        )

    return VixStretchResult(
        date=latest["date"].date(),
        close=float(latest["close"]),
        rolling_mean_20d=float(latest["rolling_mean_20d"]),
        rolling_std_20d=float(latest["rolling_std_20d"]),
        z_score=float(latest["z_score"]),
    )
