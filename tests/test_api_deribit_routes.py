"""The Deribit write route and its status check (``api/deribit_routes``,
``docs/adr/0032``).

Every test here overrides BOTH ``get_lake_store`` and
``get_quarantine_blobs`` onto one tmp root, so a file-set snapshot of that
root sees everything the route could write -- and nothing can reach the
production lake the checkout's ``.env`` points at.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import get_type_hints

import grimp
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tail_lab.api import deribit_routes
from tail_lab.api.main import app
from tail_lab.api.schemas import DeribitOptionRow, DeribitSnapshotRequest, DeribitSnapshotResponse
from tail_lab.config import Settings
from tail_lab.contracts import deribit_options as contract
from tail_lab.contracts.deribit_options import DERIBIT_BOOKS, DeribitRecord
from tail_lab.ingestion.deribit_options import to_records
from tail_lab.lake.blob_store import BlobStore
from tail_lab.lake.store import DeltaLakeStore
from tests.test_contracts_deribit_options import (
    CREATED_MS,
    NOW,
    NOW_MS,
    book_names,
    record,
    records,
)
from tests.test_ingestion_deribit_options import raw_book, recovered_payloads
from tests.test_scripts_deribit_snapshot import deribit_snapshot

TOKEN = "deribit-token"
BASE = "http://testserver"


class Lake:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = DeltaLakeStore(root)
        self.blobs = BlobStore(root)

    def files(self) -> set[Path]:
        return {p for p in self.root.rglob("*") if p.is_file()}

    def rows(self, currency: str = "BTC") -> int:
        dataset = DERIBIT_BOOKS[currency].dataset
        if not self.store.bronze_ingest_dates(dataset):
            return 0
        return len(self.store.read_bronze_as_of(dataset, NOW.date() + dt.timedelta(days=5)))


@pytest.fixture
def lake(tmp_path: Path) -> Lake:
    root = tmp_path / "lake"
    root.mkdir()
    return Lake(root)


ROUTE_SHA = "route-sha-pinned"


def _settings(token: str | None) -> Settings:
    return Settings(_env_file=None, feedback_token=token, code_sha=ROUTE_SHA)  # type: ignore[call-arg]


@pytest.fixture
def client(lake: Lake, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app.dependency_overrides[deribit_routes.get_lake_store] = lambda: lake.store
    app.dependency_overrides[deribit_routes.get_quarantine_blobs] = lambda: lake.blobs
    monkeypatch.setattr(deribit_routes, "_utcnow", lambda: NOW)
    monkeypatch.setattr(deribit_routes, "get_settings", lambda: _settings(None))
    monkeypatch.setattr("tail_lab.api.auth.get_settings", lambda: _settings(TOKEN))
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(deribit_routes.get_lake_store, None)
        app.dependency_overrides.pop(deribit_routes.get_quarantine_blobs, None)


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def body(recs: list[dict[str, object]], listed: list[str] | None = None) -> dict[str, object]:
    return {
        "rows": recs,
        "listed": [str(r["instrument_name"]) for r in recs] if listed is None else listed,
    }


def post(client: TestClient, currency: str, payload: dict[str, object]) -> dict[str, object]:
    resp = client.post(deribit_snapshot.book_url(BASE, currency), json=payload, headers=_auth())
    out: dict[str, object] = {"status": resp.status_code, **resp.json()}
    return out


# ---- the wire ----------------------------------------------------------------------


def test_the_wire_model_and_the_contract_record_cannot_drift() -> None:
    hints = get_type_hints(DeribitRecord)
    assert set(DeribitRecord.__annotations__) == set(DeribitOptionRow.model_fields)
    for name, field in DeribitOptionRow.model_fields.items():
        assert hints[name] == field.annotation, name


def test_every_record_to_records_emits_has_every_wire_key() -> None:
    """No omitted ``None`` keys: the route forbids none, but a missing key
    and a ``None`` must not mean two different things."""
    book, instruments = raw_book("BTC", 3)
    joined = to_records(book, instruments[:2])
    for rec in joined.records:
        assert set(rec) == set(DeribitOptionRow.model_fields)


def test_a_nan_literal_is_refused_by_the_wire_model() -> None:
    with pytest.raises(ValidationError):
        DeribitOptionRow.model_validate_json(
            json.dumps(record()).replace('"bid": 0.0145', '"bid": NaN', 1)
        )


def test_a_nan_body_over_http_writes_nothing(client: TestClient, lake: Lake) -> None:
    """FastAPI's 422 handler cannot echo a NaN, so this is a 500 -- with
    nothing written. Without ``allow_inf_nan=False`` the NaN bid would
    commit as a null."""
    recs = records(200)
    raw = json.dumps(body(recs)).replace('"bid": 0.0145', '"bid": NaN', 1)
    before = lake.files()
    resp = client.post(
        deribit_snapshot.book_url(BASE, "BTC"),
        content=raw.encode(),
        headers={**_auth(), "Content-Type": "application/json"},
    )
    assert resp.status_code == 500
    assert lake.files() == before


def test_an_out_of_range_creation_timestamp_is_a_422(client: TestClient) -> None:
    recs = records(10)
    recs[3]["creation_timestamp_ms"] = 10**17
    assert post(client, "BTC", body(recs))["status"] == 422


# ---- auth and routing ---------------------------------------------------------------


def test_token_unset_is_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tail_lab.api.auth.get_settings", lambda: _settings(None))
    assert post(client, "BTC", body(records(5)))["status"] == 404


def test_auth_runs_before_the_book_lookup(client: TestClient) -> None:
    """A wrong token on an UNKNOWN book is 401, not 404. The body is valid
    and fresh, or it would 422 before auth ever ran."""
    resp = client.post(
        deribit_snapshot.book_url(BASE, "DOGE"), json=body(records(5)), headers=_auth("wrong")
    )
    assert resp.status_code == 401


def test_an_unknown_book_is_404(client: TestClient) -> None:
    assert post(client, "DOGE", body(records(5)))["status"] == 404


@pytest.mark.parametrize("currency", list(DERIBIT_BOOKS))
def test_the_scripts_url_and_body_reach_a_committing_route(
    client: TestClient, lake: Lake, currency: str
) -> None:
    """The script's own URL builder and body builder against the REAL app: a
    router missing from ``api/main.py``, a case drift (a lowercase book
    reaching rule 1b would 409 every book) or a misnamed ``listed`` (422
    under ``extra="forbid"``) all fail here."""
    joined = to_records(*raw_book(currency, 120))
    out = post(client, currency, deribit_snapshot.book_body(joined))
    assert out["status"] == 200, out
    assert out["committed"] is True
    assert out["dataset"] == DERIBIT_BOOKS[currency].dataset
    assert lake.rows(currency) == 120


def test_a_round_trip_preserves_bid_including_null(client: TestClient, lake: Lake) -> None:
    book, instruments = raw_book("BTC", 150)
    book[4]["bid_price"] = None
    out = post(client, "BTC", deribit_snapshot.book_body(to_records(book, instruments)))
    assert (out["status"], out["committed"], out["rows"]) == (200, True, 150)
    stored = lake.store.read_bronze_as_of("deribit_options_btc", NOW.date()).set_index(
        "instrument_name"
    )
    assert pd.isna(stored.loc[book[4]["instrument_name"], "bid"])
    assert stored.loc[book[5]["instrument_name"], "bid"] == book[5]["bid_price"]


# ---- the rules over HTTP ---------------------------------------------------------------


def test_a_btc_body_posted_to_eth_is_refused_and_writes_nothing(
    client: TestClient, lake: Lake
) -> None:
    before = lake.files()
    out = post(client, "ETH", body(records(50)))
    assert out["status"] == 409
    assert lake.files() == before


def test_a_wrong_book_body_is_refused_even_on_a_captured_day(
    client: TestClient, lake: Lake
) -> None:
    assert post(client, "ETH", body(records(50, "ETH")))["committed"] is True
    before = lake.files()
    assert post(client, "ETH", body(records(50)))["status"] == 409
    assert lake.files() == before


def test_one_wrong_book_row_is_quarantined(client: TestClient, lake: Lake) -> None:
    book, instruments = raw_book("BTC", 200)
    book[7]["quote_currency"] = "ETH"
    out = post(client, "BTC", deribit_snapshot.book_body(to_records(book, instruments)))
    assert (out["status"], out["rows"], out["quarantined"]) == (200, 199, 1)
    blob = lake.blobs.read_json(str(out["quarantine_key"]))
    assert [r["reason"] for r in blob["rows"]] == ["wrong_book"]  # type: ignore[union-attr]


@pytest.mark.parametrize("side", ["book", "instruments"])
def test_a_nan_from_deribit_is_quarantined_invalid_value(
    client: TestClient, lake: Lake, side: str
) -> None:
    book, instruments = raw_book("BTC", 200)
    if side == "book":
        book[9]["bid_price"] = float("nan")
    else:
        instruments[9]["strike"] = float("nan")
    out = post(client, "BTC", deribit_snapshot.book_body(to_records(book, instruments)))
    assert (out["status"], out["rows"], out["quarantined"]) == (200, 199, 1)
    (row,) = lake.blobs.read_json(str(out["quarantine_key"]))["rows"]  # type: ignore[misc]
    assert row["reason"] == "invalid_value"
    assert row["payload"]["invalid_fields"] == ["bid=nan" if side == "book" else "strike=nan"]
    stored = lake.store.read_bronze_as_of("deribit_options_btc", NOW.date())
    assert book[9]["instrument_name"] not in set(stored["instrument_name"])


@pytest.mark.parametrize("bad", [10**17, 2**63 - 1, 0])
def test_an_out_of_window_expiry_over_http_is_one_quarantined_row(
    client: TestClient, bad: int
) -> None:
    recs = records(200)
    recs[11]["expiration_timestamp_ms"] = bad
    out = post(client, "BTC", body(recs))
    assert (out["status"], out["rows"], out["quarantined"]) == (200, 199, 1)


def test_five_bad_of_two_hundred_commit_the_rest(client: TestClient, lake: Lake) -> None:
    recs = records(200)
    for i in (1, 40, 80, 120, 160):
        recs[i]["mark"] = -1.0
    out = post(client, "BTC", body(recs))
    assert (out["status"], out["rows"], out["quarantined"]) == (200, 195, 5)
    assert out["quarantine_share"] == 0.025
    blob = lake.blobs.read_json(str(out["quarantine_key"]))
    assert [r["payload"] for r in blob["rows"]] == [recs[i] for i in (1, 40, 80, 120, 160)]  # type: ignore[union-attr]


def test_every_row_quarantined_writes_only_the_blob(client: TestClient, lake: Lake) -> None:
    out = post(client, "BTC", body(records(20, strike=None)))
    assert (out["status"], out["committed"], out["rows"], out["bronze_path"]) == (
        200,
        False,
        0,
        None,
    )
    assert out["quarantine_share"] == 1.0 and out["quarantine_written"] is True
    assert lake.rows() == 0
    catch_up = records(200, creation_timestamp_ms=CREATED_MS + 90_000)
    catch_up[0]["mark"] = -1.0
    second = post(client, "BTC", body(catch_up))
    assert second["committed"] is True and second["rows"] == 199
    assert second["quarantine_key"] != out["quarantine_key"]
    assert len(lake.blobs.list_json("ops/quarantine/deribit_options_btc")) == 2


def test_already_captured_writes_nothing_even_with_quarantinable_rows(
    client: TestClient, lake: Lake
) -> None:
    assert post(client, "BTC", body(records(50)))["committed"] is True
    before = lake.files()
    out = post(client, "BTC", body(records(50, mark=-1.0)))
    assert (out["already_captured"], out["committed"], out["quarantined"]) == (True, False, 0)
    assert out["quarantine_share"] is None and out["quarantine_written"] is None
    assert lake.files() == before


def _with_listed_extra(n: int, extra: int, expiry: str) -> dict[str, object]:
    recs = records(n)
    return body(recs, [str(r["instrument_name"]) for r in recs] + book_names(extra, expiry=expiry))


@pytest.mark.parametrize(
    ("payload", "now", "status"),
    [
        (body(records(20)), dt.datetime(2026, 10, 30, 8, 4, 30, tzinfo=dt.UTC), 425),
        (body(records(20)), NOW + dt.timedelta(minutes=40), 409),
        (body(records(20, creation_timestamp_ms=NOW_MS + 600_000)), NOW, 409),
        (_with_listed_extra(100, 5, "25DEC26"), NOW, 409),
        (body(records(20), book_names(3, expiry="29OCT26")), NOW, 409),
        (body(records(20, "ETH")), NOW, 409),
        (body([record(n) for n in book_names(10, expiry="29OCT26")]), NOW, 422),
        (body([{**r, "creation_timestamp_ms": 10**17} for r in records(5)]), NOW, 422),
        ({"rows": [], "listed": ["x"]}, NOW, 422),
        ({"rows": records(5), "listed": []}, NOW, 422),
        (body([{**r, "expiration_timestamp_ms": 2**63} for r in records(5)]), NOW, 422),
        (body([{**r, "bid_price": 0.01} for r in records(5)]), NOW, 422),
        ({**body(records(5)), "extra": 1}, NOW, 422),
    ],
    ids=[
        "too-early",
        "stale",
        "future",
        "coverage",
        "no-live-listings",
        "wrong-book",
        "all-expired",
        "bad-timestamp",
        "empty",
        "empty-listed",
        "expiry-past-int64",
        "row-extra-key",
        "extra-key",
    ],
)
def test_no_refusal_writes_anything(
    client: TestClient,
    lake: Lake,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    now: dt.datetime,
    status: int,
) -> None:
    if status == 425:
        early = int(dt.datetime(2026, 10, 30, 8, 4, tzinfo=dt.UTC).timestamp() * 1000)
        payload = body(records(20, creation_timestamp_ms=early))
    lake.store.write_bronze(
        "deribit_options_btc", NOW.date() - dt.timedelta(days=1), pd.DataFrame({"x": [1]})
    )
    monkeypatch.setattr(deribit_routes, "_utcnow", lambda: now)
    before = lake.files()
    assert post(client, "BTC", payload)["status"] == status
    assert lake.files() == before


def test_a_row_less_schema_failure_is_a_500_that_writes_nothing(
    client: TestClient, lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(contract, "STORED_COLUMNS", (*contract.STORED_COLUMNS, "invalid_fields"))
    before = lake.files()
    assert post(client, "BTC", body(records(20)))["status"] == 500
    assert lake.files() == before


def test_a_non_serialisable_quarantine_is_a_500_that_writes_nothing(
    client: TestClient, lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = contract.quarantine_document

    def poisoned(**kwargs: object) -> dict[str, object]:
        return {**real(**kwargs), "poison": {1}}  # type: ignore[arg-type]

    monkeypatch.setattr(contract, "quarantine_document", poisoned)
    recs = records(200)
    recs[3]["mark"] = -1.0
    before = lake.files()
    assert post(client, "BTC", body(recs))["status"] == 500
    assert lake.files() == before


# ---- the quarantine fallback --------------------------------------------------------


def test_a_failed_blob_write_answers_200_and_logs_each_row(
    client: TestClient, lake: Lake, caplog: pytest.LogCaptureFixture
) -> None:
    recs = records(303)
    for i in (5, 6, 200):
        recs[i]["mark"] = -1.0
    (lake.root / "ops").write_text("a file, not a directory")
    with caplog.at_level(logging.INFO):
        out = post(client, "BTC", body(recs))
    assert (out["status"], out["committed"], out["rows"]) == (200, True, 300)
    assert out["quarantine_written"] is False
    lines = recovered_payloads(caplog, deribit_routes.UNWRITTEN_EVENT)
    assert len(lines) == 3
    assert {capture for capture, _ in lines} == {out["quarantine_key"]}
    expected = [
        DeribitOptionRow.model_validate(recs[i]).model_dump(mode="json") for i in (5, 6, 200)
    ]
    assert [payload for _, payload in lines] == expected
    assert_cause_logged(caplog, "api.ingest.deribit_options.quarantine_write_failed")


def assert_cause_logged(caplog: pytest.LogCaptureFixture, event: str) -> None:
    """The fallback never hides WHY the blob write failed: one record names
    the capture and the error's type and message."""
    (failed,) = [r.getMessage() for r in caplog.records if f"event={event} " in r.getMessage()]
    assert re.search(r"capture=ops/quarantine/\S+ error=\w+: \S", failed), failed


