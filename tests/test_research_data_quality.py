"""Tests for single-source data-quality checks (``research/data_quality.py``)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.data_quality import (
    assess_asset_quality,
    scan_price_anomalies,
)


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=pd.date_range("2026-01-01", periods=len(vals), freq="B"))


def test_spike_and_revert_is_flagged() -> None:
    # 100 100 150 100 100 -> a +50% print that reverts fully next bar.
    flags = scan_price_anomalies(_series([100, 100, 150, 100, 100, 100]))
    spikes = [f for f in flags if f.kind == "spike"]
    assert len(spikes) == 1
    assert spikes[0].date == dt.date(2026, 1, 5)  # the third business day


def test_real_crash_is_not_flagged() -> None:
    # A genuine -34% move that PERSISTS (keeps falling) must not be flagged as
    # a bad tick -- that's the whole point of the spike-and-revert condition.
    flags = scan_price_anomalies(_series([100, 100, 66, 60, 55, 55]))
    assert [f for f in flags if f.kind == "spike"] == []


def test_stale_run_is_flagged() -> None:
    # 5 identical closes in a row reads as a frozen feed.
    flags = scan_price_anomalies(_series([100, 100, 100, 100, 100, 110]))
    stale = [f for f in flags if f.kind == "stale"]
    assert len(stale) == 1
    assert "5 identical" in stale[0].detail


def test_clean_series_has_no_flags() -> None:
    rng = np.random.default_rng(0)
    clean = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size=200)))
    assert scan_price_anomalies(_series(list(clean))) == []


def _seed(store: DeltaLakeStore, ingest: dt.date, closes: list[float], symbol: str = "spy") -> None:
    n = len(closes)
    arr = np.array(closes, dtype=float)
    df = pd.DataFrame(
        {
            "symbol": symbol.upper(),
            "trade_date": pd.date_range(end=ingest, periods=n, freq="B"),
            "open": arr,
            "high": arr * 1.001,
            "low": arr * 0.999,
            "close": arr,
            "volume": np.full(n, 1_000_000, dtype=int),
            "adj_close": arr,
        }
    )
    store.write_bronze(dataset_id(symbol), ingest, df)


def test_assess_asset_quality_end_to_end(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed(store, ingest, [100] * 10 + [180] + [100] * 9)  # one bad tick in the middle
    report = assess_asset_quality(store, asset="spy", as_of=ingest)
    assert report.asset == "spy"
    assert report.n_bars == 20
    assert report.n_suspicious >= 1
    assert any(f.kind == "spike" for f in report.flags)


def test_assess_asset_quality_missing_raises(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(LookupError, match="no OHLCV"):
        assess_asset_quality(store, asset="nope", as_of=dt.date(2026, 3, 2))
