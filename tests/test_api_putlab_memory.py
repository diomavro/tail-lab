"""API tests for the hypothesis-memory routes (``api/putlab_memory_routes.py``):
the token-gated record write path + the public prior-art read."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tail_lab.api.main import app
from tail_lab.api.putlab_memory_routes import get_lake_store as mem_get_lake_store
from tail_lab.api.putlab_memory_routes import get_memory_store as mem_get_memory_store
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.blob_store import BlobStore
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.memory.store import HypothesisMemory

TOKEN = "test-memory-token"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # _require_token calls get_settings() directly (not via Depends); it builds
    # a fresh Settings() each call, so a monkeypatched env var is enough (env
    # takes precedence over .env in pydantic-settings).
    monkeypatch.setenv("TAIL_LAB_FEEDBACK_TOKEN", TOKEN)

    lake = DeltaLakeStore(tmp_path / "lake")
    memory = HypothesisMemory(BlobStore(tmp_path / "mem"))
    today = dt.datetime.now(dt.UTC).date()

    rng = np.random.default_rng(11)
    n = 320
    closes = np.clip(100 + np.cumsum(rng.normal(0, 1.0, size=n)), 20, None)
    dates = pd.date_range(end=today, periods=n, freq="B")
    lake.write_bronze(
        dataset_id("spy"),
        today,
        pd.DataFrame(
            {
                "symbol": "SPY",
                "trade_date": dates,
                "open": closes,
                "high": closes * 1.002,
                "low": closes * 0.998,
                "close": closes,
                "volume": np.full(n, 1_000_000, dtype=int),
                "adj_close": closes,
            }
        ),
    )
    vix = 14 + 12 * (np.sin(np.linspace(0, 12, n)) + 1)
    lake.write_bronze("vix", today, pd.DataFrame({"date": dates, "close": vix}))

    app.dependency_overrides[mem_get_lake_store] = lambda: lake
    app.dependency_overrides[mem_get_memory_store] = lambda: memory
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


_RULE = {"asset": "spy", "moneyness_pct": 5, "tenor_weeks": 4, "years": 1}


def test_record_requires_token(client: TestClient) -> None:
    assert client.post("/api/putlab/memory/record", params=_RULE).status_code == 401
    assert (
        client.post(
            "/api/putlab/memory/record", params=_RULE, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )


def test_record_then_prior_art_accumulates(client: TestClient) -> None:
    auth = {"Authorization": f"Bearer {TOKEN}"}
    # Untested before any record.
    pre = client.get("/api/putlab/memory", params=_RULE).json()
    assert pre["verdict"] == "untested"
    assert pre["outcomes"] == []

    first = client.post("/api/putlab/memory/record", params=_RULE, headers=auth)
    assert first.status_code == 200
    body = first.json()
    assert body["rule_hash"].startswith("h-")
    assert body["recorded"]  # at least one regime recorded
    assert all(r["run_count"] == 1 for r in body["recorded"])

    # Recording the same rule again bumps run_count, not a second node.
    second = client.post("/api/putlab/memory/record", params=_RULE, headers=auth).json()
    assert all(r["run_count"] == 2 for r in second["recorded"])

    art = client.get("/api/putlab/memory", params=_RULE).json()
    assert art["verdict"] in {"confirmed", "regime_only", "failed"}
    assert art["outcomes"]
    assert all(o["run_count"] == 2 for o in art["outcomes"])  # persisted, accumulated


def test_record_404_unknown_asset(client: TestClient) -> None:
    auth = {"Authorization": f"Bearer {TOKEN}"}
    resp = client.post(
        "/api/putlab/memory/record",
        params={"asset": "nope", "moneyness_pct": 5, "tenor_weeks": 4, "years": 1},
        headers=auth,
    )
    assert resp.status_code == 404


def test_prior_art_is_public(client: TestClient) -> None:
    # No token needed for the read.
    assert client.get("/api/putlab/memory", params=_RULE).status_code == 200
