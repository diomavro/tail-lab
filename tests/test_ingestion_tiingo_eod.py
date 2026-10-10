"""Tiingo EOD adapter: parse, validate/quarantine, and the write policy.

The write policy is the part that protects every reader: as-of reads take one
whole partition, so a partition missing a symbol would hide that symbol's
history -- a failed fetch must therefore write nothing -- and a symbol whose
dividend or split row failed validation would silently mis-state its yield, so
it is withheld (readers then see ``q`` unknown) rather than half-written.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from pathlib import Path

import pandas as pd
import pytest
import requests

from tail_lab.contracts.tiingo_eod import DATASET
from tail_lab.ingestion import tiingo_eod
from tail_lab.ingestion.tiingo_eod import (
    QUARANTINE_DATASET,
    ingest_tiingo_eod,
    parse_tiingo_prices,
    tiingo_getter,
)
from tail_lab.lake.store import DeltaLakeStore

FIXTURE = Path(__file__).parent / "fixtures" / "tiingo_prices_sample.json"
DAY = dt.date(2026, 10, 10)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    # A retried fetch must never sleep the real 300 s in a test.
    monkeypatch.setattr(tiingo_eod, "_FETCH_BACKOFF_S", 0.0)


def _sample() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = json.loads(FIXTURE.read_text())
    return rows


def _fake(payloads: Mapping[str, object]) -> tiingo_eod.TiingoGet:
    def get(symbol: str, params: Mapping[str, str]) -> object:
        assert params["startDate"] == "1993-01-01"
        return payloads[symbol]

    return get


def test_parse_keeps_the_vendor_cells_and_normalises_the_date() -> None:
    frame = parse_tiingo_prices("nvda", _sample())
    assert list(frame.columns) == [
        "symbol",
        "trade_date",
        "close",
        "adj_close",
        "div_cash",
        "split_factor",
    ]
    split_day = frame.loc[frame["trade_date"] == pd.Timestamp("2024-06-10")].iloc[0]
    assert split_day["split_factor"] == 10.0
    # The close is AS TRADED -- the pre-split day is ~10x the post-split one.
    assert frame.iloc[0]["close"] == pytest.approx(1209.98)
    assert frame.iloc[-1]["div_cash"] == pytest.approx(0.01)


def test_parse_refuses_an_empty_or_non_array_payload() -> None:
    with pytest.raises(ValueError, match="no rows"):
        parse_tiingo_prices("spy", [])
    with pytest.raises(ValueError, match="not a JSON array"):
        parse_tiingo_prices("spy", {"detail": "Not found."})


def test_parse_turns_a_malformed_cell_into_a_null_for_quarantine() -> None:
    rows = _sample()
    rows[1]["divCash"] = "n/a"
    frame = parse_tiingo_prices("nvda", rows)
    assert pd.isna(frame.iloc[1]["div_cash"])


def test_ingest_commits_every_symbol_in_one_partition(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    result = ingest_tiingo_eod(
        store, ["NVDA", "spy"], get=_fake({"nvda": _sample(), "spy": _sample()}), ingest_date=DAY
    )
    assert result.symbols == ("nvda", "spy")
    assert result.valid_rows == 8
    assert not result.skipped
    stored = store.read_bronze_as_of(DATASET, DAY)
    assert set(stored["symbol"]) == {"nvda", "spy"}


def test_a_failed_fetch_writes_nothing(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)

    def get(symbol: str, params: Mapping[str, str]) -> object:
        if symbol == "spy":
            raise requests.exceptions.HTTPError("404")
        return _sample()

    with pytest.raises(requests.exceptions.HTTPError):
        ingest_tiingo_eod(store, ["nvda", "spy"], get=get, ingest_date=DAY)
    assert not store.bronze_partition_exists(DATASET, DAY)


def test_a_symbol_with_a_bad_row_is_withheld_and_quarantined(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    bad = _sample()
    bad[3]["divCash"] = -0.01  # a negative dividend is a feed error
    result = ingest_tiingo_eod(
        store, ["nvda", "spy"], get=_fake({"nvda": bad, "spy": _sample()}), ingest_date=DAY
    )
    assert result.withheld_symbols == ("nvda",)
    assert result.quarantined_rows == 1
    stored = store.read_bronze_as_of(DATASET, DAY)
    # Withheld entirely -- its three valid rows too -- so readers see it absent.
    assert set(stored["symbol"]) == {"spy"}
    assert len(store.read_bronze_as_of(QUARANTINE_DATASET, DAY)) == 1


def test_a_second_run_the_same_day_skips_without_fetching(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest_tiingo_eod(store, ["spy"], get=_fake({"spy": _sample()}), ingest_date=DAY)

    def boom(symbol: str, params: Mapping[str, str]) -> object:
        raise AssertionError("a skipped run must not fetch")

    result = ingest_tiingo_eod(store, ["spy"], get=boom, ingest_date=DAY)
    assert result.skipped
    assert result.bronze_path is None


def test_a_live_fetch_without_a_key_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="api_key"):
        ingest_tiingo_eod(DeltaLakeStore(tmp_path), ["spy"], ingest_date=DAY)


def test_the_getter_spaces_requests_and_redacts_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    def fake_get(url: str, params: Mapping[str, str], timeout: float) -> requests.Response:
        resp = requests.Response()
        resp.status_code = 404  # not transient: no retry, no backoff sleep
        resp._content = b"Not found"
        resp.url = f"{url}?token={params['token']}"
        return resp

    monkeypatch.setattr(tiingo_eod.requests, "get", fake_get)
    get = tiingo_getter("SECRET", throttle_s=7.0, sleep=slept.append)
    for symbol in ("spy", "qqq"):
        with pytest.raises(requests.exceptions.HTTPError) as info:
            get(symbol, {"startDate": "1993-01-01"})
        assert "SECRET" not in str(info.value)
    # The first call is immediate; each later one waits the throttle.
    assert slept == [7.0]


def test_an_infinite_price_or_factor_is_quarantined_not_stored(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    bad = _sample()
    bad[1]["close"] = float("inf")  # JSON allows Infinity; it would read as q = 0
    result = ingest_tiingo_eod(
        store, ["nvda", "spy"], get=_fake({"nvda": bad, "spy": _sample()}), ingest_date=DAY
    )
    assert result.withheld_symbols == ("nvda",)


def test_a_run_where_every_symbol_is_withheld_fails_instead_of_writing_nothing(
    tmp_path: Path,
) -> None:
    # An empty write creates no partition, so last week's snapshot would keep
    # serving the withheld symbols while the run looked green.
    store = DeltaLakeStore(tmp_path)
    bad = _sample()
    bad[0]["divCash"] = -1.0
    with pytest.raises(ValueError, match="every Tiingo symbol was withheld"):
        ingest_tiingo_eod(store, ["nvda"], get=_fake({"nvda": bad}), ingest_date=DAY)
    assert not store.bronze_partition_exists(DATASET, DAY)


def test_the_session_is_the_date_as_written_whatever_the_offset() -> None:
    rows = _sample()
    rows[0]["date"] = "2024-06-06T00:00:00+09:00"  # UTC would make it the 5th
    rows[1]["date"] = "2024-06-07T20:00:00-05:00"  # UTC would make it the 8th
    frame = parse_tiingo_prices("nvda", rows)
    assert list(frame["trade_date"][:2]) == [pd.Timestamp("2024-06-06"), pd.Timestamp("2024-06-07")]


def test_a_boolean_cell_is_malformed_not_one() -> None:
    rows = _sample()
    rows[2]["divCash"] = True
    assert pd.isna(parse_tiingo_prices("nvda", rows).iloc[2]["div_cash"])


def _response(status: int, body: bytes) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body
    resp.url = "https://api.tiingo.com/tiingo/daily/spy/prices?token=SECRET"
    return resp


def test_the_getter_returns_the_parsed_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tiingo_eod.requests,
        "get",
        lambda url, params, timeout: _response(200, FIXTURE.read_bytes()),
    )
    payload = tiingo_getter("SECRET", throttle_s=0.0, sleep=lambda s: None)("spy", {})
    assert payload == _sample()


def test_a_truncated_200_is_retried_like_any_blip(monkeypatch: pytest.MonkeyPatch) -> None:
    # A half-sent body must not kill a 95-minute run on the first try: it is
    # parsed inside the retried call, as every sibling adapter does.
    bodies = [b'[{"date":"2020', b"<html>busy</html>", FIXTURE.read_bytes()]
    monkeypatch.setattr(
        tiingo_eod.requests, "get", lambda url, params, timeout: _response(200, bodies.pop(0))
    )
    payload = tiingo_getter("SECRET", throttle_s=0.0, sleep=lambda s: None)("spy", {})
    assert payload == _sample()
    assert bodies == []


def test_an_empty_200_is_a_failed_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tiingo_eod.requests, "get", lambda url, params, timeout: _response(200, b"")
    )
    with pytest.raises(ValueError, match="empty 200 body"):
        tiingo_getter("SECRET", throttle_s=0.0, sleep=lambda s: None)("spy", {})


def test_the_run_log_counts_dividends_and_splits(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    ingest_tiingo_eod(
        DeltaLakeStore(tmp_path), ["nvda"], get=_fake({"nvda": _sample()}), ingest_date=DAY
    )
    line = next(
        r.getMessage() for r in caplog.records if "event=ingest.tiingo_eod " in r.getMessage()
    )
    # The fixture holds one dividend (2024-06-11) and one 10:1 split (2024-06-10).
    assert "dividend_rows=1" in line and "split_rows=1" in line
