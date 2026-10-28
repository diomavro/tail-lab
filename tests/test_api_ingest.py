from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
from deltalake.exceptions import SchemaMismatchError
from fastapi.testclient import TestClient

from tail_lab.api.ingest_routes import get_lake_store
from tail_lab.api.main import app
from tail_lab.config import Settings
from tail_lab.contracts.option_chain import (
    CALL_DATASET,
    CALL_QUARANTINE_DATASET,
    DATASET,
    DEFAULT_SNAPSHOT_SYMBOLS,
    QUARANTINE_DATASET,
)
from tail_lab.lake.store import DeltaLakeStore

TOKEN = "sweep-token"


@pytest.fixture
def store(tmp_path: Path) -> DeltaLakeStore:
    return DeltaLakeStore(tmp_path)


@pytest.fixture
def client(store: DeltaLakeStore) -> Iterator[TestClient]:
    get_lake_store.cache_clear()
    app.dependency_overrides[get_lake_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_lake_store, None)


def _set_token(monkeypatch: pytest.MonkeyPatch, token: str | None) -> None:
    """The gate reads ``get_settings()`` directly (not a FastAPI
    ``Depends``), so patch the name as imported into ``ingest_routes``."""
    monkeypatch.setattr(
        "tail_lab.api.auth.get_settings",
        lambda: Settings(feedback_token=token),
    )


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "underlying": "SPY",
        "quote_date": "2026-08-26",
        "expiration": "2026-09-25",
        "strike": 700.0,
        "bid": 1.20,
        "ask": 1.30,
        "volume": 42,
        "open_interest": 811,
        "spot": 767.12,
        "iv": 0.2415,
        "delta": -0.19,
        "theo": 1.25,
    }
    row.update(overrides)
    return row


def _body(
    *rows: dict[str, Any],
    ingest_date: str | None = "2026-08-26",
    symbols: list[str] | None = None,
    market_session: str | None = None,
) -> dict[str, Any]:
    """A sweep body. ``symbols`` defaults to exactly the names in ``rows`` --
    a sweep that got every chain it asked for."""
    body_rows = list(rows) or [_row()]
    body: dict[str, Any] = {
        "rows": body_rows,
        "symbols": symbols or sorted({r["underlying"] for r in body_rows}),
        "market_session": market_session,
    }
    if ingest_date is not None:
        body["ingest_date"] = ingest_date
    return body


# ---- the gate --------------------------------------------------------------


def test_unconfigured_deploy_does_not_advertise_the_route(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, None)
    resp = client.post("/api/ingest/option-chain", json=_body())
    assert resp.status_code == 404


def test_a_missing_bearer_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_token(monkeypatch, TOKEN)
    assert client.post("/api/ingest/option-chain", json=_body()).status_code == 401


def test_a_wrong_bearer_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_token(monkeypatch, TOKEN)
    resp = client.post(
        "/api/ingest/option-chain",
        json=_body(),
        headers={"Authorization": "Bearer nope"},
    )
    assert resp.status_code == 401


# ---- the write -------------------------------------------------------------


