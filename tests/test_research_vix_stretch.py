from __future__ import annotations

import datetime as dt
import statistics
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.vix_stretch import compute_vix_stretch
from tail_lab.transforms.vix import STRETCH_WINDOW


def _seed_bronze(store: DeltaLakeStore, ingest_date: dt.date, n: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed=7)
    closes = (18.0 + rng.normal(size=n)).round(4)
    dates = pd.date_range(end=ingest_date, periods=n, freq="D")
    df = pd.DataFrame({"date": dates, "close": closes})
    store.write_bronze("vix", ingest_date, df)
    return df


def test_compute_vix_stretch_end_to_end(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
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


def test_compute_vix_stretch_flat_window_is_undefined_not_missing(tmp_path: Path) -> None:
    """A flat trailing window (std == 0) makes the z-score genuinely undefined
    — distinct from insufficient history. The error must say so, not misdiagnose."""
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 3, 1)
    n = STRETCH_WINDOW + 2
    df = pd.DataFrame(
        {"date": pd.date_range(end=ingest_date, periods=n, freq="D"), "close": [20.0] * n}
    )
    store.write_bronze("vix", ingest_date, df)

    with pytest.raises(LookupError, match="zero variance"):
        compute_vix_stretch(store, as_of=ingest_date)


def test_compute_vix_stretch_respects_no_look_ahead(tmp_path: Path) -> None:
    """Point-in-time: a stretch computed as-of an earlier ingest date must
    never reflect data from a later ingest."""
    store = DeltaLakeStore(tmp_path)
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


def test_compute_vix_stretch_rejects_leaked_restatement(tmp_path: Path) -> None:
    """Adversarial (docs/adr/0009): construct a later snapshot that restates
    a value *inside* the trailing window in a way that would flip the
    z-score's sign if it leaked, then prove an as-of read of the earlier
    date is unaffected."""
    store = DeltaLakeStore(tmp_path)
    day1 = dt.date(2026, 3, 1)
    day2 = dt.date(2026, 3, 2)

    # A flat 20-day series ending with one high close -> a clearly positive
    # z-score for day1.
    n = STRETCH_WINDOW + 1
    dates = pd.date_range(end=day1, periods=n, freq="D")
    closes = [10.0] * (n - 1) + [50.0]
    df_day1 = pd.DataFrame({"date": dates, "close": closes})
    store.write_bronze("vix", day1, df_day1)

    result_day1 = compute_vix_stretch(store, as_of=day1)
    assert result_day1.z_score > 0

    # A later snapshot restates the entire window to a flat series with a
    # LOW final close -> if this leaked into the day1 read, the z-score
    # would flip negative. It must not.
    restated_closes = [10.0] * (n - 1) + [1.0]
    df_day2 = pd.DataFrame({"date": dates, "close": restated_closes})
    store.write_bronze("vix", day2, df_day2)

    result_day1_after_restatement = compute_vix_stretch(store, as_of=day1)
    assert result_day1_after_restatement == result_day1
    assert result_day1_after_restatement.z_score > 0

    # Sanity: the restatement really would have changed the answer if read
    # directly -- proves this is a real leak risk, not a vacuous test.
    result_day2 = compute_vix_stretch(store, as_of=day2)
    assert result_day2.z_score < 0
    assert result_day2.z_score != result_day1.z_score


def test_compute_vix_stretch_raises_on_insufficient_history(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 3, 1)
    _seed_bronze(store, ingest_date, n=STRETCH_WINDOW - 1)

    with pytest.raises(LookupError):
        compute_vix_stretch(store, as_of=ingest_date)


def test_compute_vix_stretch_pinned_hand_computable_case(tmp_path: Path) -> None:
    """Independently derivable (STANDARDS.md, not a snapshot of the
    function's own output): 19 flat days at 10.0 then one day at 20.0.
    Expected mean/std/z computed with the stdlib ``statistics`` module, a
    code path that shares nothing with ``transforms.vix.silver_to_gold``'s
    pandas ``.rolling()`` implementation."""
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 4, 1)
    closes = [10.0] * (STRETCH_WINDOW - 1) + [20.0]
    dates = pd.date_range(end=ingest_date, periods=STRETCH_WINDOW, freq="D")
    store.write_bronze("vix", ingest_date, pd.DataFrame({"date": dates, "close": closes}))

    result = compute_vix_stretch(store, as_of=ingest_date)

    expected_mean = statistics.mean(closes)
    expected_std = statistics.stdev(closes)  # sample stdev, ddof=1 (matches pandas default)
    expected_z = (closes[-1] - expected_mean) / expected_std

    assert result.close == 20.0
    assert result.rolling_mean_20d == pytest.approx(expected_mean)
    assert result.rolling_std_20d == pytest.approx(expected_std)
    assert result.z_score == pytest.approx(expected_z)
    # And the hand-derivable numbers themselves, pinned literally.
    assert result.rolling_mean_20d == pytest.approx(10.5)
    assert result.rolling_std_20d == pytest.approx(2.23606797749979)
    assert result.z_score == pytest.approx(4.2485291572496005)
