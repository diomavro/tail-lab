"""Tests for the Marks route (``api/marks_routes.py``): status codes for a
name it cannot draw versus a name the lake has nothing for, and that a second
request is served from the memo rather than re-scanning the quote table."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tail_lab.api import marks_routes
from tail_lab.api.main import app
from tail_lab.lake.store import DeltaLakeStore

AS_OF = "2026-01-02"


@pytest.fixture
def store(tmp_path: Path) -> DeltaLakeStore:
    lake = DeltaLakeStore(tmp_path)
    day = pd.Timestamp("2020-03-02")
    lake.write_bronze("vix", dt.date(2026, 1, 1), pd.DataFrame({"date": [day], "close": [20.0]}))
    quote = {
        "underlying": "spy",
        "quote_date": day,
        "expiration": day + pd.Timedelta(days=30),
        "strike": 90.0,
        "bid": 1.0,
        "ask": 1.1,
        "volume": 3,
        "spot": 100.0,
    }
    lake.write_bronze("optionsdx_quotes_spy", dt.date(2026, 1, 1), pd.DataFrame([quote]))
    return lake


@pytest.fixture
def client(store: DeltaLakeStore) -> Iterator[TestClient]:
    marks_routes._CACHE.clear()
    app.dependency_overrides[marks_routes.get_lake_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(marks_routes.get_lake_store, None)
        marks_routes._CACHE.clear()


def test_serves_the_grid(client: TestClient) -> None:
    body = client.get("/api/putlab/marks", params={"symbol": "SPY", "as_of": AS_OF}).json()
    assert body["symbol"] == "spy"
    assert body["bands"] == ["<=2w", "2-6w", "6-17w"]
    (source,) = body["sources"]
    assert source["cells"] == [[0, 10, 1, 1, 1]]
    assert body["regime_sessions"] == {"calm": 0, "elevated": 1, "crisis": 0}


def test_a_name_without_a_depth_axis_is_422_and_an_empty_one_404(client: TestClient) -> None:
    assert (
        client.get("/api/putlab/marks", params={"symbol": "vix", "as_of": AS_OF}).status_code == 422
    )
    assert (
        client.get("/api/putlab/marks", params={"symbol": "qqq", "as_of": AS_OF}).status_code == 404
    )


def test_a_repeat_request_is_served_from_the_memo(
    client: TestClient, store: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = marks_routes.compute_quote_coverage

    def counting(lake: DeltaLakeStore, *, symbol: str, as_of: dt.date) -> object:
        calls.append(symbol)
        return real(lake, symbol=symbol, as_of=as_of)

    monkeypatch.setattr(marks_routes, "compute_quote_coverage", counting)
    for _ in range(2):
        assert (
            client.get("/api/putlab/marks", params={"symbol": "spy", "as_of": AS_OF}).status_code
            == 200
        )
    assert calls == ["spy"]
