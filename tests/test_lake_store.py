"""The two invariants that matter most (README "hard principles"):
point-in-time correctness and bronze immutability.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore

DATASET = "vix"


def _frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})


def test_no_look_ahead(tmp_path: Path) -> None:
    """A backtest reading as-of an earlier date must never see a snapshot
    ingested later — the #1 invariant (README)."""
    store = DeltaLakeStore(tmp_path)

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


def test_restated_value_does_not_leak_into_earlier_asof_read(tmp_path: Path) -> None:
    """A restated/revised observation (docs/adr/0009: 'restated events are a
    new bronze write with a later timestamp') must not change an as-of read
    of the original ingest date -- even though the *same calendar date* is
    revised, not just extended with new dates. Adversarial: the revision is
    constructed so it WOULD change the answer if it leaked."""
    store = DeltaLakeStore(tmp_path)

    day1 = dt.date(2026, 1, 5)
    day2 = dt.date(2026, 1, 6)  # a later ingest that restates 2026-01-02

    original = _frame(["2026-01-02", "2026-01-05"], [15.0, 16.0])
    restated = _frame(["2026-01-02", "2026-01-05"], [999.0, 16.0])  # 2026-01-02 revised

    store.write_bronze(DATASET, day1, original)
    store.write_bronze(DATASET, day2, restated)

    as_of_day1 = store.read_bronze_as_of(DATASET, day1)
    close_by_date = dict(zip(as_of_day1["date"].dt.date, as_of_day1["close"], strict=True))
    assert close_by_date[dt.date(2026, 1, 2)] == 15.0  # the original value, not 999.0
    assert 999.0 not in as_of_day1["close"].tolist()

    # The restatement IS visible once the simulation clock reaches day2.
    as_of_day2 = store.read_bronze_as_of(DATASET, day2)
    assert (
        dict(zip(as_of_day2["date"].dt.date, as_of_day2["close"], strict=True))[dt.date(2026, 1, 2)]
        == 999.0
    )


def test_bronze_is_immutable(tmp_path: Path) -> None:
    """Re-ingesting the same day must never mutate the existing bronze
    partition. Verified at the Delta transaction-log level (the physical
    equivalent of the old "raw file bytes/mtime unchanged" check): a no-op
    write issues no new Delta commit, so the table version and the resolved
    partition's file listing are byte-for-byte identical before and after."""
    from deltalake import DeltaTable

    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 1, 5)
    table_uri = str(tmp_path / "bronze" / DATASET)

    original = _frame(["2026-01-05"], [16.0])
    location = store.write_bronze(DATASET, ingest_date, original)

    table_after_first_write = DeltaTable(table_uri)
    version_after_first_write = table_after_first_write.version()
    files_after_first_write = sorted(
        table_after_first_write.get_add_actions(flatten=True).column("path").to_pylist()
    )

    # A second ingest for the same day, with DIFFERENT data, must be a no-op:
    # no new Delta commit, same location, same files on disk.
    different = _frame(["2026-01-05"], [999.0])
    second_location = store.write_bronze(DATASET, ingest_date, different)

    table_after_second_write = DeltaTable(table_uri)
    assert second_location == location
    assert table_after_second_write.version() == version_after_first_write
    assert (
        sorted(table_after_second_write.get_add_actions(flatten=True).column("path").to_pylist())
        == files_after_first_write
    )

    stored = store.read_bronze_as_of(DATASET, ingest_date)
    assert stored["close"].iloc[0] == 16.0


def test_bronze_snapshot_id_is_stable_and_content_addressed(tmp_path: Path) -> None:
    """Reproducibility (docs/adr/0012): the snapshot id must be stable for the
    same resolved snapshot, and must change when the snapshot content changes
    — this is what a result cites in place of a lakeFS commit id."""
    store = DeltaLakeStore(tmp_path)
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
    store = DeltaLakeStore(tmp_path)
    df = _frame(["2026-01-02", "2026-01-05"], [15.0, 16.0])

    store.write_silver(DATASET, df)
    assert store.read_silver(DATASET).equals(df)

    store.write_gold(DATASET, df)
    assert store.read_gold(DATASET).equals(df)


def test_as_of_ignores_non_date_bronze_partition_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partition value that isn't a parseable ISO date must be ignored by
    as-of resolution, not crash it -- the Delta-backed equivalent of the old
    "stray non-date subdirectory" defensive test. Under Delta the
    transaction log is authoritative, so a stray value can't land there
    through the public API; this exercises the same defensive parsing
    (`dt.date.fromisoformat` inside a try/except) directly by injecting one
    via the partition-listing seam."""
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 1, 5)
    store.write_bronze(DATASET, ingest_date, _frame(["2026-01-05"], [16.0]))

    real = store._existing_bronze_ingest_date_strings
    monkeypatch.setattr(
        store,
        "_existing_bronze_ingest_date_strings",
        lambda table_uri: real(table_uri) | {"not-a-date"},
    )

    result = store.read_bronze_as_of(DATASET, ingest_date)
    assert result["close"].iloc[0] == 16.0


def test_read_missing_dataset_raises(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(LookupError):
        store.read_bronze_as_of("nonexistent", dt.date(2026, 1, 1))
    with pytest.raises(LookupError):
        store.read_silver("nonexistent")
    with pytest.raises(LookupError):
        store.read_gold("nonexistent")


def test_query_via_duckdb(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    df = _frame(["2026-01-02", "2026-01-05"], [15.0, 16.0])
    location = store.write_silver(DATASET, df)

    result = store.query(f"SELECT COUNT(*) AS n FROM delta_scan('{Path(location).as_posix()}')")
    assert int(result["n"].iloc[0]) == 2
