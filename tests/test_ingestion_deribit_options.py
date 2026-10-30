"""Deribit fetch, join and the ``--local`` writer (``ingestion/deribit_options``).

Nothing here touches the network: ``requests.get`` and ``fetch_book`` are
replaced with synthetic payloads shaped like the 2026-10-30 captures. The
raw-payload builders (:func:`raw_book`) are shared with the route and script
tests.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections.abc import Callable, Mapping
from pathlib import Path

import pandas as pd
import pytest
import requests
from deltalake.exceptions import DeltaError

from tail_lab.contracts import deribit_options as contract
from tail_lab.contracts.deribit_options import DeribitApiError, DeribitRefusal
from tail_lab.ingestion import deribit_options as ingestion
from tail_lab.ingestion.deribit_options import (
    FetchedBook,
    ingest_deribit_options,
    to_records,
)
from tail_lab.lake.blob_store import BlobStore
from tail_lab.lake.store import DeltaLakeStore
from tests.test_contracts_deribit_options import CREATED_MS, NOW, book_names, record

Payload = list[dict[str, object]]
Lake = tuple[DeltaLakeStore, BlobStore, Path]
_OPTION_TYPE = {"P": "put", "C": "call"}


@pytest.fixture(autouse=True)
def _local_lake_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The checkout's ``.env`` points at production Tigris. Nothing here
    builds ``Settings`` -- ingestion never imports ``config`` -- but if
    anything ever did, it would land in this tmp dir, not in today's real
    partition."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAIL_LAB_LAKE_BACKEND", "local")
    monkeypatch.setenv("TAIL_LAB_LAKE_ROOT", str(tmp_path / "lake"))


def raw_book_row(name: str, **overrides: object) -> dict[str, object]:
    """One ``get_book_summary_by_currency`` row, from :func:`record`'s values,
    with the columns the adapter drops (``last``, ``high``...) present too."""
    rec = record(name)
    row: dict[str, object] = {
        "instrument_name": name,
        "creation_timestamp": rec["creation_timestamp_ms"],
        "base_currency": rec["underlying"],
        "quote_currency": rec["premium_currency"],
        "bid_price": rec["bid"],
        "ask_price": rec["ask"],
        "mark_price": rec["mark"],
        "mark_iv": rec["mark_iv_pct"],
        "underlying_price": rec["underlying_price"],
        "underlying_index": rec["underlying_index"],
        "estimated_delivery_price": rec["index_price"],
        "interest_rate": rec["interest_rate"],
        "open_interest": rec["open_interest"],
        "volume": rec["volume"],
        "volume_usd": rec["volume_usd"],
        "last": None,
        "high": None,
        "low": None,
        "price_change": None,
        "mid_price": None,
        "volume_notional": 0.0,
    }
    row.update(overrides)
    return row


def raw_instrument_row(name: str, **overrides: object) -> dict[str, object]:
    """One ``get_instruments`` row. Its own ``creation_timestamp`` is the
    LISTING time, nine days before the book's -- the field-source trap."""
    rec = record(name)
    row: dict[str, object] = {
        "instrument_name": name,
        "creation_timestamp": CREATED_MS - 9 * 86_400_000,
        "expiration_timestamp": rec["expiration_timestamp_ms"],
        "strike": rec["strike"],
        "counter_currency": rec["strike_currency"],
        "option_type": _OPTION_TYPE[str(rec["right"])],
        "contract_size": rec["contract_size"],
        "tick_size": rec["tick_size"],
        "min_trade_amount": rec["min_trade_amount"],
        "kind": "option",
        "is_active": True,
        "base_currency": rec["underlying"],
        "quote_currency": rec["premium_currency"],
    }
    row.update(overrides)
    return row


#: A live book is stamped over a spread, not one instant: measured 2026-10-30,
#: 12-54 distinct ``creation_timestamp`` values over 17-89 ms per response.
CREATION_SPREAD_MS = (0, 7, 19, 33, 61)


def raw_book(currency: str, n: int, *, created_ms: int = CREATED_MS) -> tuple[Payload, Payload]:
    """``n`` live names of ``currency``'s book: ``(book, instruments)``."""
    names = book_names(n, currency)
    return (
        [
            raw_book_row(
                name,
                creation_timestamp=created_ms + CREATION_SPREAD_MS[i % len(CREATION_SPREAD_MS)],
            )
            for i, name in enumerate(names)
        ],
        [raw_instrument_row(name) for name in names],
    )


