from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tail_lab.api.main import app, get_lake_store
from tail_lab.lake.store import LocalParquetLakeStore
from tail_lab.transforms.vix import STRETCH_WINDOW


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = LocalParquetLakeStore(tmp_path)
    rng = np.random.default_rng(seed=1)
    n = STRETCH_WINDOW + 3
    today = dt.date.today()
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


def test_vix_stretch_returns_metric(client: TestClient) -> None:
    resp = client.get("/api/vix/stretch")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"date", "close", "rolling_mean_20d", "rolling_std_20d", "z_score"}
    assert isinstance(body["z_score"], float)


def test_vix_stretch_404_when_no_data(tmp_path: Path) -> None:
    store = LocalParquetLakeStore(tmp_path)
    get_lake_store.cache_clear()
    app.dependency_overrides[get_lake_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.get("/api/vix/stretch")
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_lake_store, None)
