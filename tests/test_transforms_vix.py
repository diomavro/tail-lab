from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tail_lab.transforms.vix import STRETCH_WINDOW, bronze_to_silver, silver_to_gold


def test_bronze_to_silver_sorts_and_dedupes() -> None:
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-02", "2026-01-05"]),
            "close": [16.0, 15.0, 99.0],  # duplicate 2026-01-05: last wins
        }
    )
    silver = bronze_to_silver(df)
    assert silver["date"].tolist() == list(pd.to_datetime(["2026-01-02", "2026-01-05"]))
    assert silver["close"].tolist() == [15.0, 99.0]


def test_silver_to_gold_pins_stretch_metric_against_hand_computed_value() -> None:
    """Independent (numpy, not pandas .rolling) computation of the trailing
    20-day mean/std/z-score, pinned against the production transform."""
    n = 25
    rng = np.random.default_rng(seed=42)
    closes = (15.0 + rng.normal(size=n)).round(4)
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    silver = pd.DataFrame({"date": dates, "close": closes})

    gold = silver_to_gold(silver, window=STRETCH_WINDOW)

    # First window-1 rows have no full trailing window yet.
    assert gold["rolling_mean_20d"].iloc[: STRETCH_WINDOW - 1].isna().all()

    # Independently (numpy) compute expected stats for the last row.
    last_window = closes[n - STRETCH_WINDOW : n]
    expected_mean = float(np.mean(last_window))
    expected_std = float(np.std(last_window, ddof=1))
    expected_z = (float(closes[-1]) - expected_mean) / expected_std

    last = gold.iloc[-1]
    assert last["rolling_mean_20d"] == pytest.approx(expected_mean, abs=1e-9)
    assert last["rolling_std_20d"] == pytest.approx(expected_std, abs=1e-9)
    assert last["z_score"] == pytest.approx(expected_z, abs=1e-9)


def test_silver_to_gold_hand_computed_small_case() -> None:
    """A fully hand-computable case: 3-day window over [10, 20, 30, 60]."""
    silver = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=4, freq="D"),
            "close": [10.0, 20.0, 30.0, 60.0],
        }
    )
    gold = silver_to_gold(silver, window=3)

    # Row 0, 1: not enough history.
    assert gold["rolling_mean_20d"].iloc[0:2].isna().all()

    # Row 2 (window = [10, 20, 30]): mean=20, sample std=10, close=30 -> z=1.0
    row2 = gold.iloc[2]
    assert row2["rolling_mean_20d"] == pytest.approx(20.0)
    assert row2["rolling_std_20d"] == pytest.approx(10.0)
    assert row2["z_score"] == pytest.approx(1.0)

    # Row 3 (window = [20, 30, 60]): mean=110/3, sample std=20.81665999,
    # close=60 -> z=(60-36.6667)/20.81666 = 1.120897... (statistics module, cross-checked)
    row3 = gold.iloc[3]
    assert row3["rolling_mean_20d"] == pytest.approx(110 / 3)
    assert row3["rolling_std_20d"] == pytest.approx(20.81665999466133)
    assert row3["z_score"] == pytest.approx(1.12089707663561)
