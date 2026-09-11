from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.vix_complex import (
    ingest_vix_complex,
    parse_vix_complex_csv,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore

# --- parsing: the two source shapes this family actually serves ----------


def test_parse_ohlc_shape_against_real_vix3m_fixture(vix3m_sample: str) -> None:
    df = parse_vix_complex_csv("VIX3M", vix3m_sample)

    assert list(df.columns) == ["series", "trade_date", "open", "high", "low", "close"]
    assert (df["series"] == "VIX3M").all()
    assert df["trade_date"].is_monotonic_increasing
    assert df["trade_date"].is_unique
    assert df["open"].notna().all()
    # 2009-09-18 is the fixture's first row: pins the MM/DD/YYYY parse and
    # that OPEN/HIGH/LOW/CLOSE landed in the right columns, not shifted.
    first = df.loc[df["trade_date"] == pd.Timestamp("2009-09-18")]
    assert first["open"].iloc[0] == pytest.approx(25.91)
    assert first["close"].iloc[0] == pytest.approx(26.54)


def test_parse_ohlc_shape_against_real_vix9d_fixture(vix9d_sample: str) -> None:
    df = parse_vix_complex_csv("VIX9D", vix9d_sample)

    assert (df["series"] == "VIX9D").all()
    row = df.loc[df["trade_date"] == pd.Timestamp("2026-09-01")]
    assert row["close"].iloc[0] == pytest.approx(14.33)


def test_parse_bare_shape_against_real_vvix_fixture(vvix_sample: str) -> None:
    """VVIX serves ``DATE,VVIX`` -- no OHLC on the wire at all."""
    df = parse_vix_complex_csv("VVIX", vvix_sample)

    assert (df["series"] == "VVIX").all()
    assert df["open"].isna().all()
    assert df["high"].isna().all()
    assert df["low"].isna().all()
    assert df["close"].notna().all()
    first = df.loc[df["trade_date"] == pd.Timestamp("2006-03-06")]
    assert first["close"].iloc[0] == pytest.approx(71.73)


def test_parse_bare_shape_against_real_skew_fixture(skew_sample: str) -> None:
    df = parse_vix_complex_csv("SKEW", skew_sample)

    assert (df["series"] == "SKEW").all()
    assert df["open"].isna().all()
    first = df.loc[df["trade_date"] == pd.Timestamp("1990-01-02")]
    assert first["close"].iloc[0] == pytest.approx(126.09)


def test_parse_drops_blanks_but_keeps_malformed() -> None:
    raw = "DATE,VVIX\n01/02/2026,85.0\n01/05/2026,\n99/99/2026,86.0\n"
    df = parse_vix_complex_csv("VVIX", raw)

    assert len(df) == 2
    assert df["trade_date"].isna().sum() == 1


def test_parse_empty_source_returns_typed_empty_frame() -> None:
    df = parse_vix_complex_csv("SKEW", "DATE,SKEW\n")

    assert df.empty
    assert list(df.columns) == ["series", "trade_date", "open", "high", "low", "close"]


def test_parse_date_only_row_returns_typed_empty_frame() -> None:
    """A ``DATE`` column with no value column at all (not even a bare
    ``DATE,<TICKER>``) is not a row this source ever legitimately produces --
    treat it the same as an empty file rather than raising."""
    df = parse_vix_complex_csv("SKEW", "DATE\n01/02/2026\n")

    assert df.empty
    assert list(df.columns) == ["series", "trade_date", "open", "high", "low", "close"]


# --- validate/quarantine --------------------------------------------------


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "series": ["SKEW", "SKEW", "SKEW"],
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
            "open": [float("nan")] * 3,
            "high": [float("nan")] * 3,
            "low": [float("nan")] * 3,
            "close": [130.0, -3.0, 140.0],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert len(quarantined) == 1
    assert quarantined["close"].iloc[0] == -3.0


def test_same_date_across_two_series_is_not_a_duplicate() -> None:
    """Uniqueness is on (series, trade_date), not trade_date alone -- the
    whole point of one shared dataset is ingesting the family together."""
    df = pd.DataFrame(
        {
            "series": ["VVIX", "SKEW"],
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "open": [float("nan"), float("nan")],
            "high": [float("nan"), float("nan")],
            "low": [float("nan"), float("nan")],
            "close": [85.0, 130.0],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert quarantined.empty


# --- orchestration ---------------------------------------------------------


def test_ingest_commits_whole_family_as_one_snapshot(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {
        "VIX3M": "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2026,18.0,18.5,17.8,18.2\n",
        "VVIX": "DATE,VVIX\n01/02/2026,85.0\n",
        "SKEW": "DATE,SKEW\n01/02/2026,130.0\n",
    }
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_vix_complex(store, ["VIX3M", "VVIX", "SKEW"], ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 3
    assert result.quarantined_rows == 0
    assert result.quarantine_path is None
    assert result.series == ("VIX3M", "VVIX", "SKEW")

    bronze = store.read_bronze_as_of("vix_complex", ingest_date)
    assert len(bronze) == 3
    assert set(bronze["series"]) == {"VIX3M", "VVIX", "SKEW"}


def test_ingest_quarantines_bad_rows_without_losing_good_ones(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {"SKEW": "DATE,SKEW\n01/02/2026,130.0\n01/05/2026,-3.0\n01/06/2026,140.0\n"}
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_vix_complex(store, ["SKEW"], ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 2
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None

    bronze = store.read_bronze_as_of("vix_complex", ingest_date)
    assert -3.0 not in bronze["close"].tolist()

    quarantined = store.read_bronze_as_of("vix_complex__quarantine", ingest_date)
    assert len(quarantined) == 1
    assert quarantined["close"].iloc[0] == -3.0


def test_ingest_all_valid_writes_no_quarantine_partition(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {"SKEW": "DATE,SKEW\n01/02/2026,130.0\n"}
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_vix_complex(store, ["SKEW"], ingest_date=ingest_date, raw=raw)

    assert result.quarantined_rows == 0
    assert result.quarantine_path is None
    with pytest.raises(LookupError):
        store.read_bronze_as_of("vix_complex__quarantine", ingest_date)


def test_ingest_uppercases_requested_series(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {"SKEW": "DATE,SKEW\n01/02/2026,130.0\n"}
    result = ingest_vix_complex(store, ["skew"], ingest_date=dt.date(2026, 1, 6), raw=raw)

    assert result.series == ("SKEW",)
    assert result.valid_rows == 1


def test_ingest_matches_lowercase_raw_keys_against_uppercased_series(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resolved` series names are always uppercase; a `raw` dict keyed in
    lowercase must still be matched, not silently missed and fall through to
    a live fetch (design review, PR #87)."""

    def _fail_if_called(series: str, *, timeout: float = 30.0) -> str:
        raise AssertionError(f"fetch_vix_complex_raw should not be called for {series}")

    monkeypatch.setattr("tail_lab.ingestion.vix_complex.fetch_vix_complex_raw", _fail_if_called)
    store = DeltaLakeStore(tmp_path)
    raw = {"skew": "DATE,SKEW\n01/02/2026,130.0\n"}
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_vix_complex(store, ["SKEW"], ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 1
    assert result.series == ("SKEW",)
    bronze = store.read_bronze_as_of("vix_complex", ingest_date)
    assert bronze["close"].iloc[0] == pytest.approx(130.0)


def test_ingest_defaults_to_the_full_series_catalogue(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {
        "VIX3M": "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2026,18.0,18.5,17.8,18.2\n",
        "VIX9D": "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2026,12.0,12.5,11.8,12.2\n",
        "VVIX": "DATE,VVIX\n01/02/2026,85.0\n",
        "SKEW": "DATE,SKEW\n01/02/2026,130.0\n",
    }
    result = ingest_vix_complex(store, ingest_date=dt.date(2026, 1, 6), raw=raw)

    assert set(result.series) == {"VIX3M", "VIX9D", "VVIX", "SKEW"}
    assert result.valid_rows == 4


def test_ingest_logs_the_full_run_surface(tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    """docs/STANDARDS.md §f: an automated action without a runtime record is
    below the bar, so the run line is part of the contract, not decoration."""
    store = DeltaLakeStore(tmp_path)
    raw = {"SKEW": "DATE,SKEW\n01/02/2026,130.0\n01/05/2026,140.0\n"}
    with caplog.at_level("INFO", logger="tail_lab.ingestion.vix_complex"):
        ingest_vix_complex(store, ["SKEW"], ingest_date=dt.date(2026, 1, 6), raw=raw)

    line = "\n".join(caplog.messages)
    assert "event=ingest.vix_complex" in line
    assert "dataset=vix_complex" in line
    assert "source=cboe_cdn" in line
    assert "series=SKEW" in line
    assert "valid_rows=2" in line
    assert "quarantined_rows=0" in line
