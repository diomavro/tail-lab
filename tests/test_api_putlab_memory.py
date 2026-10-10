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

    # This lake has no Tiingo snapshot, so every roll priced at an unknown
    # q = 0: the runs are filed under the "none" hash and read back as legacy,
    # never as measured evidence.
    art = client.get("/api/putlab/memory", params=_RULE).json()
    assert body["rule_hash"] == art["legacy"]["rule_hash"] != art["rule_hash"]
    assert art["verdict"] == "untested" and art["outcomes"] == []
    legacy = art["legacy"]
    assert legacy["verdict"] in {"confirmed", "regime_only", "failed"}
    assert legacy["note"] == "priced without dividends (q = 0)"
    assert all(o["run_count"] == 2 for o in legacy["outcomes"])  # persisted, accumulated
    assert all(o["spec"]["dividends"] == "none" for o in legacy["outcomes"])


def test_a_dividend_measured_run_is_filed_under_its_own_hash(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    from tail_lab.research import dividends
    from tests.test_research_dividends import seed_tiingo_eod

    dividends._memo.clear()
    lake = app.dependency_overrides[mem_get_lake_store]()
    seed_tiingo_eod(lake, ["spy"], dt.datetime.now(dt.UTC).date())
    auth = {"Authorization": f"Bearer {TOKEN}"}
    recorded = client.post("/api/putlab/memory/record", params=_RULE, headers=auth).json()
    art = client.get("/api/putlab/memory", params=_RULE).json()
    # Stored under the measured hash, the spec saying so -- and nothing legacy.
    assert recorded["rule_hash"] == art["rule_hash"]
    assert art["outcomes"] and all(o["spec"]["dividends"] == "measured" for o in art["outcomes"])
    # The run id names the dividend data the run priced with, so a re-record on
    # a new Tiingo snapshot is a different run.
    assert all("#tiingo_eod@" in o["last_run_id"] for o in art["outcomes"])
    lines = [
        r.getMessage() for r in caplog.records if "event=putlab.memory.record " in r.getMessage()
    ]
    assert lines and all(
        "dividend_snapshot=tiingo_eod@" in m and "dividends=measured" in m for m in lines
    )
    assert art["legacy"] is None


def test_pre_dividend_records_keep_their_hash() -> None:
    # Pinned before RuleSpec gained `dividends`: every stored record is keyed by
    # this hash, so changing it would orphan the whole memory.
    from tail_lab.contracts.hypothesis import RuleSpec

    spec = RuleSpec(asset="spy", moneyness_pct=5, tenor_weeks=4, lookback_years=4)
    assert spec.rule_hash() == "h-a0360216"
    assert spec.model_copy(update={"dividends": "measured"}).rule_hash() != "h-a0360216"


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


def test_a_record_cites_the_snapshot_its_prices_used_even_if_one_lands_mid_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A weekly ingest can land between the backtest and the run id: the record
    # must name the data the prices came from, so it reads the snapshot once.
    from tail_lab.research import dividends
    from tests.test_research_dividends import seed_tiingo_eod

    dividends._memo.clear()
    lake = app.dependency_overrides[mem_get_lake_store]()
    seed_tiingo_eod(lake, ["spy"], dt.datetime.now(dt.UTC).date())
    real = type(lake).bronze_snapshot_id
    reads = {"n": 0}

    def later_after_first(self: object, dataset: str, as_of: dt.date) -> str:
        snap = real(lake, dataset, as_of)
        if dataset == "tiingo_eod":
            reads["n"] += 1
            return snap if reads["n"] == 1 else "tiingo_eod@LATER#ingest-landed"
        return snap

    monkeypatch.setattr(type(lake), "bronze_snapshot_id", later_after_first)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    client.post("/api/putlab/memory/record", params=_RULE, headers=auth)
    art = client.get("/api/putlab/memory", params=_RULE).json()
    assert art["outcomes"]
    assert all("LATER" not in o["last_run_id"] for o in art["outcomes"])
