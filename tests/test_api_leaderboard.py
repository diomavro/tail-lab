"""API tests for the sensitivity leaderboard route
(``api/leaderboard_routes.py``), exercised through the real HTTP path with a
seeded Delta store."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tail_lab.api.leaderboard_routes import get_lake_store as leaderboard_get_lake_store
from tail_lab.api.main import app
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore


def _seed_ohlcv(
    store: DeltaLakeStore, symbol: str, ingest_date: dt.date, seed: int, n: int = 60
) -> None:
    rng = np.random.default_rng(seed=seed)
    closes = np.clip(100 + np.cumsum(rng.normal(0, 1.0, size=n)), 20, None)
    df = pd.DataFrame(
        {
            "symbol": symbol.upper(),
            "trade_date": pd.date_range(end=ingest_date, periods=n, freq="B"),
            "open": closes,
            "high": closes * 1.002,
            "low": closes * 0.998,
            "close": closes,
            "volume": np.full(n, 1_000_000, dtype=int),
            "adj_close": closes,
        }
    )
    store.write_bronze(dataset_id(symbol), ingest_date, df)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = DeltaLakeStore(tmp_path)
    today = dt.datetime.now(dt.UTC).date()
    _seed_ohlcv(store, "spy", today, seed=1)
    _seed_ohlcv(store, "qqq", today, seed=2)
    _seed_ohlcv(store, "tsla", today, seed=3)
    app.dependency_overrides[leaderboard_get_lake_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(leaderboard_get_lake_store, None)


def test_leaderboard_returns_ranked_rows(client: TestClient) -> None:
    resp = client.get("/api/leaderboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "downside_beta"
    assert body["benchmark"] == "SPY"
    symbols = {row["symbol"] for row in body["rows"]}
    assert symbols == {"SPY", "QQQ", "TSLA"}
    ranks = [row["rank"] for row in body["rows"]]
    assert ranks == sorted(ranks)
    scores = [row["score"] for row in body["rows"]]
    assert scores == sorted(scores, reverse=True)


def test_leaderboard_metric_query_param_selects_co_skewness(client: TestClient) -> None:
    resp = client.get("/api/leaderboard", params={"metric": "co_skewness"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "co_skewness"
    assert all(row["metric"] == "co_skewness" for row in body["rows"])


def test_leaderboard_rejects_unsupported_metric(client: TestClient) -> None:
    resp = client.get("/api/leaderboard", params={"metric": "not_a_real_metric"})
    assert resp.status_code == 422


def test_leaderboard_404_when_benchmark_missing(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    app.dependency_overrides[leaderboard_get_lake_store] = lambda: store
    try:
        client = TestClient(app)
        resp = client.get("/api/leaderboard")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(leaderboard_get_lake_store, None)


def test_leaderboard_emits_structured_run_log(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """§f: a leaderboard build logs its identity + headline outputs."""
    import logging

    with caplog.at_level(logging.INFO, logger="tail_lab.api.leaderboard"):
        client.get("/api/leaderboard")
    line = next(
        r.getMessage() for r in caplog.records if "event=leaderboard.build " in r.getMessage()
    )
    assert "benchmark=SPY" in line
    assert "benchmark_snapshot=" in line
    assert "code_sha=" in line
    assert "n_rows=" in line


def test_leaderboard_miss_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    store = DeltaLakeStore(tmp_path)
    app.dependency_overrides[leaderboard_get_lake_store] = lambda: store
    try:
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="tail_lab.api.leaderboard"):
            client.get("/api/leaderboard")
        assert any("event=leaderboard.miss" in r.getMessage() for r in caplog.records)
    finally:
        app.dependency_overrides.pop(leaderboard_get_lake_store, None)
