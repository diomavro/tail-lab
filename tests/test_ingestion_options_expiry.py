from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.options_expiry import (
    ingest_options_expiry,
    parse_yahoo_options_expiry,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore


def test_parse_yahoo_options_expiry_against_fixture(
    options_expiry_yahoo_sample: dict[str, Any],
) -> None:
    df = parse_yahoo_options_expiry("SPY", options_expiry_yahoo_sample)

    assert list(df.columns) == ["symbol", "expiration_date"]
    assert len(df) == 11
    assert (df["symbol"] == "SPY").all()
    # Sorted, unique expiration dates.
    assert df["expiration_date"].is_monotonic_increasing
    assert df["expiration_date"].is_unique
    # First listed expiration in the fixture is the 2026-08-21 unix timestamp.
    assert df["expiration_date"].iloc[0] == pd.Timestamp("2026-08-21")


def test_parse_yahoo_options_expiry_uppercases_symbol(
    options_expiry_yahoo_sample: dict[str, Any],
) -> None:
    df = parse_yahoo_options_expiry("spy", options_expiry_yahoo_sample)
    assert (df["symbol"] == "SPY").all()


def test_parse_yahoo_options_expiry_dedupes_timestamps() -> None:
    raw = {
        "optionChain": {
            "result": [
                {
                    "underlyingSymbol": "QQQ",
                    "expirationDates": [1767312000, 1767312000, 1767398400],
                }
            ]
        }
    }
    df = parse_yahoo_options_expiry("QQQ", raw)
    assert len(df) == 2


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["SPY", None, "SPY"],
            "expiration_date": pd.to_datetime(["2026-09-04", "2026-09-11", "2026-09-18"]),
        }
    )
    valid, quarantined = validate_and_quarantine(df)
    assert len(valid) == 2
    assert len(quarantined) == 1


def test_validate_and_quarantine_all_valid_quarantines_nothing() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["SPY", "SPY"],
            "expiration_date": pd.to_datetime(["2026-09-04", "2026-09-11"]),
        }
    )
    valid, quarantined = validate_and_quarantine(df)
    assert len(valid) == 2
    assert quarantined.empty


def test_ingest_options_expiry_commits_bronze_and_quarantines_bad_rows(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {
        "optionChain": {
            "result": [
                {
                    "underlyingSymbol": "SPY",
                    "expirationDates": [1767312000, 1767398400],
                }
            ]
        }
    }
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_options_expiry(store, "SPY", ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 2
    assert result.quarantined_rows == 0
    assert result.quarantine_path is None

    bronze = store.read_bronze_as_of("options_expiry_spy", ingest_date)
    assert len(bronze) == 2

    with pytest.raises(LookupError):
        store.read_bronze_as_of("options_expiry_spy__quarantine", ingest_date)


def test_ingest_options_expiry_writes_quarantine_partition_for_bad_rows(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A realistic Yahoo payload never itself produces an invalid row (the
    schema's only constraints are a non-null symbol and (symbol,
    expiration_date) uniqueness, both of which ``parse_yahoo_options_expiry``
    already guarantees) -- so the quarantine-commit branch is exercised by
    monkeypatching the parser's output, same as this module's fetch/parse
    split intends: ``ingest_options_expiry`` must still commit whatever
    ``parse_yahoo_options_expiry`` returns through the same validate ->
    quarantine -> bronze path as every other adapter."""
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 1, 6)
    bad = pd.DataFrame(
        {
            "symbol": ["SPY", None],
            "expiration_date": pd.to_datetime(["2026-09-04", "2026-09-11"]),
        }
    )
    monkeypatch.setattr(
        "tail_lab.ingestion.options_expiry.parse_yahoo_options_expiry", lambda symbol, raw: bad
    )

    result = ingest_options_expiry(store, "SPY", ingest_date=ingest_date, raw={})

    assert result.valid_rows == 1
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None

    quarantined = store.read_bronze_as_of("options_expiry_spy__quarantine", ingest_date)
    assert len(quarantined) == 1
