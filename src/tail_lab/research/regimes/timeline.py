"""Point-in-time market-regime timeline from the VIX complex.

``label_vix_series`` is pure (VIX closes in, regime labels out);
``compute_regime_timeline`` reads the bronze VIX snapshot known on or before
``as_of`` and labels every date in it. The memory layer (phase 2) maps each
backtest cycle's entry date onto this timeline to key its verdict by regime.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from tail_lab.contracts.regime import RegimeLabel, classify_vix_level
from tail_lab.lake.store import LakeStore

VIX_DATASET = "vix"


def label_vix_series(vix_close: pd.Series) -> pd.Series:
    """Regime label for each VIX close, preserving the input index."""
    labels = [classify_vix_level(float(v)) for v in vix_close.to_numpy()]
    return pd.Series(labels, index=vix_close.index, name="regime")


def compute_regime_timeline(store: LakeStore, *, as_of: dt.date) -> pd.Series:
    """Date-indexed regime labels from the VIX snapshot known as of ``as_of``.

    Reads only the bronze VIX known on or before ``as_of``
    (:meth:`LakeStore.read_bronze_as_of` enforces no-look-ahead), sorted and
    de-duplicated by date. Raises ``LookupError`` if no VIX snapshot exists as
    of that date.
    """
    try:
        bronze = store.read_bronze_as_of(VIX_DATASET, as_of)
    except LookupError as exc:
        raise LookupError(f"no VIX known as of {as_of.isoformat()}") from exc
    if bronze.empty:
        raise LookupError(f"no VIX known as of {as_of.isoformat()}")

    ordered = bronze.sort_values("date").drop_duplicates(subset="date", keep="last")
    close = pd.Series(
        ordered["close"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(ordered["date"]),
        name="close",
    )
    return label_vix_series(close)


def regime_on_or_before(timeline: pd.Series, when: dt.date) -> RegimeLabel:
    """The regime in force on ``when`` — the label of the latest timeline date
    that is on or before it (markets are closed on weekends/holidays, so an
    arbitrary calendar date needs the last *trading* day's regime).

    Raises ``LookupError`` if ``when`` predates the whole timeline.
    """
    ts = pd.Timestamp(when)
    eligible = timeline.loc[timeline.index <= ts]
    if eligible.empty:
        raise LookupError(f"{when.isoformat()} predates the regime timeline")
    label: RegimeLabel = eligible.iloc[-1]
    return label
