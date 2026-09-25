from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tail_lab.api.ingest_routes import get_lake_store
from tail_lab.api.main import app
from tail_lab.config import Settings
from tail_lab.contracts.option_chain import DATASET, DEFAULT_SNAPSHOT_SYMBOLS
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
            raise RuntimeError("SchemaMismatchError: 13 vs 14")
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
