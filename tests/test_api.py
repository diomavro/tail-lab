from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tail_lab.api.main import app, get_lake_store
from tail_lab.api.schemas import VixStretchResponse
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.transforms.vix import STRETCH_WINDOW


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = DeltaLakeStore(tmp_path)
    rng = np.random.default_rng(seed=1)
    n = STRETCH_WINDOW + 3
    # UTC to match the endpoint's as-of default (and the ingest default) —
    # a local-tz date here mismatches the UTC as-of clock and 404s.
    today = dt.datetime.now(dt.UTC).date()
    closes = (18.0 + rng.normal(size=n)).round(4)
    dates = pd.date_range(end=today, periods=n, freq="D")
    df = pd.DataFrame({"date": dates, "close": closes})
    store.write_bronze("vix", today, df)

    app.dependency_overrides = {}
    get_lake_store.cache_clear()
    original = app.dependency_overrides.get(get_lake_store)
    app.dependency_overrides[get_lake_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_lake_store, None)
        if original is not None:
            app.dependency_overrides[get_lake_store] = original


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_security_headers_present(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_vix_stretch_returns_metric(client: TestClient) -> None:
    resp = client.get("/api/vix/stretch")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"date", "close", "rolling_mean_20d", "rolling_std_20d", "z_score"}
    assert isinstance(body["z_score"], float)
    # Full response-schema-shape check (STANDARDS §correctness "the response
    # schema shape"): validates against the actual DTO, not just key names —
    # catches a field silently changing type (e.g. z_score serialized as str).
    validated = VixStretchResponse(**body)
    assert validated.date.isoformat() == body["date"]
    for field in ("close", "rolling_mean_20d", "rolling_std_20d", "z_score"):
        assert isinstance(body[field], int | float)


def test_vix_stretch_404_when_no_data(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    get_lake_store.cache_clear()
    app.dependency_overrides[get_lake_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.get("/api/vix/stretch")
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_lake_store, None)


def test_vix_stretch_404_when_as_of_predates_any_snapshot(tmp_path: Path) -> None:
    """Data exists, but only *after* the requested as_of -- must 404, not
    silently fall back to the earliest (future-relative-to-as_of) snapshot."""
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 6, 15)
    n = STRETCH_WINDOW + 1
    df = pd.DataFrame(
        {"date": pd.date_range(end=ingest_date, periods=n, freq="D"), "close": [18.0] * n}
    )
    store.write_bronze("vix", ingest_date, df)

    get_lake_store.cache_clear()
    app.dependency_overrides[get_lake_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.get("/api/vix/stretch", params={"as_of": "2026-06-01"})
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_lake_store, None)


def test_vix_stretch_as_of_respects_point_in_time(tmp_path: Path) -> None:
    """A later snapshot with a different metric value must not leak into a
    request for an earlier ``as_of`` -- the API-level counterpart to the
    lake/research adversarial tests, exercised through the real HTTP path."""
    store = DeltaLakeStore(tmp_path)
    day1 = dt.date(2026, 6, 15)
    day2 = dt.date(2026, 6, 16)
    n = STRETCH_WINDOW + 1

    # A slight ramp (not a flat series) so the trailing std -- and hence the
    # z-score -- is well-defined and non-degenerate.
    day1_closes = [18.0 + 0.01 * i for i in range(n)]
    df_day1 = pd.DataFrame(
        {"date": pd.date_range(end=day1, periods=n, freq="D"), "close": day1_closes}
    )
    store.write_bronze("vix", day1, df_day1)

    # day2's snapshot has a wildly different close on the final day -- if it
    # leaked into an as_of=day1 request, the metric would change.
    df_day2 = pd.DataFrame(
        {
            "date": pd.date_range(end=day2, periods=n, freq="D"),
            "close": [*day1_closes[1:], 80.0],
        }
    )
    store.write_bronze("vix", day2, df_day2)

    get_lake_store.cache_clear()
    app.dependency_overrides[get_lake_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp_day1 = c.get("/api/vix/stretch", params={"as_of": day1.isoformat()})
            resp_day2 = c.get("/api/vix/stretch", params={"as_of": day2.isoformat()})
    finally:
        app.dependency_overrides.pop(get_lake_store, None)

    assert resp_day1.status_code == 200
    assert resp_day2.status_code == 200
    body_day1, body_day2 = resp_day1.json(), resp_day2.json()
    assert body_day1["date"] == day1.isoformat()
    assert body_day2["date"] == day2.isoformat()
    assert body_day1["close"] == pytest.approx(day1_closes[-1])
    assert body_day2["close"] == pytest.approx(80.0)
    assert body_day1["z_score"] != body_day2["z_score"]