def fake_fetch(
    monkeypatch: pytest.MonkeyPatch, payloads: Mapping[str, tuple[object, object]]
) -> list[str]:
    """Replace ``fetch_book`` (the reference the writer calls) with
    ``payloads``; returns the currencies fetched, in order."""
    fetched: list[str] = []

    def fetch(currency: str) -> FetchedBook:
        fetched.append(currency)
        book, instruments = payloads[currency]
        return FetchedBook(book, instruments, 2, 1234)

    monkeypatch.setattr(ingestion, "fetch_book", fetch)
    return fetched


@pytest.fixture
def lake(tmp_path: Path) -> Lake:
    root = tmp_path / "lake"
    root.mkdir()
    return DeltaLakeStore(root), BlobStore(root), root


def _files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


def _ingest(
    lake: Lake,
    currency: str = "BTC",
    clock: Callable[[], dt.datetime] = lambda: NOW,
) -> ingestion.DeribitIngestResult:
    store, blobs, _ = lake
    return ingest_deribit_options(currency, store, blobs, clock, "sha-test")


def recovered_payloads(caplog: pytest.LogCaptureFixture, event: str) -> list[tuple[str, object]]:
    """``(capture, payload)`` for each ``event`` line, by the DATA_CONTRACTS
    §16 recovery rule: everything after `` payload=``, outer quotes stripped."""
    out: list[tuple[str, object]] = []
    for rec in caplog.records:
        message = rec.getMessage()
        if not message.startswith(f"event={event} "):
            continue
        capture = message.split(" capture=", 1)[1].split(" ", 1)[0]
        payload = message.split(" payload=", 1)[1]
        out.append((capture, json.loads(payload.removeprefix('"').removesuffix('"'))))
    return out


# ---- fetch ------------------------------------------------------------------------


def _response(status: int, body: bytes | str) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode() if isinstance(body, str) else body
    resp.url = "https://www.deribit.com/api/v2/public/x"
    return resp


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tail_lab.ingestion.sources.time.sleep", lambda _s: None)


def _scripted_get(monkeypatch: pytest.MonkeyPatch, responses: list[requests.Response]) -> list[str]:
    calls: list[str] = []

    def get(url: str, **_kwargs: object) -> requests.Response:
        calls.append(url.rsplit("/", 1)[-1])
        return responses.pop(0)

    monkeypatch.setattr(ingestion.requests, "get", get)
    return calls


def _ok(result: object) -> requests.Response:
    return _response(200, json.dumps({"jsonrpc": "2.0", "result": result}))


def test_fetch_book_reads_the_book_then_the_instruments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _scripted_get(monkeypatch, [_ok([{"a": 1}]), _ok([{"b": 2}])])
    fetched = ingestion.fetch_book("BTC")
    assert calls == ["get_book_summary_by_currency", "get_instruments"]
    assert (fetched.book, fetched.instruments, fetched.attempts) == ([{"a": 1}], [{"b": 2}], 2)
    assert fetched.bytes > 0


def test_a_429_with_a_json_body_is_retried(monkeypatch: pytest.MonkeyPatch, no_sleep: None) -> None:
    calls = _scripted_get(
        monkeypatch, [_response(429, '{"error": "slow down"}'), _ok([1]), _ok([2])]
    )
    assert ingestion.fetch_book("BTC").attempts == 3
    assert calls[0] == calls[1] == "get_book_summary_by_currency"


def test_an_html_200_is_retried(monkeypatch: pytest.MonkeyPatch, no_sleep: None) -> None:
    """A Cloudflare page or a truncated body: ``JSONDecodeError`` is a
    ``RequestException``, which ``retry_transient`` retries."""
    _scripted_get(monkeypatch, [_response(200, "<html>"), _ok([1]), _ok([2])])
    assert ingestion.fetch_book("BTC").attempts == 3


