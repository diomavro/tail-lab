"""The two invariants that matter most (README "hard principles"):
point-in-time correctness and bronze immutability.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.lake.store import LocalParquetLakeStore

DATASET = "vix"


def _frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})


def test_no_look_ahead(tmp_path: Path) -> None:
    """A backtest reading as-of an earlier date must never see a snapshot
    ingested later — the #1 invariant (README)."""
    store = LocalParquetLakeStore(tmp_path)

    day1 = dt.date(2026, 1, 5)
    day2 = dt.date(2026, 1, 6)

    snapshot_day1 = _frame(["2026-01-02", "2026-01-05"], [15.0, 16.0])
    snapshot_day2 = _frame(["2026-01-02", "2026-01-05", "2026-01-06"], [15.0, 16.0, 17.0])

    store.write_bronze(DATASET, day1, snapshot_day1)
    store.write_bronze(DATASET, day2, snapshot_day2)

    as_of_day1 = store.read_bronze_as_of(DATASET, day1)
    assert dt.date(2026, 1, 6) not in set(as_of_day1["date"].dt.date)
    assert len(as_of_day1) == 2

    as_of_day2 = store.read_bronze_as_of(DATASET, day2)
    assert len(as_of_day2) == 3

    # Reading as of a date strictly before any snapshot exists must fail
    # loudly, not silently return the wrong (future) data.
    with pytest.raises(LookupError):
        store.read_bronze_as_of(DATASET, dt.date(2026, 1, 1))


def test_bronze_is_immutable(tmp_path: Path) -> None:
    """Re-ingesting the same day must never mutate the existing bronze file."""
    store = LocalParquetLakeStore(tmp_path)
    ingest_date = dt.date(2026, 1, 5)

    original = _frame(["2026-01-05"], [16.0])
    path = store.write_bronze(DATASET, ingest_date, original)
    original_mtime_ns = path.stat().st_mtime_ns
    original_bytes = path.read_bytes()

    # A second ingest for the same day, with DIFFERENT data, must be a no-op.
    different = _frame(["2026-01-05"], [999.0])
    second_path = store.write_bronze(DATASET, ingest_date, different)

    assert second_path == path
    assert path.read_bytes() == original_bytes
    assert path.stat().st_mtime_ns == original_mtime_ns

    stored = store.read_bronze_as_of(DATASET, ingest_date)
    assert stored["close"].iloc[0] == 16.0


def test_silver_and_gold_round_trip(tmp_path: Path) -> None:
    store = LocalParquetLakeStore(tmp_path)
    df = _frame(["2026-01-02", "2026-01-05"], [15.0, 16.0])

    store.write_silver(DATASET, df)
    assert store.read_silver(DATASET).equals(df)

    store.write_gold(DATASET, df)
    assert store.read_gold(DATASET).equals(df)


def test_read_missing_dataset_raises(tmp_path: Path) -> None:
    store = LocalParquetLakeStore(tmp_path)
    with pytest.raises(LookupError):
        store.read_bronze_as_of("nonexistent", dt.date(2026, 1, 1))
    with pytest.raises(LookupError):
        store.read_silver("nonexistent")
    with pytest.raises(LookupError):
        store.read_gold("nonexistent")


def test_query_via_duckdb(tmp_path: Path) -> None:
    store = LocalParquetLakeStore(tmp_path)
    df = _frame(["2026-01-02", "2026-01-05"], [15.0, 16.0])
    path = store.write_silver(DATASET, df)

    result = store.query(f"SELECT COUNT(*) AS n FROM read_parquet('{path.as_posix()}')")
    assert int(result["n"].iloc[0]) == 2
