"""``DeltaLakeStore.read_bronze_history_as_of``: every eligible partition of one
dataset in a single projected, filtered scan (docs/adr/0033). Point-in-time
eligibility, the ignore list, the scan shape and the cache are what matter."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute
import pyarrow.dataset
import pytest
from deltalake import DeltaTable

from tail_lab.lake.store import BronzeSnapshotMissing, DeltaLakeStore

DS = "credit"
D1, D2, D3 = dt.date(2026, 3, 1), dt.date(2026, 3, 2), dt.date(2026, 3, 3)
COLS = ["obs_date", "value"]
HY = {"series_id": "HY"}


def _part(value: float) -> pd.DataFrame:
    """One HY row and one IG row; ``value`` identifies the partition."""
    return pd.DataFrame(
        {
            "series_id": ["HY", "IG"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "value": [value, 99.0],
        }
    )


def _store(tmp_path: Path) -> DeltaLakeStore:
    store = DeltaLakeStore(tmp_path)
    store._RESOLVE_TTL_S = -1.0  # every call re-reads the date list
    return store


def _seed(store: DeltaLakeStore, days: dict[dt.date, float], dataset: str = DS) -> None:
    for day, value in days.items():
        store.write_bronze(dataset, day, _part(value))


def _values(frame: pd.DataFrame) -> dict[dt.date, float]:
    return dict(zip(frame["ingest_date"], frame["value"], strict=True))


def test_eligibility_is_on_or_before_as_of_and_minus_the_ignored(tmp_path: Path) -> None:
    """WHY: a partition ingested after ``as_of`` is look-ahead (ADR 0009); an
    ignored one is a known-bad partition, keyed by (dataset, date) so the same
    date under another dataset is untouched."""
    store = _store(tmp_path)
    _seed(store, {D1: 1.0, D2: 2.0, D3: 3.0})
    read = store.read_bronze_history_as_of
    assert _values(read(DS, D2, columns=COLS, equals=HY)) == {D1: 1.0, D2: 2.0}
    assert _values(read(DS, D2, columns=COLS, equals=HY, ignored=frozenset({(DS, D1)}))) == {
        D2: 2.0
    }
    other = frozenset({("credit_archive", D1)})
    assert _values(read(DS, D2, columns=COLS, equals=HY, ignored=other)) == {D1: 1.0, D2: 2.0}


@dataclass
class _Seen:
    calls: int = 0
    partitions: object = None
    columns: object = None
    filter: object = None


def test_scan_is_one_projected_filtered_in_list_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WHY: the point is ONE scan with the filter and projection pushed into
    Parquet (about 1.5 s for 28 partitions, not 28 reads). Filtering after
    ``to_pandas`` would pull the IG rows and every column over the wire."""
    store = _store(tmp_path)
    _seed(store, {D1: 1.0, D2: 2.0, D3: 3.0})
    seen = _Seen()
    original = DeltaTable.to_pyarrow_dataset

    class _Proxy:
        def __init__(self, inner: pyarrow.dataset.Dataset) -> None:
            self._inner = inner

        def to_table(
            self, *, columns: list[str], filter: pyarrow.compute.Expression | None
        ) -> pa.Table:
            seen.columns, seen.filter = columns, filter
            return self._inner.to_table(columns=columns, filter=filter)

    def spy(self: DeltaTable, partitions: list[tuple[str, str, list[str]]] | None = None) -> _Proxy:
        seen.calls += 1
        seen.partitions = partitions
        return _Proxy(original(self, partitions=partitions))

    monkeypatch.setattr(DeltaTable, "to_pyarrow_dataset", spy)
    out = store.read_bronze_history_as_of(DS, D3, columns=COLS, equals=HY)
    assert seen.calls == 1
    assert seen.partitions == [("ingest_date", "in", ["2026-03-01", "2026-03-02", "2026-03-03"])]
    assert seen.filter is not None
    assert "series_id" in str(seen.filter)
    assert seen.columns == [*COLS, "ingest_date"]
    assert list(out.columns) == [*COLS, "ingest_date"]
    assert len(out) == 3  # HY rows only, one per partition
    assert all(isinstance(d, dt.date) for d in out["ingest_date"])