def test_a_400_is_not_retried_and_keeps_deribits_reason(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    calls = _scripted_get(
        monkeypatch, [_response(400, '{"error": {"message": "Invalid params", "code": -32602}}')]
    )
    with pytest.raises(requests.HTTPError, match="Invalid params"):
        ingestion.fetch_book("XYZ")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        "[1, 2]",
        '{"jsonrpc": "2.0", "error": {"code": 10000}}',
        '{"jsonrpc": "2.0", "result": []}',
        '{"jsonrpc": "2.0"}',
    ],
)
def test_an_unusable_200_is_a_deribit_api_error(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    calls = _scripted_get(monkeypatch, [_response(200, body)])
    with pytest.raises(DeribitApiError):
        ingestion.fetch_book("BTC")
    assert len(calls) == 1


# ---- to_records ---------------------------------------------------------------------


def test_to_records_joins_on_name_and_maps_every_field() -> None:
    book, instruments = raw_book("BTC", 2)
    joined = to_records(book, instruments)
    assert joined.records[0] == record(str(book[0]["instrument_name"]))
    assert joined.listed == [str(r["instrument_name"]) for r in instruments]
    assert joined.instruments_dropped == 0


def test_put_and_call_map_to_p_and_c() -> None:
    book, instruments = raw_book("BTC", 2)
    assert [r["right"] for r in to_records(book, instruments).records] == ["P", "C"]


def test_the_observation_time_comes_from_the_book_never_the_instruments() -> None:
    """The instruments row's ``creation_timestamp`` is its listing time, nine
    days old. Had it won, every book would be refused as stale."""
    book, instruments = raw_book("BTC", 1)
    assert to_records(book, instruments).records[0]["creation_timestamp_ms"] == CREATED_MS


def test_an_unmatched_book_row_keeps_null_instrument_fields() -> None:
    book, instruments = raw_book("BTC", 2)
    joined = to_records(book, instruments[:1])
    unmatched = joined.records[1]
    assert unmatched["strike"] is None
    assert unmatched["expiration_timestamp_ms"] is None
    assert unmatched["right"] is None
    assert unmatched["invalid_fields"] is None
    assert json.dumps(unmatched, allow_nan=False)


def test_listed_includes_an_instrument_missing_from_the_book() -> None:
    book, instruments = raw_book("BTC", 3)
    assert to_records(book[:2], instruments).listed[2] == instruments[2]["instrument_name"]


@pytest.mark.parametrize(
    ("side", "field", "value", "expected"),
    [
        ("book", "bid_price", float("nan"), "bid=nan"),
        ("book", "bid_price", True, "bid=True"),
        ("book", "bid_price", "0.01", "bid='0.01'"),
        ("book", "mark_price", 10**400, "mark=" + repr(10**400)[:40]),
        ("instruments", "strike", float("nan"), "strike=nan"),
        ("instruments", "expiration_timestamp", True, "expiration_timestamp_ms=True"),
        ("instruments", "option_type", 5, "right=5"),
        ("instruments", "option_type", "PUT", "right='PUT'"),
    ],
)
def test_a_field_fault_is_none_and_named_by_its_wire_field(
    side: str, field: str, value: object, expected: str
) -> None:
    book, instruments = raw_book("BTC", 2)
    (book if side == "book" else instruments)[1][field] = value
    rec = to_records(book, instruments).records[1]
    assert rec["invalid_fields"] == [expected]
    assert to_records(book, instruments).records[0]["invalid_fields"] is None


def test_malformed_instruments_rows_are_dropped_from_join_and_listed() -> None:
    book, instruments = raw_book("BTC", 3)
    joined = to_records(book, [None, {"strike": 1.0}, *instruments])
    assert joined.instruments_dropped == 2
    assert len(joined.listed) == 3


# ---- the --local writer ------------------------------------------------------------


def test_a_clean_book_commits(monkeypatch: pytest.MonkeyPatch, lake: Lake) -> None:
    fake_fetch(monkeypatch, {"BTC": raw_book("BTC", 120)})
    result = _ingest(lake)
    assert result.committed and result.rows == 120
    assert result.quarantine_written is None and result.quarantine_key is None
    store, _, _ = lake
    assert len(store.read_bronze_as_of("deribit_options_btc", NOW.date())) == 120


def test_already_captured_writes_nothing_even_with_bad_rows(
    monkeypatch: pytest.MonkeyPatch, lake: tuple
) -> None:
    fake_fetch(monkeypatch, {"BTC": raw_book("BTC", 120)})
    _ingest(lake)
    book, instruments = raw_book("BTC", 120)
    book[4]["mark_price"] = -1.0
    fake_fetch(monkeypatch, {"BTC": (book, instruments)})
    before = _files(lake[2])
    result = _ingest(lake)
    assert result.already_captured and not result.committed
    assert result.quarantine_key is None
    assert _files(lake[2]) == before


def _with_bad_rows(n: int, bad: int) -> tuple[Payload, Payload]:
    book, instruments = raw_book("BTC", n)
    for row in book[1 : 1 + bad]:
        row["mark_price"] = -1.0
    return book, instruments


def test_a_failed_blob_write_logs_every_row_one_line_each(
    monkeypatch: pytest.MonkeyPatch, lake: Lake, caplog: pytest.LogCaptureFixture
) -> None:
    """3 quarantined among 300 surviving rows, and the blob write fails (a
    plain file named ``ops`` at the lake root). The dataset still commits,
    and exactly 3 lines carry the payloads under one capture id."""
    book, instruments = _with_bad_rows(303, 3)
    fake_fetch(monkeypatch, {"BTC": (book, instruments)})
    (lake[2] / "ops").write_text("not a directory")
    with caplog.at_level(logging.INFO):
        result = _ingest(lake)
    assert result.committed and result.rows == 300
    assert result.quarantine_written is False
    lines = recovered_payloads(caplog, ingestion.UNWRITTEN_EVENT)
    assert len(lines) == 3
    assert {capture for capture, _ in lines} == {result.quarantine_key}
    expected = to_records(book, instruments).records[1:4]
    assert [payload for _, payload in lines] == expected
    (failed,) = [
        r.getMessage()
        for r in caplog.records
        if "event=ingestion.deribit_options.quarantine_write_failed " in r.getMessage()
    ]
    assert re.search(r"capture=ops/quarantine/\S+ error=\w+: \S", failed), failed


def test_a_dataset_write_that_raises_after_landing_still_writes_the_quarantine(
    monkeypatch: pytest.MonkeyPatch, lake: tuple
) -> None:
    store, blobs, _ = lake
    real = store.write_bronze

    def lands_then_raises(dataset: str, day: dt.date, df: pd.DataFrame) -> str:
        real(dataset, day, df)
        raise DeltaError("CommitFailed after landing")

    monkeypatch.setattr(store, "write_bronze", lands_then_raises)
    fake_fetch(monkeypatch, {"BTC": _with_bad_rows(203, 2)})
    with pytest.raises(DeltaError):
        _ingest(lake)
    (blob,) = blobs.list_json("ops/quarantine/deribit_options_btc")
    assert len(blob["rows"]) == 2  # type: ignore[arg-type]


def test_when_both_writes_fail_the_rows_are_logged_and_the_dataset_error_propagates(
    monkeypatch: pytest.MonkeyPatch, lake: Lake, caplog: pytest.LogCaptureFixture
) -> None:
    store, _, root = lake

    def fails(*_a: object) -> str:
        raise OSError("dataset write failed")

    monkeypatch.setattr(store, "write_bronze", fails)
    (root / "ops").write_text("not a directory")
    fake_fetch(monkeypatch, {"BTC": _with_bad_rows(203, 2)})
    with caplog.at_level(logging.INFO), pytest.raises(OSError, match="dataset write failed"):
        _ingest(lake)
    assert len(recovered_payloads(caplog, ingestion.UNWRITTEN_EVENT)) == 2


def test_a_non_serialisable_quarantine_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, lake: tuple
) -> None:
    """Proven before any write: without the pre-check the dataset commits
    and the blob store's own late ``json.dumps`` loses the quarantine."""
    real = contract.quarantine_document

    def poisoned(**kwargs: object) -> dict[str, object]:
        return {**real(**kwargs), "poison": {1, 2}}  # type: ignore[arg-type]

    monkeypatch.setattr(contract, "quarantine_document", poisoned)
    fake_fetch(monkeypatch, {"BTC": _with_bad_rows(203, 2)})
    with pytest.raises(DeribitRefusal) as caught:
        _ingest(lake)
    assert caught.value.status == 500
    assert _files(lake[2]) == set()


