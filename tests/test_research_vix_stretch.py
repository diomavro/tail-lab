from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.lake.store import LocalParquetLakeStore
from tail_lab.research.vix_stretch import compute_vix_stretch
from tail_lab.transforms.vix import STRETCH_WINDOW


def _seed_bronze(store: LocalParquetLakeStore, ingest_date: dt.date, n: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed=7)
    closes = (18.0 + rng.normal(size=n)).round(4)
    dates = pd.date_range(end=ingest_date, periods=n, freq="D")
    df = pd.DataFrame({"date": dates, "close": closes})
    store.write_bronze("vix", ingest_date, df)
    return df


def test_compute_vix_stretch_end_to_end(tmp_path: Path) -> None:
    store = LocalParquetLakeStore(tmp_path)
    ingest_date = dt.date(2026, 3, 1)
    df = _seed_bronze(store, ingest_date, n=STRETCH_WINDOW + 5)

    result = compute_vix_stretch(store, as_of=ingest_date)

    expected_window = df["close"].to_numpy()[-STRETCH_WINDOW:]
    expected_mean = float(np.mean(expected_window))
    expected_std = float(np.std(expected_window, ddof=1))
    expected_close = float(df["close"].iloc[-1])

    assert result.date == ingest_date
    assert result.close == pytest.approx(expected_close)
    assert result.rolling_mean_20d == pytest.approx(expected_mean)
    assert result.rolling_std_20d == pytest.approx(expected_std)
    assert result.z_score == pytest.approx((expected_close - expected_mean) / expected_std)


def test_compute_vix_stretch_respects_no_look_ahead(tmp_path: Path) -> None:
    """Point-in-time: a stretch computed as-of an earlier ingest date must
    never reflect data from a later ingest."""
    store = LocalParquetLakeStore(tmp_path)
    day1 = dt.date(2026, 3, 1)
    day2 = dt.date(2026, 3, 2)

    _seed_bronze(store, day1, n=STRETCH_WINDOW + 5)
    result_day1 = compute_vix_stretch(store, as_of=day1)

    # A second, later snapshot with different data must not change the
    # already-computed as-of-day1 result when we ask again for day1.
    _seed_bronze(store, day2, n=STRETCH_WINDOW + 6)
    result_day1_again = compute_vix_stretch(store, as_of=day1)

    assert result_day1_again == result_day1
    assert result_day1.date == day1


def test_compute_vix_stretch_raises_on_insufficient_history(tmp_path: Path) -> None:
    store = LocalParquetLakeStore(tmp_path)
    ingest_date = dt.date(2026, 3, 1)
    _seed_bronze(store, ingest_date, n=STRETCH_WINDOW - 1)

    with pytest.raises(LookupError):
        compute_vix_stretch(store, as_of=ingest_date)
