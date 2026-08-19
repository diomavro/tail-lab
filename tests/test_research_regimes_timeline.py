"""Tests for the market-regime classifier + timeline
(``contracts/regime.py``, ``research/regimes/timeline.py``)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.contracts.regime import (
    CALM_MAX,
    ELEVATED_MAX,
    REGIME_LABELS,
    classify_vix_level,
)
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.regimes.timeline import (
    compute_regime_timeline,
    label_vix_series,
    regime_on_or_before,
)


def test_regime_labels_are_ordered_by_stress() -> None:
    assert REGIME_LABELS == ("calm", "elevated", "crisis")


def test_classify_vix_level_thresholds_are_half_open() -> None:
    # Boundaries are [lower, upper): the threshold value belongs to the harsher
    # bucket, so exactly-CALM_MAX is already 'elevated', not still 'calm'.
    assert classify_vix_level(CALM_MAX - 0.01) == "calm"
    assert classify_vix_level(CALM_MAX) == "elevated"
    assert classify_vix_level(ELEVATED_MAX - 0.01) == "elevated"
    assert classify_vix_level(ELEVATED_MAX) == "crisis"
    assert classify_vix_level(9.0) == "calm"
    assert classify_vix_level(60.0) == "crisis"


def test_label_vix_series_preserves_index() -> None:
    idx = pd.date_range("2022-01-03", periods=3, freq="B")
    close = pd.Series([12.0, 22.0, 40.0], index=idx)
    labels = label_vix_series(close)
    assert list(labels) == ["calm", "elevated", "crisis"]
    assert labels.index.equals(idx)


def _seed_vix(store: DeltaLakeStore, ingest_date: dt.date, closes: list[float]) -> pd.DatetimeIndex:
    dates = pd.date_range(end=ingest_date, periods=len(closes), freq="B")
    store.write_bronze("vix", ingest_date, pd.DataFrame({"date": dates, "close": closes}))
    return dates


def test_compute_regime_timeline_labels_every_date(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest, [12.0, 18.0, 30.0, 15.0])
    timeline = compute_regime_timeline(store, as_of=ingest)
    assert list(timeline) == ["calm", "elevated", "crisis", "calm"]


def test_compute_regime_timeline_respects_no_look_ahead(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    day1, day2 = dt.date(2026, 3, 2), dt.date(2026, 3, 3)
    _seed_vix(store, day1, [12.0, 13.0, 14.0])
    t1 = compute_regime_timeline(store, as_of=day1)
    # A later snapshot that restates everything to crisis must not leak back.
    _seed_vix(store, day2, [40.0, 41.0, 42.0])
    t1_again = compute_regime_timeline(store, as_of=day1)
    assert list(t1_again) == list(t1) == ["calm", "calm", "calm"]
    assert list(compute_regime_timeline(store, as_of=day2)) == ["crisis", "crisis", "crisis"]


def test_compute_regime_timeline_missing_vix_raises(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(LookupError, match="no VIX"):
        compute_regime_timeline(store, as_of=dt.date(2026, 3, 2))


def test_regime_on_or_before_uses_last_trading_day() -> None:
    idx = pd.to_datetime(["2022-03-04", "2022-03-07", "2022-03-08"])  # Fri, Mon, Tue
    timeline = pd.Series(["calm", "elevated", "crisis"], index=idx)
    # A Saturday resolves to Friday's regime (last trading day on or before).
    assert regime_on_or_before(timeline, dt.date(2022, 3, 5)) == "calm"
    assert regime_on_or_before(timeline, dt.date(2022, 3, 8)) == "crisis"
    with pytest.raises(LookupError, match="predates"):
        regime_on_or_before(timeline, dt.date(2022, 1, 1))