def test_every_row_quarantined_writes_the_blob_and_no_dataset_then_a_catch_up_lands(
    monkeypatch: pytest.MonkeyPatch, lake: tuple
) -> None:
    store, blobs, _ = lake
    book, instruments = raw_book("BTC", 20)
    for row in instruments:
        row["strike"] = None
    fake_fetch(monkeypatch, {"BTC": (book, instruments)})
    first = _ingest(lake)
    assert (first.committed, first.rows, first.bronze_path) == (False, 0, None)
    assert first.quarantine_share == 1.0 and first.quarantine_written
    assert store.bronze_ingest_dates("deribit_options_btc") == []

    later = CREATED_MS + 120_000
    book, instruments = raw_book("BTC", 200, created_ms=later)
    book[3]["mark_price"] = -1.0
    fake_fetch(monkeypatch, {"BTC": (book, instruments)})
    second = _ingest(lake, clock=lambda: NOW + dt.timedelta(minutes=2))
    assert second.committed and second.rows == 199
    assert second.quarantine_key != first.quarantine_key
    assert len(blobs.list_json("ops/quarantine/deribit_options_btc")) == 2


def test_a_blob_with_only_unmatched_rows_and_one_with_typed_rows_both_read_back(
    monkeypatch: pytest.MonkeyPatch, lake: tuple
) -> None:
    """Day 1 quarantines only unmatched rows (every instrument field null),
    day 2 a typed row -- the shape that broke a typed Delta quarantine."""
    _, blobs, _ = lake
    book, instruments = raw_book("BTC", 101)
    fake_fetch(monkeypatch, {"BTC": (book, instruments[:100])})
    day1 = _ingest(lake)
    book2, instruments2 = raw_book("BTC", 100, created_ms=CREATED_MS + 86_400_000)
    book2[5]["mark_price"] = -1.0
    fake_fetch(monkeypatch, {"BTC": (book2, instruments2)})
    day2 = _ingest(lake, clock=lambda: NOW + dt.timedelta(days=1))
    for result, expected in ((day1, book[100]), (day2, book2[5])):
        assert result.quarantine_key is not None
        blob = blobs.read_json(result.quarantine_key)
        (row,) = blob["rows"]  # type: ignore[misc]
        assert row["instrument_name"] == expected["instrument_name"]
        assert row["payload"]["instrument_name"] == expected["instrument_name"]
    assert day2.previous_partition_exists is True


