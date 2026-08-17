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
    location = store.write_bronze(DATASET, ingest_date, original)
    path = Path(location)
    original_mtime_ns = path.stat().st_mtime_ns
    original_bytes = path.read_bytes()

    # A second ingest for the same day, with DIFFERENT data, must be a no-op.
    different = _frame(["2026-01-05"], [999.0])
    second_location = store.write_bronze(DATASET, ingest_date, different)

    assert second_location == location
    assert path.read_bytes() == original_bytes
    assert path.stat().st_mtime_ns == original_mtime_ns

    stored = store.read_bronze_as_of(DATASET, ingest_date)
    assert stored["close"].iloc[0] == 16.0


def test_bronze_snapshot_id_is_stable_and_content_addressed(tmp_path: Path) -> None:
    """Reproducibility (docs/adr/0012): the snapshot id must be stable for the
    same resolved snapshot, and must change when the snapshot content changes
    — this is what a result cites in place of a lakeFS commit id."""
    store = LocalParquetLakeStore(tmp_path)
    day1 = dt.date(2026, 1, 5)
    day2 = dt.date(2026, 1, 6)

    store.write_bronze(DATASET, day1, _frame(["2026-01-05"], [16.0]))

    snapshot_id_1 = store.bronze_snapshot_id(DATASET, day1)
    snapshot_id_1_again = store.bronze_snapshot_id(DATASET, day1)
    assert snapshot_id_1 == snapshot_id_1_again
    assert snapshot_id_1.startswith(f"{DATASET}@2026-01-05#")

    # A later, different snapshot must resolve to a different id.
    store.write_bronze(DATASET, day2, _frame(["2026-01-06"], [17.0]))
    snapshot_id_2 = store.bronze_snapshot_id(DATASET, day2)
    assert snapshot_id_2 != snapshot_id_1
    assert snapshot_id_2.startswith(f"{DATASET}@2026-01-06#")

    # Reading as of day1 must still resolve to the day1 snapshot id, even
    # though a later snapshot now exists (no-look-ahead applies to the id too).
    assert store.bronze_snapshot_id(DATASET, day1) == snapshot_id_1

    with pytest.raises(LookupError):
        store.bronze_snapshot_id(DATASET, dt.date(2026, 1, 1))


def test_silver_and_gold_round_trip(tmp_path: Path) -> None:
    store = LocalParquetLakeStore(tmp_path)
    df = _frame(["2026-01-02", "2026-01-05"], [15.0, 16.0])

    store.write_silver(DATASET, df)
    assert store.read_silver(DATASET).equals(df)

    store.write_gold(DATASET, df)
    assert store.read_gold(DATASET).equals(df)


def test_as_of_ignores_non_date_bronze_subdirectories(tmp_path: Path) -> None:
    """A stray non-date subdirectory under bronze/<dataset>/ (e.g. a manual
    scratch folder) must be ignored by as-of resolution, not crash it —
    exercises the shared-base defensive path all backends share."""
    store = LocalParquetLakeStore(tmp_path)
    ingest_date = dt.date(2026, 1, 5)
    store.write_bronze(DATASET, ingest_date, _frame(["2026-01-05"], [16.0]))

    stray = tmp_path / "bronze" / DATASET / "not-a-date"
    stray.mkdir(parents=True)
    (stray / "data.parquet").write_bytes(b"not actually parquet")

    result = store.read_bronze_as_of(DATASET, ingest_date)
    assert result["close"].iloc[0] == 16.0


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
    location = store.write_silver(DATASET, df)

    result = store.query(f"SELECT COUNT(*) AS n FROM read_parquet('{Path(location).as_posix()}')")
    assert int(result["n"].iloc[0]) == 2
