from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.vix import ingest_vix, parse_yahoo_chart, validate_and_quarantine
from tail_lab.lake.store import DeltaLakeStore


def test_parse_yahoo_chart_against_fixture(vix_yahoo_sample: dict[str, Any]) -> None:
    df = parse_yahoo_chart(vix_yahoo_sample)

    assert list(df.columns) == ["date", "close"]
    assert len(df) > 0
    # Null closes in the raw payload must be dropped, not turned into NaN rows.
    assert df["close"].notna().all()
    # Sorted, unique dates.
    assert df["date"].is_monotonic_increasing
    assert df["date"].is_unique


def test_parse_yahoo_chart_drops_null_closes() -> None:
    raw = {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": -18000},
                    "timestamp": [1767312000, 1767398400, 1767484800],
                    "indicators": {"quote": [{"close": [15.0, None, 16.5]}]},
                }
            ]
        }
    }
    df = parse_yahoo_chart(raw)
    assert len(df) == 2
    assert df["close"].tolist() == [15.0, 16.5]


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
            "close": [15.0, -3.0, 16.0],
        }
    )
    valid, quarantined = validate_and_quarantine(df)
    assert len(valid) == 2
    assert len(quarantined) == 1
    assert quarantined["close"].iloc[0] == -3.0
    assert set(valid["close"]) == {15.0, 16.0}


def test_validate_and_quarantine_all_valid_quarantines_nothing() -> None:
    """The happy-path branch (no bad rows at all) -- distinct code path from
    the mixed-validity case above (the try succeeds and returns immediately,
    never touching the except/SchemaErrors split logic)."""
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
            "close": [15.0, 16.0],
        }
    )
    valid, quarantined = validate_and_quarantine(df)
    assert len(valid) == 2
    assert quarantined.empty


def test_ingest_vix_commits_bronze_and_quarantines_bad_rows(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": 0},
                    "timestamp": [1767312000, 1767398400, 1767484800],
                    "indicators": {"quote": [{"close": [15.0, -3.0, 16.0]}]},
                }
            ]
        }
    }
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_vix(store, ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 2
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None

    bronze = store.read_bronze_as_of("vix", ingest_date)
    assert len(bronze) == 2
    assert -3.0 not in bronze["close"].tolist()

    # Quarantined rows are committed through the store (backend-agnostic —
    # never a raw filesystem write, see ingestion/vix.py), so they're
    # readable back through the same abstraction as any other bronze data.
    quarantined = store.read_bronze_as_of("vix__quarantine", ingest_date)
    assert len(quarantined) == 1
    assert quarantined["close"].iloc[0] == -3.0


def test_ingest_vix_all_valid_writes_no_quarantine_partition(tmp_path: Any) -> None:
    """When nothing is quarantined, ingest_vix must not write a (spurious,
    empty) quarantine snapshot and must report ``quarantine_path=None``."""
    store = DeltaLakeStore(tmp_path)
    raw = {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": 0},
                    "timestamp": [1767312000, 1767398400],
                    "indicators": {"quote": [{"close": [15.0, 16.0]}]},
                }
            ]
        }
    }
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_vix(store, ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 2
    assert result.quarantined_rows == 0
    assert result.quarantine_path is None

    with pytest.raises(LookupError):
        store.read_bronze_as_of("vix__quarantine", ingest_date)
