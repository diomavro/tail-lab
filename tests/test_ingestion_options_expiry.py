from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.options_expiry import (
    ingest_options_expiry,
    parse_cboe_options_expiry,
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


# --- Cboe primary source (2026-08-21: Yahoo demoted to fallback) ---------


def test_parse_cboe_expiry_against_real_fixture(cboe_chain_sample: dict[str, Any]) -> None:
    df = parse_cboe_options_expiry("SPY", cboe_chain_sample)

    assert list(df.columns) == ["symbol", "expiration_date"]
    assert (df["symbol"] == "SPY").all()
    assert df["expiration_date"].is_monotonic_increasing
    assert df["expiration_date"].is_unique
    # The fixture holds two contracts per expiry across six expiries -- the
    # distinct set, not the contract count, is what this dataset stores.
    assert len(df) == 6
    assert len(cboe_chain_sample["data"]["options"]) == 12


def test_parse_cboe_expiry_decodes_the_occ_symbol() -> None:
    """SPY260821C00200000 -> 2026-08-21. The expiry lives inside the contract
    symbol; getting the YYMMDD slice wrong silently shifts every cadence
    classification downstream."""
    raw = {"data": {"options": [{"option": "SPY260821C00200000"}]}}
    df = parse_cboe_options_expiry("SPY", raw)

    assert len(df) == 1
    assert df["expiration_date"].iloc[0] == pd.Timestamp("2026-08-21")


def test_parse_cboe_expiry_handles_index_roots_and_long_strikes() -> None:
    raw = {"data": {"options": [{"option": "SPXW261218P06000000"}]}}
    df = parse_cboe_options_expiry("SPX", raw)

    assert df["expiration_date"].iloc[0] == pd.Timestamp("2026-12-18")


def test_parse_cboe_expiry_skips_unparseable_symbols_rather_than_guessing() -> None:
    """An unparseable contract symbol tells us nothing about a date, and
    inventing one would put a fabricated expiration into the very dataset
    that exists to record when options actually list."""
    raw = {
        "data": {
            "options": [
                {"option": "SPY260821C00200000"},
                {"option": "GARBAGE"},
                {"option": ""},
            ]
        }
    }
    df = parse_cboe_options_expiry("SPY", raw)
    assert len(df) == 1


def test_parse_cboe_expiry_empty_chain_returns_typed_empty_frame() -> None:
    df = parse_cboe_options_expiry("SPY", {"data": {"options": []}})
    assert df.empty
    assert list(df.columns) == ["symbol", "expiration_date"]


def test_ingest_prefers_cboe_and_records_the_source(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    result = ingest_options_expiry(
        store,
        "SPY",
        ingest_date=dt.date(2026, 1, 6),
        cboe_raw={"data": {"options": [{"option": "SPY260821C00200000"}]}},
    )

    assert result.source_id == "cboe"
    assert result.valid_rows == 1


def test_ingest_falls_back_to_yahoo_when_cboe_chain_is_empty(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    result = ingest_options_expiry(
        store,
        "SPY",
        ingest_date=dt.date(2026, 1, 6),
        cboe_raw={"data": {"options": []}},
        raw={"optionChain": {"result": [{"expirationDates": [1767312000]}]}},
    )

    assert result.source_id == "yahoo"
    assert result.valid_rows == 1
