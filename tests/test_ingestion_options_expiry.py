from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest
import requests

from tail_lab.ingestion import options_expiry, sources
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


# ---- transient Cboe faults ---------------------------------------------------


class _Resp:
    def __init__(self, status: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)  # type: ignore[arg-type]

    def json(self) -> dict[str, Any]:
        return self._payload


_CHAIN = {"data": {"options": [{"option": "SPY260925P00500000"}]}}


def _scripted(monkeypatch: pytest.MonkeyPatch, responses: list[_Resp]) -> list[str]:
    calls: list[str] = []

    def fake_get(url: str, **_kw: Any) -> _Resp:
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(options_expiry.requests, "get", fake_get)
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)
    return calls


def test_a_transient_cboe_fault_is_retried_not_escalated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The daily refresh pulls this for all 24 snapshot symbols; one Cboe 503
    on any of them used to turn the whole refresh red -- with Yahoo, the only
    fallback, 429ing everyone. A blip must cost a retry, not the day."""
    calls = _scripted(monkeypatch, [_Resp(503), _Resp(429), _Resp(200, _CHAIN)])

    assert options_expiry.fetch_cboe_chain_raw("spy") == _CHAIN
    assert len(calls) == 3
    assert all(url.endswith("/SPY.json") for url in calls)


def test_a_refusal_is_not_retried_and_the_index_form_is_tried_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 403/404 means Cboe does not list the bare root -- it files index
    chains under ``_SPX``. Retrying the refusal only burns the backoff."""
    calls = _scripted(monkeypatch, [_Resp(403), _Resp(200, _CHAIN)])

    assert options_expiry.fetch_cboe_chain_raw("spx") == _CHAIN
    assert [url.rsplit("/", 1)[-1] for url in calls] == ["SPX.json", "_SPX.json"]


def test_a_persistent_fault_still_fails_after_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _scripted(monkeypatch, [_Resp(503)] * 6)

    with pytest.raises(ValueError, match="no usable chain"):
        options_expiry.fetch_cboe_chain_raw("spy")
    assert len(calls) == 2 * options_expiry._FETCH_ATTEMPTS


def test_a_hung_cboe_stays_inside_the_refresh_budget() -> None:
    """The daily refresh's systemd unit (TimeoutStartSec=120min) is sized on
    ~165s of worst case per symbol for this step. Retries multiply the
    per-request timeout, so raising either knob without re-sizing the unit
    gets the refresh killed mid-loop -- later symbols and the FRED step never
    run, and the only alert says "timeout"."""
    import inspect

    def default_timeout(fn: Any) -> float:
        return float(inspect.signature(fn).parameters["timeout"].default)

    attempts, backoff = options_expiry._FETCH_ATTEMPTS, options_expiry._FETCH_BACKOFF_S
    cboe_worst = 2 * (
        attempts * default_timeout(options_expiry.fetch_cboe_chain_raw) + (attempts - 1) * backoff
    )
    # Yahoo's fallback is three requests (cookie, crumb, options). Read from
    # the code, not restated: a hardcoded 3 x 15 here once let that default
    # drift to 60s with every test green (adversarial review, 2026-09-24).
    yahoo_fallback = 3 * default_timeout(options_expiry.fetch_options_expiry_raw)

    assert cboe_worst + yahoo_fallback <= 165
