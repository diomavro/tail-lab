"""Bronze -> silver -> gold pure functions for the VIX futures term
structure (`docs/DATA_CONTRACTS.md` #11).

Pure by design, mirroring ``transforms/vix.py``: every function here takes
DataFrame(s) in and returns a DataFrame out. Nothing in this module touches
the filesystem, the network, or a ``LakeStore``.
"""

from __future__ import annotations

import pandas as pd

from tail_lab.contracts.vix_futures import VxFuturesSchema

#: Calendar-day target maturity for the constant-maturity curve. 30 days is
#: the standard tenor CBOE's own constant-maturity products interpolate to,
#: and it is close to the "soon-expiry" tenor README's OOM puts roll on.
TARGET_MATURITY_DAYS = 30


def bronze_to_silver(df: pd.DataFrame) -> pd.DataFrame:
    """Clean + validate a bronze VX futures frame into silver: sorted by
    ``(trade_date, contract_expiry)``, deduplicated, contract-typed. Raises
    if the frame does not satisfy the contract -- bronze rows are expected
    to already be valid (ingestion quarantines bad rows before they reach
    bronze), so a silver-stage failure signals a bug."""
    cleaned = (
        df.sort_values(["trade_date", "contract_expiry"])
        .drop_duplicates(subset=["trade_date", "contract_expiry"], keep="last")
        .reset_index(drop=True)
    )
    return VxFuturesSchema.validate(cleaned, lazy=True)


def silver_to_gold(df: pd.DataFrame, *, target_days: int = TARGET_MATURITY_DAYS) -> pd.DataFrame:
    """Interpolate the listed VX curve to one constant-``target_days``-
    maturity settlement price per ``trade_date`` -- the term-structure read
    every other dataset here reads spot VIX blind to
    (`docs/DATA_CONTRACTS.md` #11).

    For each ``trade_date``, brackets ``target_days`` between the nearest
    listed contract expiring on or before it and the nearest expiring on or
    after it, and linearly interpolates their ``settle`` prices by calendar
    days-to-expiry. A ``trade_date`` where every listed contract's
    days-to-expiry falls on the same side of ``target_days``
    (extrapolation) is dropped rather than guessed -- the same "don't glue
    past what's honestly known" stance the adapter takes on pre-2013
    history.

    Also carries ``front_settle``/``front_days_to_expiry`` (the single
    nearest-to-expiry listed contract) alongside the interpolated value,
    since "how far the curve had to reach" is exactly the context a bare
    number would hide.
    """
    working = df.copy()
    working["days_to_expiry"] = (working["contract_expiry"] - working["trade_date"]).dt.days
    working = working.sort_values(["trade_date", "days_to_expiry"])

    rows = [
        row
        for _, group in working.groupby("trade_date", sort=True)
        if (row := _interpolate(group, target_days=target_days)) is not None
    ]
    if not rows:
        return _empty_gold_frame(df["trade_date"])
    return pd.DataFrame(rows).reset_index(drop=True)


def _empty_gold_frame(trade_date: pd.Series) -> pd.DataFrame:
    """A correctly-typed zero-row frame, matching the non-empty path's dtypes
    (`AGENT_TODO.md`'s PR #84 design-review advisory) -- an object-dtype empty
    frame passes every test today but breaks the first all-extrapolated
    ``trade_date`` a downstream consumer hits, since pandas ops that assume a
    numeric/datetime dtype fail silently or loudly differently on ``object``.

    ``trade_date``'s dtype is sliced from the input frame rather than
    hardcoded, since pandas' own datetime64 resolution (``ns`` vs. ``us``) is
    a pandas version detail, not something this module should pin."""
    return pd.DataFrame(
        {
            "trade_date": trade_date.iloc[0:0].reset_index(drop=True),
            "cm_settle": pd.Series([], dtype="float64"),
            "front_settle": pd.Series([], dtype="float64"),
            "front_days_to_expiry": pd.Series([], dtype="int64"),
        }
    )


def _interpolate(group: pd.DataFrame, *, target_days: int) -> dict[str, object] | None:
    """One ``trade_date``'s bracketed interpolation, or ``None`` if
    ``target_days`` cannot be bracketed by the contracts actually listed
    that day. ``group`` must already be sorted ascending by
    ``days_to_expiry``."""
    before = group.loc[group["days_to_expiry"] <= target_days]
    after = group.loc[group["days_to_expiry"] >= target_days]
    if before.empty or after.empty:
        return None

    near = before.iloc[-1]  # largest days-to-expiry that's still <= target
    far = after.iloc[0]  # smallest days-to-expiry that's still >= target

    near_days, far_days = float(near["days_to_expiry"]), float(far["days_to_expiry"])
    if near_days == far_days:
        cm_settle = float(near["settle"])
    else:
        weight_far = (target_days - near_days) / (far_days - near_days)
        cm_settle = float(near["settle"]) + weight_far * (
            float(far["settle"]) - float(near["settle"])
        )

    return {
        "trade_date": group["trade_date"].iloc[0],
        "cm_settle": cm_settle,
        "front_settle": float(group["settle"].iloc[0]),
        "front_days_to_expiry": int(group["days_to_expiry"].iloc[0]),
    }
