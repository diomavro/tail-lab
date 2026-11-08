"""``GET /api/putlab/vol-surface`` serves the session's smiles (ADR 0036).

Why these matter: the route is what the Surface tab's Smile section reads. It
must answer a refusal with a 200 and its reason (an uncollected name is an
answer, not an error), resolve ``as_of`` point-in-time, and log one audit
line per computed surface (STANDARDS §f).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tail_lab.api import vol_surface_routes
from tail_lab.api.main import app
from tail_lab.contracts.option_chain import CALL_DATASET, DATASET
from tail_lab.lake.store import DeltaLakeStore
from tests.test_research_vol_surface_read import (
    CALL_STRIKES,
    PUT_STRIKES,
    SESSION,
    _call,
    _put,
    _rows,
)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = DeltaLakeStore(tmp_path)
    store.write_bronze(DATASET, SESSION, _rows(PUT_STRIKES, _put))
    store.write_bronze(CALL_DATASET, SESSION, _rows(CALL_STRIKES, _call))
    vol_surface_routes._SURFACE_MEMO.clear()
    app.dependency_overrides[vol_surface_routes.get_lake_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(vol_surface_routes.get_lake_store, None)
        vol_surface_routes._SURFACE_MEMO.clear()


def test_it_serves_a_fitted_two_wing_surface(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO"):
        resp = client.get(
            "/api/putlab/vol-surface", params={"symbol": "SPY", "as_of": SESSION.isoformat()}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "spy" and body["two_wing"] is True and body["refusal"] is None
    (slice_,) = body["slices"]
    assert slice_["fit"] == "svi" and slice_["forward_source"] == "parity"
    assert any(
        r.getMessage().startswith("event=api.putlab.vol_surface ") and "fitted=1" in r.getMessage()
        for r in caplog.records
    )


def test_an_uncollected_name_is_a_200_with_its_reason(client: TestClient) -> None:
    resp = client.get(
        "/api/putlab/vol-surface", params={"symbol": "xlk", "as_of": SESSION.isoformat()}
    )
    assert resp.status_code == 200
    assert resp.json()["refusal"].startswith("no xlk put quotes")


def test_vix_is_refused(client: TestClient) -> None:
    resp = client.get(
        "/api/putlab/vol-surface", params={"symbol": "vix", "as_of": SESSION.isoformat()}
    )
    assert resp.status_code == 200 and "futures" in resp.json()["refusal"]