@pytest.mark.parametrize(
    ("mutate", "status"),
    [
        (lambda book, _i: book.__setitem__(slice(None), []), 422),
        (lambda book, _i: [r.update(quote_currency="ETH") for r in book], 409),
        (lambda book, _i: book[50].update(creation_timestamp=10**17), 409),
        (
            lambda _b, inst: inst.extend(
                raw_instrument_row(n) for n in book_names(5, expiry="25DEC26")
            ),
            409,
        ),
        (lambda _b, inst: inst.__setitem__(slice(None), [None]), 409),
    ],
    ids=["empty-book", "wrong-book", "bad-timestamp", "coverage", "no-live-listings"],
)
def test_a_refusal_under_local_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    lake: Lake,
    mutate: Callable[[Payload, list[object]], object],
    status: int | None,
) -> None:
    book, instruments = raw_book("BTC", 120)
    mutate(book, instruments)  # type: ignore[arg-type]
    fake_fetch(monkeypatch, {"BTC": (book, instruments)})
    before = _files(lake[2])
    with pytest.raises((DeribitRefusal, DeribitApiError)) as caught:
        _ingest(lake)
    if status is not None:
        assert isinstance(caught.value, DeribitRefusal) and caught.value.status == status
    assert _files(lake[2]) == before


def _run_log(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith(f"event={ingestion.RUN_EVENT} ")
    ]


def test_the_run_log_records_the_partition_and_dropped_instruments(
    monkeypatch: pytest.MonkeyPatch, lake: Lake, caplog: pytest.LogCaptureFixture
) -> None:
    book, instruments = raw_book("BTC", 120)
    fake_fetch(monkeypatch, {"BTC": (book, [None, {"strike": 1.0}, *instruments])})
    with caplog.at_level(logging.INFO):
        _ingest(lake)
    (line,) = _run_log(caplog)
    assert "instruments_dropped=2" in line
    assert "partition=2026-10-30" in line
    assert "valid=120" in line


def test_the_run_log_records_a_refusal(
    monkeypatch: pytest.MonkeyPatch, lake: Lake, caplog: pytest.LogCaptureFixture
) -> None:
    fake_fetch(monkeypatch, {"BTC": raw_book("BTC", 50, created_ms=CREATED_MS - 40 * 60_000)})
    with caplog.at_level(logging.INFO), pytest.raises(DeribitRefusal):
        _ingest(lake)
    (line,) = _run_log(caplog)
    assert 'refusal="409: stale' in line


