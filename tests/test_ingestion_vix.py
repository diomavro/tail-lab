from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.sources import AllSourcesFailed
from tail_lab.ingestion.vix import (
    ingest_vix,
    parse_cboe_vix_csv,
    parse_yahoo_chart,
    validate_and_quarantine,
)
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


# --- Cboe primary source (2026-08-21: Yahoo demoted to fallback) ---------


def test_parse_cboe_vix_against_real_fixture(cboe_vix_sample: str) -> None:
    df = parse_cboe_vix_csv(cboe_vix_sample)

    assert list(df.columns) == ["date", "close"]
    assert df["date"].is_monotonic_increasing
    assert df["date"].is_unique
    assert df["close"].notna().all()
    # VIX was rebased/published from 1990-01-02; pinning the exact pair
    # proves the MM/DD/YYYY parse landed on the right day.
    assert df["date"].iloc[0] == pd.Timestamp("1990-01-02")
    assert df["close"].iloc[0] == pytest.approx(17.24)


def test_parse_cboe_vix_takes_close_not_open(cboe_vix_sample: str) -> None:
    """The file is DATE,OPEN,HIGH,LOW,CLOSE. Taking the second column would
    silently give OPEN -- on 2008-10-10 that is 65.85 rather than the 69.95
    close, which is the kind of error a row count never catches."""
    df = parse_cboe_vix_csv(cboe_vix_sample)
    row = df.loc[df["date"] == pd.Timestamp("2008-10-10")]

    assert len(row) == 1
    assert row["close"].iloc[0] == pytest.approx(69.95)


def test_parse_cboe_vix_reads_us_date_format(cboe_vix_sample: str) -> None:
    """03/12/2020 is 12 March (the COVID spike), not 3 December."""
    df = parse_cboe_vix_csv(cboe_vix_sample)

    march = df.loc[df["date"] == pd.Timestamp("2020-03-12")]
    assert len(march) == 1
    assert march["close"].iloc[0] == pytest.approx(75.47)
    assert df.loc[df["date"] == pd.Timestamp("2020-12-03")].empty


def test_parse_cboe_vix_drops_blanks_but_keeps_malformed() -> None:
    """A blank close is a holiday -- "not a row". A garbled date or a
    non-numeric level is a bad row and must reach quarantine."""
    raw = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2026,1,2,0.5,15.0\n01/05/2026,1,2,0.5,\n99/99/2026,1,2,0.5,16.0\n"
    df = parse_cboe_vix_csv(raw)

    assert len(df) == 2
    assert df["date"].isna().sum() == 1


def test_parse_cboe_vix_empty_source_returns_typed_empty_frame() -> None:
    df = parse_cboe_vix_csv("DATE,OPEN,HIGH,LOW,CLOSE\n")

    assert df.empty
    assert list(df.columns) == ["date", "close"]


def test_ingest_prefers_cboe_and_records_the_source(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    yahoo_raw = {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": 0},
                    "timestamp": [1767312000],
                    "indicators": {"quote": [{"close": [99.0]}]},
                }
            ]
        }
    }
    result = ingest_vix(
        store,
        ingest_date=dt.date(2026, 1, 6),
        cboe_csv="DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2026,1,2,0.5,15.0\n",
        raw=yahoo_raw,
    )

    assert result.source_id == "cboe"
    assert result.valid_rows == 1
    bronze = store.read_bronze_as_of("vix", dt.date(2026, 1, 6))
    # The Yahoo payload was supplied too and must NOT have been used.
    assert bronze["close"].tolist() == [15.0]


def test_ingest_falls_back_to_yahoo_when_cboe_is_unusable(tmp_path: Any) -> None:
    """Cboe answering with an empty/garbled file must not fail the ingest --
    that is the failure mode the source chain exists to survive."""
    store = DeltaLakeStore(tmp_path)
    yahoo_raw = {
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
    result = ingest_vix(
        store,
        ingest_date=dt.date(2026, 1, 6),
        cboe_csv="DATE,OPEN,HIGH,LOW,CLOSE\n",
        raw=yahoo_raw,
    )

    assert result.source_id == "yahoo"
    assert result.valid_rows == 2


def test_ingest_raises_when_every_source_is_dead(tmp_path: Any) -> None:
    """Fail loud rather than commit an empty partition that would shadow
    good data on the next as-of read."""
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(AllSourcesFailed):
        ingest_vix(
            store,
            ingest_date=dt.date(2026, 1, 6),
            cboe_csv="DATE,OPEN,HIGH,LOW,CLOSE\n",
            raw={"chart": {"result": [{"meta": {}, "timestamp": [], "indicators": {"quote": [{"close": []}]}}]}},
        )
