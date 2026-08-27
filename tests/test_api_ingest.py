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
from tail_lab.contracts.option_chain import DATASET
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
    """``_require_token`` reads ``get_settings()`` directly (not a FastAPI
    ``Depends``), so patch the name as imported into ``ingest_routes``."""
    monkeypatch.setattr(
        "tail_lab.api.ingest_routes.get_settings",
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


def _body(*rows: dict[str, Any], ingest_date: str = "2026-08-26") -> dict[str, Any]:
    return {"rows": list(rows) or [_row()], "ingest_date": ingest_date}


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


def test_rows_are_revalidated_server_side(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trusted caller is not the same as a well-formed payload, and bronze
    is immutable once written — so the contract is enforced here too, not
    only in the workflow that produced the rows."""
    _set_token(monkeypatch, TOKEN)
    resp = client.post(
        "/api/ingest/option-chain",
        json=_body(_row(ask=0.0)),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 422
    assert "contract violation" in resp.json()["detail"]


def test_an_empty_sweep_is_a_client_error_not_an_empty_partition(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing an empty partition would make a failed sweep indistinguishable
    from a quiet market day on later as-of reads."""
    _set_token(monkeypatch, TOKEN)
    resp = client.post(
        "/api/ingest/option-chain",
        json={"rows": [], "ingest_date": "2026-08-26"},
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


def test_the_partition_defaults_to_the_session_not_the_server_clock(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no explicit ingest_date the rows decide the partition. Sending
    the runner's today is what put 2026-08-26's session into an
    ingest_date=2026-08-27 partition on the first scheduled sweep."""
    _set_token(monkeypatch, TOKEN)
    resp = client.post(
        "/api/ingest/option-chain",
        json={"rows": [_row()]},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert resp.status_code == 200
    assert "ingest_date=2026-08-26" in resp.json()["bronze_path"]
    assert len(store.read_bronze_as_of(DATASET, dt.date(2026, 8, 26))) == 1
