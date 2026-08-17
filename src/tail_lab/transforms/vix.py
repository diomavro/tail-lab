"""Bronze -> silver -> gold pure functions for the VIX dataset.

Pure by design: every function here takes DataFrame(s) in and returns a
DataFrame out. Nothing in this module touches the filesystem, the network,
or a ``LakeStore`` — orchestration (reading bronze as-of, calling these,
persisting the result) lives in ``research``, which is allowed to import
both this module and ``lake`` under the layering rule.
"""

from __future__ import annotations

import pandas as pd

from tail_lab.contracts.vix import VixSchema

#: Trailing window (trading days) for the realized-mean / stretch z-score.
STRETCH_WINDOW = 20


def bronze_to_silver(df: pd.DataFrame) -> pd.DataFrame:
    """Clean + validate a bronze VIX frame into silver: sorted, deduplicated,
    contract-typed. Raises if the frame does not satisfy the VIX contract —
    bronze rows are expected to already be valid (ingestion quarantines bad
    rows before they reach bronze), so a silver-stage failure signals a bug."""
    cleaned = (
        df.sort_values("date").drop_duplicates(subset="date", keep="last").reset_index(drop=True)
    )
    return VixSchema.validate(cleaned, lazy=True)


def silver_to_gold(df: pd.DataFrame, *, window: int = STRETCH_WINDOW) -> pd.DataFrame:
    """Compute the VIX "stretch" gold table: trailing ``window``-day realized
    mean/std of the close, and the z-score of each day's close against its
    own trailing distribution ("how stretched is VIX right now").

    Rows before ``window`` observations have accumulated get ``NaN`` stats
    (``min_periods=window``) rather than a noisy short-window estimate.
    Sample standard deviation (``ddof=1``, pandas' default) is used.
    """
    out = df.sort_values("date").reset_index(drop=True).copy()
    rolling = out["close"].rolling(window=window, min_periods=window)
    out["rolling_mean_20d"] = rolling.mean()
    out["rolling_std_20d"] = rolling.std()
    out["z_score"] = (out["close"] - out["rolling_mean_20d"]) / out["rolling_std_20d"]
    return out