def test_previous_day_is_none_when_only_today_exists_under_local(
    monkeypatch: pytest.MonkeyPatch, lake: Lake
) -> None:
    fake_fetch(monkeypatch, {"BTC": raw_book("BTC", 50)})
    assert _ingest(lake).previous_partition_exists is None
    again = _ingest(lake)
    assert again.already_captured and again.previous_partition_exists is None


def test_the_run_log_records_the_observation_span(
    monkeypatch: pytest.MonkeyPatch, lake: Lake, caplog: pytest.LogCaptureFixture
) -> None:
    book, instruments = raw_book("BTC", 120)
    book[0]["creation_timestamp"] = CREATED_MS - 7
    book[1]["creation_timestamp"] = CREATED_MS + 99
    fake_fetch(monkeypatch, {"BTC": (book, instruments)})
    with caplog.at_level(logging.INFO):
        _ingest(lake)
    (line,) = _run_log(caplog)
    assert f"observed_min_ms={CREATED_MS - 7} observed_max_ms={CREATED_MS + 99}" in line


def test_already_captured_under_local_reports_the_stored_capture(
    monkeypatch: pytest.MonkeyPatch, lake: Lake
) -> None:
    fake_fetch(monkeypatch, {"BTC": raw_book("BTC", 50, created_ms=CREATED_MS - 300_000)})
    assert _ingest(lake).captured_observed_at_ms is None
    fake_fetch(monkeypatch, {"BTC": raw_book("BTC", 50)})
    assert _ingest(lake).captured_observed_at_ms == CREATED_MS - 300_000


def test_the_local_capture_read_ignores_a_stale_as_of_resolution(
    monkeypatch: pytest.MonkeyPatch, lake: Lake
) -> None:
    """The --local twin of the route's test: the capture read names
    partition D itself, not a cached "as of D" that resolved to D - 1."""
    store, _, _ = lake
    day_ms = 86_400_000
    fake_fetch(monkeypatch, {"BTC": raw_book("BTC", 50, created_ms=CREATED_MS - day_ms)})
    assert _ingest(lake, clock=lambda: NOW - dt.timedelta(days=1)).committed
    store.read_bronze_column_as_of("deribit_options_btc", NOW.date(), "observed_at")
    fake_fetch(monkeypatch, {"BTC": raw_book("BTC", 50)})
    assert _ingest(lake).committed
    assert _ingest(lake).captured_observed_at_ms == CREATED_MS


@pytest.mark.parametrize("fault", [OSError("read failed"), KeyError("observed_at")])
def test_a_failed_capture_read_under_local_is_none_and_logged(
    monkeypatch: pytest.MonkeyPatch,
    lake: Lake,
    caplog: pytest.LogCaptureFixture,
    fault: Exception,
) -> None:
    store, _, _ = lake
    fake_fetch(monkeypatch, {"BTC": raw_book("BTC", 50)})
    assert _ingest(lake).committed

    def broken(*_a: object) -> object:
        raise fault

    monkeypatch.setattr(store, "query", broken)
    with caplog.at_level(logging.INFO):
        again = _ingest(lake)
    assert again.already_captured
    assert again.captured_observed_at_ms is None
    assert any(
        "event=ingestion.deribit_options.captured_read_failed dataset=deribit_options_btc"
        in r.getMessage()
        for r in caplog.records
    )


def test_fetch_book_uses_the_budgeted_retry_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stale-bound budget test reads these constants; this proves the
    fetch actually uses them."""
    retries: list[tuple[int, float]] = []
    timeouts: list[object] = []
    real = ingestion.retry_transient

    def spy_retry(call: Callable[[], object], **kwargs: object) -> object:
        retries.append((kwargs["attempts"], kwargs["backoff_s"]))  # type: ignore[arg-type]
        return real(call, **kwargs)  # type: ignore[arg-type]

    def get(_url: str, **kwargs: object) -> requests.Response:
        timeouts.append(kwargs["timeout"])
        return _ok([{"instrument_name": "x"}])

    monkeypatch.setattr(ingestion, "retry_transient", spy_retry)
    monkeypatch.setattr(ingestion.requests, "get", get)
    ingestion.fetch_book("BTC")
    budget = (ingestion._FETCH_ATTEMPTS, ingestion._FETCH_BACKOFF_S)
    assert retries == [budget, budget]
    assert timeouts == [ingestion._TIMEOUT_S, ingestion._TIMEOUT_S]