def test_a_dataset_write_that_lands_then_raises_still_writes_the_blob(
    client: TestClient, lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = lake.store.write_bronze

    def lands_then_raises(dataset: str, day: dt.date, df: pd.DataFrame) -> str:
        real(dataset, day, df)
        raise OSError("post-commit checkpoint failed")

    monkeypatch.setattr(lake.store, "write_bronze", lands_then_raises)
    recs = records(200)
    recs[3]["mark"] = -1.0
    assert post_raw_status(client, recs) == 500
    assert len(lake.blobs.list_json("ops/quarantine/deribit_options_btc")) == 1


def test_when_both_writes_fail_the_dataset_error_is_the_500_and_rows_are_logged(
    client: TestClient,
    lake: Lake,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fails(*_a: object) -> str:
        raise OSError("dataset write failed")

    monkeypatch.setattr(lake.store, "write_bronze", fails)
    (lake.root / "ops").write_text("a file")
    recs = records(200)
    recs[3]["mark"] = -1.0
    recs[4]["mark"] = -1.0
    with caplog.at_level(logging.INFO):
        assert post_raw_status(client, recs) == 500
    assert len(recovered_payloads(caplog, deribit_routes.UNWRITTEN_EVENT)) == 2


def post_raw_status(client: TestClient, recs: list[dict[str, object]]) -> int:
    resp = client.post(deribit_snapshot.book_url(BASE, "BTC"), json=body(recs), headers=_auth())
    return resp.status_code


# ---- concurrency -------------------------------------------------------------------


def test_two_concurrent_posts_commit_once(lake: Lake, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both requests reach the date-D exists-check together (a barrier), so
    without the lock both see False and both write. With it, the second
    waits, the barrier times out harmlessly, and it answers already-captured."""
    monkeypatch.setattr(deribit_routes, "_utcnow", lambda: NOW)
    monkeypatch.setattr(deribit_routes, "get_settings", lambda: _settings(None))
    monkeypatch.setattr("tail_lab.api.auth.get_settings", lambda: _settings(TOKEN))
    barrier = threading.Barrier(2, timeout=1)
    real = lake.store.bronze_partition_exists

    def exists(dataset: str, day: dt.date) -> bool:
        if day == NOW.date():
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait()
        return real(dataset, day)

    monkeypatch.setattr(lake.store, "bronze_partition_exists", exists)
    recs = records(120)
    recs[0]["mark"] = -1.0
    request = DeribitSnapshotRequest.model_validate(body(recs))
    answers: list[bool] = []

    def call() -> None:
        out = deribit_routes.ingest_deribit_options(
            "btc", request, authorization=f"Bearer {TOKEN}", store=lake.store, blobs=lake.blobs
        )
        answers.append(out.committed)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(answers) == [False, True]
    assert lake.rows() == 119


# ---- status ------------------------------------------------------------------------


def _seed(lake: Lake, currency: str, *days: dt.date) -> None:
    for day in days:
        lake.store.write_bronze(DERIBIT_BOOKS[currency].dataset, day, pd.DataFrame({"x": [1]}))


def test_status_with_no_tables_is_null(client: TestClient) -> None:
    books = client.get("/api/ingest/deribit-options/status").json()["books"]
    assert set(books) == {"btc", "eth", "usdc"}
    assert books["btc"] == {
        "first_snapshot_date": None,
        "last_snapshot_date": None,
        "stale_days": None,
        "partitions": 0,
        "missing_days": None,
    }


#: Far from the real date, so a ``date.today()`` leak in the status route
#: computes nonsense staleness and goes red.
FAR = NOW + dt.timedelta(days=400)


def test_status_counts_staleness_and_gaps_per_book(
    client: TestClient, lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deribit_routes, "_utcnow", lambda: FAR)
    today = FAR.date()
    _seed(lake, "BTC", today - dt.timedelta(days=4), today - dt.timedelta(days=2), today)
    _seed(lake, "ETH", today - dt.timedelta(days=3))
    books = client.get("/api/ingest/deribit-options/status").json()["books"]
    assert books["btc"]["stale_days"] == 0
    assert books["btc"]["partitions"] == 3
    assert books["btc"]["missing_days"] == 2
    assert books["eth"]["stale_days"] == 3
    assert books["eth"]["missing_days"] == 0
    assert books["usdc"]["stale_days"] is None


def test_status_missing_days_is_one_for_one_gap(
    client: TestClient, lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deribit_routes, "_utcnow", lambda: FAR)
    today = FAR.date()
    _seed(lake, "USDC", today - dt.timedelta(days=2), today)
    usdc = client.get("/api/ingest/deribit-options/status").json()["books"]["usdc"]
    assert (usdc["partitions"], usdc["missing_days"]) == (2, 1)


def test_an_int64_edge_expiry_beside_a_null_one_commits_over_http(client: TestClient) -> None:
    recs = records(200)
    recs[4]["expiration_timestamp_ms"] = None
    recs[9]["expiration_timestamp_ms"] = 2**63 - 1
    out = post(client, "BTC", body(recs))
    assert (out["status"], out["rows"], out["quarantined"]) == (200, 199, 1)


def test_a_refusal_detail_carries_the_stable_marker(client: TestClient, lake: Lake) -> None:
    """The client stops retrying a 5xx only on this marker: the app's own
    deterministic refusals write nothing and would refuse again."""
    resp = client.post(
        deribit_snapshot.book_url(BASE, "ETH"), json=body(records(20)), headers=_auth()
    )
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith(contract.REFUSAL_MARKER)


# ---- review round 1, test strength ------------------------------------------------------


def test_a_row_with_an_extra_key_is_refused_by_the_wire_model() -> None:
    """With every field defaulted to ``None``, a renamed key would otherwise
    arrive silently as a null."""
    with pytest.raises(ValidationError):
        DeribitOptionRow.model_validate({**record(), "x": 1})


def test_the_route_logs_every_refusal_and_every_answer(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        assert post(client, "ETH", body(records(20)))["status"] == 409
        assert post(client, "BTC", body(records(20)))["committed"] is True
    messages = [r.getMessage() for r in caplog.records]
    refused = [m for m in messages if m.startswith("event=api.ingest.deribit_options ")]
    assert any(
        "dataset=deribit_options_eth refusal=409 detail=" in m and "body is for book BTC" in m
        for m in refused
    )
    assert any(
        "dataset=deribit_options_btc" in m and "committed=true" in m and "rows_posted=20" in m
        for m in refused
    )


def test_previous_day_is_none_when_only_today_exists(client: TestClient) -> None:
    """``has_earlier_partition`` is strictly earlier: today's own partition
    does not make a missing yesterday a red signal on the first day."""
    assert post(client, "BTC", body(records(20)))["previous_partition_exists"] is None
    again = post(client, "BTC", body(records(20)))
    assert again["already_captured"] is True
    assert again["previous_partition_exists"] is None


def test_the_blob_records_the_apps_code_sha(client: TestClient, lake: Lake) -> None:
    recs = records(200)
    recs[3]["mark"] = -1.0
    out = post(client, "BTC", body(recs))
    assert lake.blobs.read_json(str(out["quarantine_key"]))["code_sha"] == ROUTE_SHA


@pytest.mark.parametrize(
    ("created", "wire_ok"), [(contract.MS_MAX, False), (contract.MS_MIN, True)]
)
def test_the_wire_creation_window_is_half_open(
    client: TestClient, created: int, wire_ok: bool
) -> None:
    """2100-01-01 is refused by the request model (422); 2016-01-01 passes
    the wire and is then refused as stale by the plan (409)."""
    out = post(client, "BTC", body(records(5, creation_timestamp_ms=created)))
    assert out["status"] == (409 if wire_ok else 422)


def test_only_the_write_path_imports_the_deribit_contract() -> None:
    """Nothing reads the Deribit books until the data-terms question is
    answered (ADR 0032). import-linter's contract covers chains into the
    contract but skips api.deribit_routes (it sits inside the source package
    tail_lab.api), so this pins the direct importers of both: a new module
    importing either -- an api sibling reaching DERIBIT_BOOKS through the
    route, a transform, a lake helper -- fails here."""
    graph = grimp.build_graph("tail_lab")
    contract_importers = graph.find_modules_that_directly_import(
        "tail_lab.contracts.deribit_options"
    )
    route_importers = graph.find_modules_that_directly_import("tail_lab.api.deribit_routes")
    assert contract_importers == {
        "tail_lab.ingestion.deribit_options",
        "tail_lab.api.deribit_routes",
        "tail_lab.api.schemas",
    }
    assert route_importers == {"tail_lab.api.main"}


def test_already_captured_reports_the_stored_capture_s_earliest_observation(
    client: TestClient,
) -> None:
    first = records(20, creation_timestamp_ms=CREATED_MS - 600_000)
    first[3]["creation_timestamp_ms"] = CREATED_MS - 600_007
    assert post(client, "BTC", body(first))["captured_observed_at_ms"] is None
    again = post(client, "BTC", body(records(20)))
    assert again["already_captured"] is True
    assert again["captured_observed_at_ms"] == CREATED_MS - 600_007


def test_a_failed_capture_read_is_none_not_a_500(
    client: TestClient, lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert post(client, "BTC", body(records(20)))["committed"] is True

    def broken(*_a: object) -> object:
        raise OSError("read failed")

    monkeypatch.setattr(lake.store, "query", broken)
    again = post(client, "BTC", body(records(20)))
    assert (again["status"], again["already_captured"]) == (200, True)
    assert again["captured_observed_at_ms"] is None


def test_a_landed_attempt_whose_earliest_rows_were_quarantined_is_still_its_own(
    client: TestClient,
) -> None:
    """Reproduces the round-4 false green. The body spans several
    milliseconds (as a live book does), every row at its earliest one is
    quarantined, so the stored partition begins LATER than the body. The
    script must still read that as its own landed attempt: red, with the
    20% share it could not see."""
    book, instruments = raw_book("BTC", 200)
    for row in book[::5]:  # every row at the earliest creation ms
        row["mark_price"] = -1.0
    payload = deribit_snapshot.book_body(to_records(book, instruments))
    first = post(client, "BTC", payload)
    assert (first["committed"], first["quarantined"]) == (True, 40)
    again = post(client, "BTC", payload)  # the retry after a lost response
    assert again["captured_observed_at_ms"] == CREATED_MS + 7
    stamps = [int(r["creation_timestamp"]) for r in book]
    answer = DeribitSnapshotResponse.model_validate(
        {k: v for k, v in again.items() if k != "status"}
    )
    assert not deribit_snapshot._judge(
        "btc", answer, may_have_landed=True, own_range=(min(stamps), max(stamps))
    )


def test_the_capture_read_ignores_a_stale_as_of_resolution(
    client: TestClient, lake: Lake, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The as-of read caches its resolution for 45 s. If anything asked "as
    of D" while only D - 1 existed, a cached read would report D - 1's
    capture for D. The capture read must name partition D itself."""
    day_ms = 86_400_000
    monkeypatch.setattr(deribit_routes, "_utcnow", lambda: NOW - dt.timedelta(days=1))
    assert post(client, "BTC", body(records(20, creation_timestamp_ms=CREATED_MS - day_ms)))[
        "committed"
    ]
    lake.store.read_bronze_column_as_of("deribit_options_btc", NOW.date(), "observed_at")
    monkeypatch.setattr(deribit_routes, "_utcnow", lambda: NOW)
    assert post(client, "BTC", body(records(20)))["committed"] is True
    again = post(client, "BTC", body(records(20)))
    assert again["captured_observed_at_ms"] == CREATED_MS
