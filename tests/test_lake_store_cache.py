"""The DeltaLakeStore in-process read cache (Phase 0 perf): snapshot-keyed,
self-invalidating, copy-on-read."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from tail_lab.lake.store import DeltaLakeStore


def _frame(vals: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"date": pd.date_range("2026-01-01", periods=len(vals), freq="D"), "close": vals}
    )


def _count_partition_reads(store: DeltaLakeStore, monkeypatch) -> list[int]:
    calls = [0]
    original = store._read_bronze_partition

    def counting(table_uri: str, snapshot_date: dt.date) -> pd.DataFrame:
        calls[0] += 1
        return original(table_uri, snapshot_date)

    monkeypatch.setattr(store, "_read_bronze_partition", counting)
    return calls


def test_second_read_is_a_cache_hit(tmp_path: Path, monkeypatch) -> None:
    store = DeltaLakeStore(tmp_path)
    store.write_bronze("vix", dt.date(2026, 3, 1), _frame([10.0, 11.0, 12.0]))
    calls = _count_partition_reads(store, monkeypatch)

    a = store.read_bronze_as_of("vix", dt.date(2026, 3, 1))
    b = store.read_bronze_as_of("vix", dt.date(2026, 3, 1))
    # bronze_snapshot_id reuses the same cached frame, no extra partition read.
    store.bronze_snapshot_id("vix", dt.date(2026, 3, 1))

    assert calls[0] == 1  # one physical read, the rest served from cache
    pd.testing.assert_frame_equal(a, b)


def test_cache_returns_a_copy(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    store.write_bronze("vix", dt.date(2026, 3, 1), _frame([10.0, 11.0]))
    first = store.read_bronze_as_of("vix", dt.date(2026, 3, 1))
    first.loc[0, "close"] = 999.0  # mutate the returned frame
    second = store.read_bronze_as_of("vix", dt.date(2026, 3, 1))
    assert second.loc[0, "close"] == 10.0  # cache uncorrupted


def test_new_snapshot_invalidates(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    store._RESOLVE_TTL_S = -1.0  # re-resolve every call, so a new ingest is seen immediately
    store.write_bronze("vix", dt.date(2026, 3, 1), _frame([10.0, 11.0]))

    as_of = dt.date(2026, 3, 5)
    first = store.read_bronze_as_of("vix", as_of)
    assert list(first["close"]) == [10.0, 11.0]

    # A later snapshot resolves to a NEW snapshot_date -> cache miss -> fresh data.
    store.write_bronze("vix", dt.date(2026, 3, 3), _frame([20.0, 21.0, 22.0]))
    second = store.read_bronze_as_of("vix", as_of)
    assert list(second["close"]) == [20.0, 21.0, 22.0]


def test_snapshot_id_stable_and_data_consistent(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    store.write_bronze("vix", dt.date(2026, 3, 1), _frame([10.0, 11.0, 12.0]))
    sid1 = store.bronze_snapshot_id("vix", dt.date(2026, 3, 1))
    sid2 = store.bronze_snapshot_id("vix", dt.date(2026, 3, 1))
    assert sid1 == sid2
    assert sid1.startswith("vix@2026-03-01#")
