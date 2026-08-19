"""API tests for the Put Lab routes (``api/putlab_routes.py``), exercised
through the real HTTP path with a seeded in-memory-ish Delta store."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tail_lab.api.main import app
from tail_lab.api.putlab_routes import get_lake_store as putlab_get_lake_store
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore


def _seed_ohlcv(store: DeltaLakeStore, symbol: str, ingest_date: dt.date, n: int = 320) -> None:
    rng = np.random.default_rng(seed=11)
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


def _seed_vix(store: DeltaLakeStore, ingest_date: dt.date, n: int = 320) -> None:
    vix = 14 + 12 * (np.sin(np.linspace(0, 12, n)) + 1)  # oscillates across regimes
    dates = pd.date_range(end=ingest_date, periods=n, freq="B")
    store.write_bronze("vix", ingest_date, pd.DataFrame({"date": dates, "close": vix}))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = DeltaLakeStore(tmp_path)
    today = dt.datetime.now(dt.UTC).date()
    _seed_ohlcv(store, "spy", today)
    _seed_vix(store, today)
    app.dependency_overrides[putlab_get_lake_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(putlab_get_lake_store, None)


def test_backtest_returns_full_result(client: TestClient) -> None:
    resp = client.get(
        "/api/putlab/backtest",
        params={"asset": "spy", "notional": 1000, "moneyness_pct": 5, "tenor_weeks": 4, "years": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["asset"] == "spy"
    assert body["n_cycles"] >= 1
    assert len(body["cycles"]) == body["n_cycles"]
    assert len(body["equity_curve"]) == body["n_cycles"] + 1  # one seed point + one per cycle
    assert isinstance(body["roi_on_premium"], float)
    assert body["total_premium"] == pytest.approx(body["n_cycles"] * 1000)
    # A cycle carries the model premium and its realized payoff.
    first = body["cycles"][0]
    assert set(first) == {
        "entry_date",
        "expiry_date",
        "spot",
        "strike",
        "sigma",
        "premium",
        "contracts",
        "payoff",
        "net",
    }
    assert first["strike"] == pytest.approx(first["spot"] * 0.95)


def test_backtest_404_unknown_asset(client: TestClient) -> None:
    resp = client.get("/api/putlab/backtest", params={"asset": "nope"})
    assert resp.status_code == 404


def test_backtest_emits_structured_run_log(client: TestClient, caplog) -> None:
    """§f: a backtest logs its identity + params + headline outputs."""
    import logging

    with caplog.at_level(logging.INFO, logger="tail_lab.api.putlab"):
        client.get("/api/putlab/backtest", params={"asset": "spy", "years": 1})
    line = next(
        r.getMessage() for r in caplog.records if "event=putlab.backtest " in r.getMessage()
    )
    assert "asset=spy" in line
    assert "ohlcv_snapshot=" in line  # bronze snapshot id read
    assert "code_sha=" in line
    assert "n_cycles=" in line and "roi_on_premium=" in line  # headline outputs


def test_backtest_miss_is_logged(client: TestClient, caplog) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="tail_lab.api.putlab"):
        client.get("/api/putlab/backtest", params={"asset": "nope"})
    assert any("event=putlab.backtest.miss" in r.getMessage() for r in caplog.records)


def test_backtest_rejects_bad_params(client: TestClient) -> None:
    # moneyness must be 0 < m < 100
    assert (
        client.get("/api/putlab/backtest", params={"asset": "spy", "moneyness_pct": 0}).status_code
        == 422
    )
    assert (
        client.get("/api/putlab/backtest", params={"asset": "spy", "notional": -5}).status_code
        == 422
    )


def test_sweep_returns_grid(client: TestClient) -> None:
    resp = client.get("/api/putlab/sweep", params={"asset": "spy", "notional": 1000, "years": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["asset"] == "spy"
    assert len(body["cells"]) >= 1
    cell = body["cells"][0]
    assert set(cell) == {"moneyness_pct", "tenor_weeks", "roi_on_premium", "n_cycles"}
    # Every cell that computed carries a real ROI and at least one roll.
    assert all(isinstance(c["roi_on_premium"], float) and c["n_cycles"] >= 1 for c in body["cells"])


def test_sweep_404_unknown_asset(client: TestClient) -> None:
    assert client.get("/api/putlab/sweep", params={"asset": "nope"}).status_code == 404


def test_regime_verdict_returns_breakdown(client: TestClient) -> None:
    resp = client.get(
        "/api/putlab/regime-verdict",
        params={"asset": "spy", "moneyness_pct": 5, "tenor_weeks": 4, "years": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["asset"] == "spy"
    assert body["verdict"] in {"confirmed", "regime_only", "failed", "untested"}
    assert body["rule_hash"].startswith("h-")
    assert body["slices"]
    slice0 = body["slices"][0]
    assert set(slice0) == {"regime", "n_cycles", "roi_on_premium", "paid_off"}


def test_regime_verdict_404_unknown_asset(client: TestClient) -> None:
    assert client.get("/api/putlab/regime-verdict", params={"asset": "nope"}).status_code == 404


def test_cadence_known_and_unknown(client: TestClient) -> None:
    known = client.get("/api/putlab/cadence", params={"asset": "spy"})
    assert known.status_code == 200
    assert known.json()["cadence"] == "weekly"
    assert known.json()["symbol"] == "SPY"

    unknown = client.get("/api/putlab/cadence", params={"asset": "zzz"})
    assert unknown.status_code == 200
    body = unknown.json()
    assert body["symbol"] == "ZZZ"
    assert "assumed" in body["label"].lower()  # flagged as a default, not a verified listing