def test_an_authorized_sweep_lands_in_bronze(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, TOKEN)
    resp = client.post(
        "/api/ingest/option-chain",
        json=_body(_row(strike=700.0), _row(strike=710.0)),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["rows"] == 2
    assert payload["symbols"] == 1
    assert payload["dataset"] == DATASET
    assert payload["quote_date"] == "2026-08-26"

    stored = store.read_bronze_as_of(DATASET, dt.date(2026, 8, 26))
    assert sorted(stored["strike"].tolist()) == [700.0, 710.0]


def test_an_omitted_iv_round_trips_as_null_not_zero(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The absence has to survive the wire hop. If it arrived as 0.0 the
    dataset would claim the exchange marked a listed put at zero vol."""
    _set_token(monkeypatch, TOKEN)
    row = _row()
    del row["iv"], row["delta"], row["theo"]
    resp = client.post(
        "/api/ingest/option-chain",
        json=_body(row),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert resp.status_code == 200
    stored = store.read_bronze_as_of(DATASET, dt.date(2026, 8, 26))
    assert stored["iv"].isna().all()


def test_a_bad_row_is_quarantined_and_the_rest_of_the_sweep_still_lands(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this replaced: the endpoint validated all-or-nothing and threw
    away a whole 22,006-quote sweep because a few dozen far-OTM strikes had no
    resting offer overnight — while the local path, running the same contract,
    quarantined those rows and kept the rest. For a dataset that cannot be
    backfilled, one unquotable strike must never cost the session."""
    _set_token(monkeypatch, TOKEN)
    resp = client.post(
        "/api/ingest/option-chain",
        json=_body(_row(strike=700.0, ask=0.0), _row(strike=710.0)),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert resp.status_code == 200
    assert resp.json()["rows"] == 1
    assert resp.json()["quarantined"] == 1
    stored = store.read_bronze_as_of(DATASET, dt.date(2026, 8, 26))
    assert stored["strike"].tolist() == [710.0]
    quarantined = store.read_bronze_as_of(f"{DATASET}__quarantine", dt.date(2026, 8, 26))
    assert quarantined["strike"].tolist() == [700.0]


def test_a_sweep_where_nothing_validates_is_still_a_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quarantining is not the same as accepting anything. If not one row
    survives the contract, the sweep is broken and must say so."""
    _set_token(monkeypatch, TOKEN)
    resp = client.post(
        "/api/ingest/option-chain",
        json=_body(_row(ask=0.0)),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 422
    assert "no rows satisfied the contract" in resp.json()["detail"]


def test_an_empty_sweep_is_a_client_error_not_an_empty_partition(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing an empty partition would make a failed sweep indistinguishable
    from a quiet market day on later as-of reads."""
    _set_token(monkeypatch, TOKEN)
    resp = client.post(
        "/api/ingest/option-chain",
        json={"rows": [], "symbols": ["SPY"], "ingest_date": "2026-08-26"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 400


# ---- freshness -------------------------------------------------------------


def test_status_reports_no_snapshot_before_the_first_sweep(client: TestClient) -> None:
    """Public and unauthenticated on purpose — staleness is the kind of thing
    the constitution says has to be visible, not filed."""
    payload = client.get("/api/ingest/option-chain/status").json()
    assert payload["last_quote_date"] is None
    assert payload["rows"] == 0
    assert payload["stale_days"] is None
    assert sorted(payload["missing_symbols"]) == sorted(s.upper() for s in DEFAULT_SNAPSHOT_SYMBOLS)


def test_status_reports_the_last_session_collected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, TOKEN)
    client.post(
        "/api/ingest/option-chain",
        json=_body(_row(strike=700.0), _row(strike=710.0, underlying="QQQ")),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    payload = client.get("/api/ingest/option-chain/status").json()
    assert payload["last_quote_date"] == "2026-08-26"
    assert payload["rows"] == 2
    assert payload["symbols"] == 2
    assert payload["stale_days"] == (dt.date.today() - dt.date(2026, 8, 26)).days


def test_status_names_the_symbols_a_partial_sweep_lost(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sweep that lands 23 of 24 chains is not stale -- ``stale_days`` alone
    would report a healthy day. This is the check the daily agent already
    runs, so a partial loss is now as loud as a wholesale one, instead of
    living only in a scheduled workflow's ``::warning::`` log line that
    nobody reads (``AGENT_TODO.md``, "Retry a failed chain FETCH")."""
    _set_token(monkeypatch, TOKEN)
    client.post(
        "/api/ingest/option-chain",
        json=_body(_row(underlying="SPY")),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    payload = client.get("/api/ingest/option-chain/status").json()
    expected_missing = sorted(s.upper() for s in DEFAULT_SNAPSHOT_SYMBOLS if s.upper() != "SPY")
    assert sorted(payload["missing_symbols"]) == expected_missing
    assert "SPY" not in payload["missing_symbols"]


def test_status_reports_each_wing_from_its_own_dataset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wings are separate POSTs, so the calls can be lost while the puts
    land (``docs/adr/0031``). Puts on 2026-08-26 for SPY and QQQ, calls a
    session earlier for SPY alone: each half of the answer must describe its
    own dataset, not the other wing's and not their union."""
    _set_token(monkeypatch, TOKEN)
    client.post(
        "/api/ingest/option-chain",
        json=_body(_row(strike=700.0), _row(strike=710.0, underlying="QQQ")),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    _post_calls(
        client,
        _body(
            _call_row(quote_date="2026-08-25"),
            _call_row(quote_date="2026-08-25", strike=810.0),
            _call_row(quote_date="2026-08-25", strike=820.0),
            ingest_date="2026-08-25",
        ),
    )

    payload = client.get("/api/ingest/option-chain/status").json()
    calls = payload["calls"]
    assert (payload["dataset"], calls["dataset"]) == (DATASET, CALL_DATASET)
    assert (payload["last_quote_date"], calls["last_quote_date"]) == ("2026-08-26", "2026-08-25")
    assert (payload["rows"], calls["rows"]) == (2, 3)
    assert (payload["symbols"], calls["symbols"]) == (2, 1)
    assert calls["stale_days"] == payload["stale_days"] + 1
    assert "QQQ" not in payload["missing_symbols"]
    assert "QQQ" in calls["missing_symbols"]
    assert "SPY" not in calls["missing_symbols"]


def test_status_reports_a_call_wing_never_collected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Puts landed, calls never did: the put half is fresh, and only the call
    half says so -- a null ``stale_days`` and every symbol missing."""
    _set_token(monkeypatch, TOKEN)
    client.post(
        "/api/ingest/option-chain",
        json=_body(_row()),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    payload = client.get("/api/ingest/option-chain/status").json()
    assert payload["last_quote_date"] == "2026-08-26"
    assert payload["calls"]["last_quote_date"] is None
    assert payload["calls"]["rows"] == 0
    assert payload["calls"]["stale_days"] is None
    assert sorted(payload["calls"]["missing_symbols"]) == sorted(
        s.upper() for s in DEFAULT_SNAPSHOT_SYMBOLS
    )


def test_the_partition_defaults_to_the_session_not_the_server_clock(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no explicit ingest_date the rows decide the partition. Sending
    the runner's today is what put 2026-08-26's session into an
    ingest_date=2026-08-27 partition on the first scheduled sweep."""
    _set_token(monkeypatch, TOKEN)
    resp = client.post(
        "/api/ingest/option-chain",
        json=_body(ingest_date=None),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert resp.status_code == 200
    assert "ingest_date=2026-08-26" in resp.json()["bronze_path"]
    assert len(store.read_bronze_as_of(DATASET, dt.date(2026, 8, 26))) == 1


def test_a_supplied_ingest_date_that_disagrees_with_the_session_is_refused(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by adversarial review 2026-09-25: posting 2026-08-26 quotes
    (``_row``'s default ``quote_date``) with ``ingest_date=2026-08-27``
    returned 200 and claimed the 27th, so that evening's real sweep for the
    27th would read the already-claimed partition and no-op. Refuse the
    mismatch instead of honouring it."""
    _set_token(monkeypatch, TOKEN)
    resp = client.post(
        "/api/ingest/option-chain",
        json=_body(ingest_date="2026-08-27"),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert resp.status_code == 400
    assert "does not match the session" in resp.json()["detail"]
    assert not store.bronze_partition_exists(DATASET, dt.date(2026, 8, 26))
    assert not store.bronze_partition_exists(DATASET, dt.date(2026, 8, 27))


# ---- parity with the local writer --------------------------------------------
#
# The scheduled sweep writes through this route, and for weeks it lacked every
# protection the local adapter gained: it reported no-ops as commits, filed
# laggard symbols under a session they never traded in, had no symbol floor,
# and could not see a frozen feed. Both now run
# ``contracts.option_chain.plan_session_write``; these pin that it is wired.


def _post(client: TestClient, body: dict[str, Any]) -> Any:
    return client.post(
        "/api/ingest/option-chain", json=body, headers={"Authorization": f"Bearer {TOKEN}"}
    )


def test_a_rerun_of_a_captured_session_reports_a_no_op(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured 2026-09-23 23:50 and 2026-09-24 09:36 UTC: the workflow printed
    "committed 20078 rows for 24 symbols (session 2026-09-22)" twice for a
    partition the lake already held -- the log read as healthy while session
    2026-09-23 was being lost."""
    _set_token(monkeypatch, TOKEN)
    assert _post(client, _body(ingest_date=None)).json()["committed"] is True
    assert _post(client, _body(ingest_date=None)).json()["committed"] is False


def test_a_frozen_feed_is_refused_rather_than_no_opped(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, TOKEN)
    _post(client, _body(ingest_date=None))

    resp = _post(client, _body(ingest_date=None, market_session="2026-08-27"))

    assert resp.status_code == 409
    assert "Cboe's feed is behind" in resp.json()["detail"]


def test_a_laggard_symbol_is_quarantined_not_filed_under_the_session(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ARKK case: ``ingest_date=2026-09-08`` holds 412 ARKK rows stamped
    2026-09-04, because Cboe was still serving ARKK's pre-holiday chain and
    this route filed everything under the newest date it saw."""
    _set_token(monkeypatch, TOKEN)
    rows = [
        _row(underlying="SPY"),
        _row(underlying="QQQ"),
        _row(underlying="ARKK", quote_date="2026-08-25"),
    ]

    resp = _post(client, _body(*rows, ingest_date=None))

    assert resp.status_code == 200
    assert resp.json()["symbols_off_session"] == ["ARKK"]
    stored = store.read_bronze_as_of(DATASET, dt.date(2026, 8, 26))
    assert sorted(stored["underlying"].unique()) == ["QQQ", "SPY"]


def test_too_few_chains_cannot_define_a_session(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured 2026-09-19: Cboe 429'd 9 of 24 symbols and the sweep still
    committed the other 15 -- permanently, since bronze is immutable. One
    chain landing out of the full default universe must be refused while
    the session is still recoverable."""
    _set_token(monkeypatch, TOKEN)

    resp = _post(client, _body(ingest_date=None, symbols=list(DEFAULT_SNAPSHOT_SYMBOLS)))

    assert resp.status_code == 409
    assert not store.bronze_partition_exists(DATASET, dt.date(2026, 8, 26))


def test_a_failed_quarantine_write_does_not_fail_a_committed_session(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production's quarantine table carried a stale 14th column, so any
    quarantine write raised SchemaMismatchError AFTER the session committed.
    A 500 there makes the script retry into a no-op and alert on a session
    that was in fact captured."""
    _set_token(monkeypatch, TOKEN)
    real_write = store.write_bronze

    def write(dataset: str, day: dt.date, frame: Any) -> str:
        if dataset.endswith("__quarantine"):
            raise SchemaMismatchError("SchemaMismatchError: 13 vs 14")
        return real_write(dataset, day, frame)

    monkeypatch.setattr(store, "write_bronze", write)

    resp = _post(client, _body(_row(strike=700.0, ask=0.0), _row(strike=710.0)))

    assert resp.status_code == 200
    assert resp.json()["committed"] is True
    assert store.read_bronze_as_of(DATASET, dt.date(2026, 8, 26))["strike"].tolist() == [710.0]


def test_a_full_production_sweep_matches_lowercase_requests_to_uppercase_rows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The script requests ``spy`` and Cboe's rows say ``SPY``. If that match
    ever breaks, every real sweep reads as 0 of 24 chains and is refused --
    every session lost, while tests built from ``_body()``'s already-uppercase
    names stay green."""
    _set_token(monkeypatch, TOKEN)
    rows = [_row(underlying=s.upper()) for s in DEFAULT_SNAPSHOT_SYMBOLS]

    resp = _post(client, _body(*rows, ingest_date=None, symbols=list(DEFAULT_SNAPSHOT_SYMBOLS)))

    assert resp.status_code == 200
    assert resp.json()["committed"] is True
    assert resp.json()["symbols"] == len(DEFAULT_SNAPSHOT_SYMBOLS)


def test_a_tolerated_partial_sweep_names_the_chains_it_lost(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """22 of 24 clears the floor and commits -- and those two chains are gone
    for that session. The response is the only place the script can learn
    their names for its ``::warning::``; a silent partial is the ARKK/XBI
    class of loss nobody notices."""
    _set_token(monkeypatch, TOKEN)
    landed, lost = DEFAULT_SNAPSHOT_SYMBOLS[:-2], DEFAULT_SNAPSHOT_SYMBOLS[-2:]
    rows = [_row(underlying=s.upper()) for s in landed]

    resp = _post(client, _body(*rows, ingest_date=None, symbols=list(DEFAULT_SNAPSHOT_SYMBOLS)))

    assert resp.status_code == 200
    assert resp.json()["committed"] is True
    assert sorted(resp.json()["symbols_failed"]) == sorted(s.upper() for s in lost)


def test_a_mid_session_post_is_too_early_not_a_refusal(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """425, not 409: the session is still trading and the previous one is in
    the lake, so the script must exit 0 rather than raise a false alarm."""
    _set_token(monkeypatch, TOKEN)
    _post(client, _body(ingest_date=None))  # 2026-08-26 captured

    monkeypatch.setattr(
        "tail_lab.api.ingest_routes._utcnow", lambda: dt.datetime(2026, 8, 27, 15, 0, tzinfo=dt.UTC)
    )
    resp = _post(
        client,
        _body(_row(quote_date="2026-08-27"), ingest_date=None, market_session="2026-08-26"),
    )

    assert resp.status_code == 425
    assert not store.bronze_partition_exists(DATASET, dt.date(2026, 8, 27))


def test_a_bug_in_the_quarantine_write_is_not_swallowed(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only lake-write failures are survivable after the session commits
    (Rule 14); a bug must surface, not be logged away."""
    _set_token(monkeypatch, TOKEN)
    real_write = store.write_bronze

    def write(dataset: str, day: dt.date, frame: Any) -> str:
        if dataset.endswith("__quarantine"):
            raise KeyError("a bug, not a lake failure")
        return real_write(dataset, day, frame)

    monkeypatch.setattr(store, "write_bronze", write)

    with pytest.raises(KeyError):
        _post(client, _body(_row(strike=700.0, ask=0.0), _row(strike=710.0)))


# ---- the call wing (docs/adr/0031) -------------------------------------------

CALLS = "/api/ingest/option-chain-calls"


def _call_row(**overrides: object) -> dict[str, object]:
    return _row(**{"strike": 800.0, "delta": 0.31, **overrides})


def _post_calls(client: TestClient, body: Mapping[str, object]) -> httpx.Response:
    return client.post(CALLS, json=body, headers={"Authorization": f"Bearer {TOKEN}"})


def test_a_call_sweep_lands_in_the_call_dataset_and_never_the_put_one(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every reader of ``option_chain_snapshot`` assumes a put; a call row in
    it would be read as one. The call route writes only its own dataset."""
    _set_token(monkeypatch, TOKEN)
    resp = _post_calls(client, _body(_call_row(strike=800.0), _call_row(strike=900.0, delta=0.05)))

    assert resp.status_code == 200
    assert resp.json()["dataset"] == CALL_DATASET
    assert resp.json()["committed"] is True
    day = dt.date(2026, 8, 26)
    assert sorted(store.read_bronze_as_of(CALL_DATASET, day)["strike"]) == [800.0, 900.0]
    assert not store.bronze_partition_exists(DATASET, day)


def test_the_call_route_validates_against_the_call_contract(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A put-signed delta is quarantined on the call route -- into the CALL
    quarantine -- and a call-signed one is kept; the put route would do the
    reverse. A hundred calls carry the one stray row, well under
    MAX_WRONG_SIGN_SHARE, so only that row is quarantined."""
    _set_token(monkeypatch, TOKEN)
    calls = [_call_row(strike=900.0 + i) for i in range(100)]
    resp = _post_calls(client, _body(*calls, _call_row(strike=810.0, delta=-0.3)))

    assert resp.status_code == 200
    assert (resp.json()["rows"], resp.json()["quarantined"]) == (100, 1)
    day = dt.date(2026, 8, 26)
    assert store.read_bronze_as_of(CALL_QUARANTINE_DATASET, day)["strike"].tolist() == [810.0]
    assert not store.bronze_partition_exists(QUARANTINE_DATASET, day)


def test_call_rows_posted_to_the_put_route_are_refused_whole(
    client: TestClient,
    store: DeltaLakeStore,
    monkeypatch: pytest.MonkeyPatch,
    cboe_chain_calls_sample: dict[str, object],
) -> None:
    """The put contract has no ``right`` column and accepts a null delta, so
    the fixture's zero-filled ITM calls (615, 660) pass it. Split-then-commit
    used to write those two calls into ``option_chain_snapshot`` -- immutably,
    for every put reader -- while quarantining the rest. The body must be
    refused before anything is written."""
    from tail_lab.ingestion.option_chain import sweep_to_records

    _set_token(monkeypatch, TOKEN)
    _puts, swept = sweep_to_records(["SPY"], fetch=lambda _s: cboe_chain_calls_sample)
    # The script's json.dumps sends NaN; httpx refuses it, so send null instead.
    calls = [
        {k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()}
        for r in swept
    ]
    assert any(r["delta"] is None for r in calls), "needs a zero-filled (null-delta) call"

    resp = _post(client, _body(*calls, ingest_date=None))

    assert resp.status_code == 422
    assert "other wing" in resp.json()["detail"]
    assert resp.json()["detail"].endswith(f"nothing written to {DATASET}")
    day = dt.date(2026, 10, 26)
    assert not store.bronze_partition_exists(DATASET, day)
    assert not store.bronze_partition_exists(QUARANTINE_DATASET, day)


def test_put_rows_posted_to_the_call_route_are_refused_whole(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, TOKEN)
    puts = [_row(strike=700.0), _row(strike=710.0), _row(strike=720.0, delta=None, iv=None)]
    resp = _post_calls(client, _body(*puts))

    assert resp.status_code == 422
    assert resp.json()["detail"].endswith(f"nothing written to {CALL_DATASET}")
    day = dt.date(2026, 8, 26)
    assert not store.bronze_partition_exists(CALL_DATASET, day)
    assert not store.bronze_partition_exists(CALL_QUARANTINE_DATASET, day)


@pytest.mark.parametrize(("wrong", "status"), [(20, 200), (21, 422)])
def test_the_wrong_sign_guard_tolerates_a_fifth_and_no_more(
    client: TestClient,
    store: DeltaLakeStore,
    monkeypatch: pytest.MonkeyPatch,
    wrong: int,
    status: int,
) -> None:
    """The guard is a share, not "any": twenty garbled rows in a hundred are
    quarantined alone. Twenty-one is past MAX_WRONG_SIGN_SHARE, so the symbol
    is quarantined whole -- and as the body's only symbol, nothing lands."""
    _set_token(monkeypatch, TOKEN)
    rows = [_row(strike=500.0 + i) for i in range(100 - wrong)]
    rows += [_row(strike=700.0 + i, delta=0.4) for i in range(wrong)]
    resp = _post(client, _body(*rows))

    assert resp.status_code == status
    if status == 200:
        assert (resp.json()["rows"], resp.json()["quarantined"]) == (80, 20)
    else:
        assert not store.bronze_partition_exists(DATASET, dt.date(2026, 8, 26))


@pytest.mark.parametrize("wing", ["P", "C"])
def test_one_symbols_wrong_signs_cost_that_symbol_not_the_session(
    client: TestClient,
    store: DeltaLakeStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    wing: str,
) -> None:
    """24 chains, one of which carries the other wing's delta sign throughout
    (4% of the body's signed rows). Refusing the body whole would lose the
    session on this path while the local writer committed the other 23. The
    misrouted symbol is quarantined whole -- its null-delta row too -- and
    the floor lets the other 23 land."""
    _set_token(monkeypatch, TOKEN)
    sign, post, dataset, quarantine = (
        (-1.0, _post, DATASET, QUARANTINE_DATASET)
        if wing == "P"
        else (1.0, _post_calls, CALL_DATASET, CALL_QUARANTINE_DATASET)
    )
    names = [f"S{i:02d}" for i in range(23)] + ["XLE"]
    rows = [
        _row(underlying=name, strike=700.0 + k, delta=sign * 0.19 * (-1 if name == "XLE" else 1))
        for name in names
        for k in range(20)
    ]
    rows.append(_row(underlying="XLE", strike=650.0, delta=None, iv=None))
    with caplog.at_level("INFO"):
        resp = post(client, _body(*rows))

    assert resp.status_code == 200, resp.text
    answer = resp.json()
    assert (answer["rows"], answer["quarantined"]) == (460, 21)
    assert answer["symbols_failed"] == ["XLE"]
    assert answer["symbols_misrouted"] == ["XLE"]
    (event,) = (
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("event=api.ingest.option_chain ")
    )
    assert "symbols_misrouted=XLE " in event
    # The misrouted warning is the only server-side record of WHICH route
    # quarantined a whole symbol (STANDARDS §f), so it must name the wing.
    (warning,) = (
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("event=api.ingest.option_chain.misrouted ")
    )
    assert f"dataset={dataset} symbols=XLE " in warning
    day = dt.date(2026, 8, 26)
    assert "XLE" not in set(store.read_bronze_as_of(dataset, day)["underlying"])
    assert set(store.read_bronze_as_of(quarantine, day)["underlying"]) == {"XLE"}


def _glitched_put_sweep(flipped: int) -> list[dict[str, Any]]:
    """24 put chains of 40 signed rows; FXI, HYG and TLT each carry
    ``flipped`` call-signed deltas."""
    names = [f"S{i:02d}" for i in range(21)] + ["FXI", "HYG", "TLT"]
    return [
        _row(
            underlying=name,
            strike=500.0 + k,
            delta=0.19 if name in {"FXI", "HYG", "TLT"} and k < flipped else -0.19,
        )
        for name in names
        for k in range(40)
    ]


def test_a_sign_glitch_on_three_put_chains_costs_their_bad_rows_not_the_session(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review A6, measured on the 2026-10-26 live bodies: 10 flipped deltas in
    each of FXI, HYG and TLT made the misrouting guard quarantine all three
    chains, the floor counted them lost, and the put session was refused --
    where the contract alone (without the misrouting guard) commits it with
    the 30 bad rows quarantined. 2 of 40 (5%)
    here is a glitch, not a misrouted chain: only those rows go."""
    _set_token(monkeypatch, TOKEN)
    resp = _post(client, _body(*_glitched_put_sweep(2)))

    assert resp.status_code == 200, resp.text
    answer = resp.json()
    assert (answer["rows"], answer["quarantined"]) == (24 * 40 - 6, 6)
    assert (answer["symbols_failed"], answer["symbols_misrouted"]) == ([], [])
    assert {"FXI", "HYG", "TLT"} <= set(
        store.read_bronze_as_of(DATASET, dt.date(2026, 8, 26))["underlying"]
    )


def test_a_refusal_names_misrouted_chains_apart_from_missing_ones(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three chains past the share are quarantined whole and the floor
    refuses -- but they had quotes, so the 409 must not list them as
    "Missing" under advice to re-run, which sends the same chains."""
    _set_token(monkeypatch, TOKEN)
    resp = _post(client, _body(*_glitched_put_sweep(10)))

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "Missing" not in detail.replace("not missing", "")
    assert "Re-run before" not in detail
    assert "DEFAULT_SNAPSHOT_SYMBOLS" not in detail
    assert "Misrouted, not missing: FXI, HYG, TLT" in detail
    assert not store.bronze_partition_exists(DATASET, dt.date(2026, 8, 26))


def test_a_body_carrying_both_wings_is_refused_by_the_call_route(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer that concatenated the wings sends the call route a body whose
    signed deltas are ~43% puts (16,665 of 38,607 on 2026-10-26). A half
    threshold accepted it and committed the null-delta put as a call; here
    4 of 10 signed rows are puts, and nothing may be written."""
    _set_token(monkeypatch, TOKEN)
    calls = [_call_row(strike=800.0 + i) for i in range(6)]
    puts = [_row(strike=600.0 + i) for i in range(4)]
    zero_filled_put = _row(strike=650.0, delta=None, iv=None)
    resp = _post_calls(client, _body(*calls, *puts, zero_filled_put))

    assert resp.status_code == 422
    assert "other wing" in resp.json()["detail"]
    day = dt.date(2026, 8, 26)
    assert not store.bronze_partition_exists(CALL_DATASET, day)
    assert not store.bronze_partition_exists(CALL_QUARANTINE_DATASET, day)


def test_a_call_rerun_is_a_no_op_and_independent_of_the_puts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotent like the put route, and each wing has its own partition:
    puts already captured must not make the first call POST a no-op."""
    _set_token(monkeypatch, TOKEN)
    assert _post(client, _body(ingest_date=None)).json()["committed"] is True
    assert _post_calls(client, _body(_call_row(), ingest_date=None)).json()["committed"] is True
    assert _post_calls(client, _body(_call_row(), ingest_date=None)).json()["committed"] is False


@pytest.mark.parametrize(
    ("token", "header", "status"),
    [(None, None, 404), (TOKEN, None, 401), (TOKEN, "Bearer nope", 401)],
)
def test_the_call_route_has_the_same_gate(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    token: str | None,
    header: str | None,
    status: int,
) -> None:
    _set_token(monkeypatch, token)
    headers = {"Authorization": header} if header else {}
    assert client.post(CALLS, json=_body(_call_row()), headers=headers).status_code == status


def test_the_call_route_refuses_too_few_chains(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, TOKEN)
    resp = _post_calls(
        client, _body(_call_row(), ingest_date=None, symbols=list(DEFAULT_SNAPSHOT_SYMBOLS))
    )
    assert resp.status_code == 409
    assert not store.bronze_partition_exists(CALL_DATASET, dt.date(2026, 8, 26))


def test_the_call_route_refuses_a_frozen_feed_by_its_own_witness(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stale-feed check reads the CALL dataset's partitions."""
    _set_token(monkeypatch, TOKEN)
    _post_calls(client, _body(_call_row(), ingest_date=None))
    resp = _post_calls(client, _body(_call_row(), ingest_date=None, market_session="2026-08-27"))
    assert resp.status_code == 409
    assert "Cboe's feed is behind" in resp.json()["detail"]


def test_the_call_route_answers_too_early_mid_session(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, TOKEN)
    _post_calls(client, _body(_call_row(), ingest_date=None))
    monkeypatch.setattr(
        "tail_lab.api.ingest_routes._utcnow", lambda: dt.datetime(2026, 8, 27, 15, 0, tzinfo=dt.UTC)
    )
    resp = _post_calls(
        client,
        _body(_call_row(quote_date="2026-08-27"), ingest_date=None, market_session="2026-08-26"),
    )
    assert resp.status_code == 425
    assert not store.bronze_partition_exists(CALL_DATASET, dt.date(2026, 8, 27))


def test_each_route_logs_its_own_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """STANDARDS §f: the audit line names the route's event and dataset."""
    _set_token(monkeypatch, TOKEN)
    with caplog.at_level("INFO"):
        _post(client, _body())
        _post_calls(client, _body(_call_row()))
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("event=api.")]
    assert any(
        m.startswith("event=api.ingest.option_chain ") and f"dataset={DATASET} " in m for m in lines
    )
    assert any(
        m.startswith("event=api.ingest.option_chain ") and f"dataset={CALL_DATASET} " in m
        for m in lines
    )


def test_a_failed_call_quarantine_write_logs_the_call_event(
    client: TestClient,
    store: DeltaLakeStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _set_token(monkeypatch, TOKEN)
    real_write = store.write_bronze

    def write(dataset: str, day: dt.date, frame: pd.DataFrame) -> str:
        if dataset.endswith("__quarantine"):
            raise SchemaMismatchError("SchemaMismatchError: 13 vs 14")
        return real_write(dataset, day, frame)

    monkeypatch.setattr(store, "write_bronze", write)
    with caplog.at_level("ERROR"):
        resp = _post_calls(client, _body(_call_row(strike=800.0, ask=0.0), _call_row(strike=810.0)))

    assert resp.status_code == 200
    failure = next(r for r in caplog.records if "quarantine_write_failed" in r.getMessage())
    assert failure.getMessage().startswith(
        f"event=api.ingest.option_chain.quarantine_write_failed dataset={CALL_QUARANTINE_DATASET} "
    )
