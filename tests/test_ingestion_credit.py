from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.credit import (
    ingest_credit,
    parse_fred_observations,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore


def _fred_payload(*rows: tuple[str, str, str]) -> dict[str, Any]:
    """Build a minimal FRED series/observations-shaped payload from
    (date, value, realtime_start) triples."""
    return {
        "observations": [
            {"date": date, "value": value, "realtime_start": vintage, "realtime_end": "9999-12-31"}
            for date, value, vintage in rows
        ]
    }


def test_parse_against_realistic_fred_payload() -> None:
    raw = _fred_payload(
        ("2026-01-02", "3.51", "2026-01-02"),
        ("2026-01-05", "3.55", "2026-01-05"),
    )
    df = parse_fred_observations("BAMLH0A0HYM2", raw)

    assert list(df.columns) == ["series_id", "obs_date", "value", "vintage_date"]
    assert (df["series_id"] == "BAMLH0A0HYM2").all()
    assert df["obs_date"].is_monotonic_increasing
    first = df.iloc[0]
    assert first["obs_date"] == pd.Timestamp("2026-01-02")
    assert first["value"] == pytest.approx(3.51)
    assert first["vintage_date"] == pd.Timestamp("2026-01-02")


def test_parse_drops_the_missing_value_sentinel_but_keeps_malformed_rows() -> None:
    """FRED marks a holiday/no-print with the literal "." -- that is "not a
    row" and is dropped; a garbled date or non-numeric value is a *bad* row
    and must survive parsing so quarantine can see it."""
    raw = _fred_payload(
        ("2026-01-02", "3.51", "2026-01-02"),
        ("2026-01-05", ".", "2026-01-05"),
        ("not-a-date", "3.60", "2026-01-06"),
        ("2026-01-07", "not-a-number", "2026-01-07"),
    )
    df = parse_fred_observations("BAMLH0A0HYM2", raw)

    # The "." row is gone; the garbled date and non-numeric value remain.
    assert len(df) == 3
    assert df["obs_date"].isna().sum() == 1
    assert df["value"].isna().sum() == 1


def test_parse_keeps_two_vintages_of_the_same_observation() -> None:
    """A revised series reports the same obs_date more than once, each with
    its own realtime_start -- this is the load-bearing case the whole
    point-in-time contract exists for, so it must survive parsing intact."""
    raw = _fred_payload(
        ("2026-01-02", "3.51", "2026-01-02"),
        ("2026-01-02", "3.48", "2026-02-01"),
    )
    df = parse_fred_observations("BAMLH0A0HYM2", raw)

    assert len(df) == 2
    assert set(df["vintage_date"]) == {pd.Timestamp("2026-01-02"), pd.Timestamp("2026-02-01")}


def test_parse_empty_source_returns_typed_empty_frame() -> None:
    df = parse_fred_observations("BAMLH0A0HYM2", {"observations": []})

    assert df.empty
    assert list(df.columns) == ["series_id", "obs_date", "value", "vintage_date"]


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "series_id": ["BAMLH0A0HYM2", "BAMLH0A0HYM2", "BAMLH0A0HYM2"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
            "value": [3.51, -1.0, 3.49],
            "vintage_date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert len(quarantined) == 1
    assert quarantined["value"].iloc[0] == -1.0


def test_ingest_commits_whole_family_as_one_snapshot(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {
        "BAMLH0A0HYM2": _fred_payload(("2026-01-02", "3.51", "2026-01-02")),
        "BAMLC0A0CM": _fred_payload(("2026-01-02", "1.20", "2026-01-02")),
    }
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_credit(store, ["BAMLH0A0HYM2", "BAMLC0A0CM"], ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 2
    assert result.quarantined_rows == 0
    assert result.quarantine_path is None
    assert result.series_ids == ("BAMLH0A0HYM2", "BAMLC0A0CM")

    bronze = store.read_bronze_as_of("credit", ingest_date)
    assert len(bronze) == 2
    assert set(bronze["series_id"]) == {"BAMLH0A0HYM2", "BAMLC0A0CM"}


def test_ingest_quarantines_bad_rows_without_losing_good_ones(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {
        "BAMLH0A0HYM2": _fred_payload(
            ("2026-01-02", "3.51", "2026-01-02"),
            ("2026-01-05", "-1.0", "2026-01-05"),
        )
    }
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_credit(store, ["BAMLH0A0HYM2"], ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 1
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None

    bronze = store.read_bronze_as_of("credit", ingest_date)
    assert -1.0 not in bronze["value"].tolist()

    quarantined = store.read_bronze_as_of("credit__quarantine", ingest_date)
    assert len(quarantined) == 1
    assert quarantined["value"].iloc[0] == -1.0


def test_ingest_all_valid_writes_no_quarantine_partition(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {"BAMLH0A0HYM2": _fred_payload(("2026-01-02", "3.51", "2026-01-02"))}
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_credit(store, ["BAMLH0A0HYM2"], ingest_date=ingest_date, raw=raw)

    assert result.quarantined_rows == 0
    assert result.quarantine_path is None
    with pytest.raises(LookupError):
        store.read_bronze_as_of("credit__quarantine", ingest_date)


def test_ingest_uppercases_requested_series_ids(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {"BAMLH0A0HYM2": _fred_payload(("2026-01-02", "3.51", "2026-01-02"))}
    result = ingest_credit(store, ["bamlh0a0hym2"], ingest_date=dt.date(2026, 1, 6), raw=raw)

    assert result.series_ids == ("BAMLH0A0HYM2",)
    assert result.valid_rows == 1


def test_ingest_without_api_key_raises_for_a_non_injected_series(tmp_path: Any) -> None:
    """FRED's endpoint is keyed -- a missing api_key must fail loudly rather
    than silently skip a series or attempt an unauthenticated fetch."""
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(ValueError, match="api_key"):
        ingest_credit(store, ["BAMLH0A0HYM2"], ingest_date=dt.date(2026, 1, 6))


def test_ingest_logs_the_full_run_surface(tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    """docs/STANDARDS.md §f: an automated action without a runtime record is
    below the bar, so the run line is part of the contract, not decoration."""
    store = DeltaLakeStore(tmp_path)
    raw = {
        "BAMLH0A0HYM2": _fred_payload(
            ("2026-01-02", "3.51", "2026-01-02"),
            ("2026-01-05", "3.55", "2026-01-05"),
        )
    }
    with caplog.at_level("INFO", logger="tail_lab.ingestion.credit"):
        ingest_credit(store, ["BAMLH0A0HYM2"], ingest_date=dt.date(2026, 1, 6), raw=raw)

    line = "\n".join(caplog.messages)
    assert "event=ingest.credit" in line
    assert "dataset=credit" in line
    assert "source=fred" in line
    assert "series_ids=BAMLH0A0HYM2" in line
    assert "valid_rows=2" in line
    assert "quarantined_rows=0" in line
    assert "first_obs_date=2026-01-02" in line
    assert "last_obs_date=2026-01-05" in line