def test_unknown_column_is_a_keyerror_not_an_arrow_error(tmp_path: Path) -> None:
    """WHY: callers' error handling keys on KeyError (same contract as
    ``read_bronze_columns_as_of``); pyarrow would raise ArrowInvalid."""
    store = _store(tmp_path)
    _seed(store, {D1: 1.0})
    with pytest.raises(KeyError, match="nope"):
        store.read_bronze_history_as_of(DS, D1, columns=["nope"], equals=HY)
    with pytest.raises(KeyError, match="nope"):
        store.read_bronze_history_as_of(DS, D1, columns=COLS, equals={"nope": "x"})


def test_cache_follows_the_eligible_date_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WHY: the cache is keyed on the eligible partitions, so a later partition,
    a BACK-DATED one, or a changed ignore list is seen at once, while an
    unchanged set is not re-scanned. Keyed on ``as_of`` or the newest date, a
    back-dated partition would be silently missing."""
    store = _store(tmp_path)
    _seed(store, {D2: 2.0})
    read = store.read_bronze_history_as_of
    assert _values(read(DS, D3, columns=COLS, equals=HY)) == {D2: 2.0}

    scans = [0]
    original = DeltaTable.to_pyarrow_dataset

    def counting(
        self: DeltaTable, partitions: list[tuple[str, str, list[str]]] | None = None
    ) -> pyarrow.dataset.Dataset:
        scans[0] += 1
        return original(self, partitions=partitions)

    monkeypatch.setattr(DeltaTable, "to_pyarrow_dataset", counting)
    read(DS, D3, columns=COLS, equals=HY)
    assert scans[0] == 0  # same date set: served from the cache

    _seed(store, {D3: 3.0})  # a later partition
    assert _values(read(DS, D3, columns=COLS, equals=HY)) == {D2: 2.0, D3: 3.0}
    _seed(store, {D1: 1.0})  # a back-dated one
    assert _values(read(DS, D3, columns=COLS, equals=HY)) == {D1: 1.0, D2: 2.0, D3: 3.0}
    ignored = frozenset({(DS, D2)})
    assert _values(read(DS, D3, columns=COLS, equals=HY, ignored=ignored)) == {D1: 1.0, D3: 3.0}


def test_history_read_returns_a_copy(tmp_path: Path) -> None:
    """WHY: a caller that mutates its frame must not corrupt the next read
    (precedent: test_lake_store_cache.py::test_cache_returns_a_copy)."""
    store = _store(tmp_path)
    _seed(store, {D1: 1.0})
    first = store.read_bronze_history_as_of(DS, D1, columns=COLS, equals=HY)
    first["value"] = -1.0
    second = store.read_bronze_history_as_of(DS, D1, columns=COLS, equals=HY)
    assert list(second["value"]) == [1.0]


def test_no_eligible_partition_raises_bronze_snapshot_missing(tmp_path: Path) -> None:
    """WHY: callers must tell "no such history" from a store fault, so the
    absence is a named LookupError subclass they catch alone."""
    store = _store(tmp_path)
    read = store.read_bronze_history_as_of
    with pytest.raises(BronzeSnapshotMissing):
        read("never_written", D1, columns=COLS, equals=HY)
    _seed(store, {D2: 2.0})
    with pytest.raises(BronzeSnapshotMissing) as before:
        read(DS, D1, columns=COLS, equals=HY)
    assert isinstance(before.value, LookupError)
    with pytest.raises(BronzeSnapshotMissing):
        read(DS, D2, columns=COLS, equals=HY, ignored=frozenset({(DS, D2)}))


def test_as_of_resolution_raises_the_same_named_error(tmp_path: Path) -> None:
    """The existing single-snapshot path raises it too, still a LookupError."""
    store = _store(tmp_path)
    with pytest.raises(BronzeSnapshotMissing):
        store.read_bronze_as_of("never_written", D1)
    _seed(store, {D2: 2.0})
    with pytest.raises(BronzeSnapshotMissing):
        store.read_bronze_as_of(DS, D1)
