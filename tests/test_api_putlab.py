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

from tail_lab.api import putlab_routes
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
    # The result caches are keyed by params + as_of only (not the store), so
    # clear them: one test's compute must not be served to another test's
    # (different) store, and the compute-path log assertions need a cache miss.
    putlab_routes._LEADERBOARD_CACHE.clear()
    putlab_routes._METRIC_SCREEN_CACHE.clear()
    putlab_routes._BACKTEST_CACHE.clear()
    putlab_routes._SWEEP_CACHE.clear()
    putlab_routes._REGIME_VERDICT_CACHE.clear()
    putlab_routes._ACCURACY_CACHE.clear()
    putlab_routes._REPLICATION_CACHE.clear()
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
        "cost",
        "payoff",
        "net",
    }
    assert first["strike"] == pytest.approx(first["spot"] * 0.95)


def test_backtest_404_unknown_asset(client: TestClient) -> None:
    resp = client.get("/api/putlab/backtest", params={"asset": "nope"})
    assert resp.status_code == 404


def test_backtest_second_call_served_from_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeat combo is memoized: the second identical call returns the cached
    result without recomputing (so preset re-clicks don't re-run the backtest)."""
    params = {"asset": "spy", "notional": 1000, "moneyness_pct": 5, "tenor_weeks": 4, "years": 1}
    first = client.get("/api/putlab/backtest", params=params)
    assert first.status_code == 200

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("compute_put_backtest called on a cache hit")

    monkeypatch.setattr(putlab_routes, "compute_put_backtest", _boom)
    second = client.get("/api/putlab/backtest", params=params)
    assert second.status_code == 200
    assert second.json() == first.json()


def test_sweep_second_call_served_from_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    params = {"asset": "spy", "notional": 1000, "years": 1}
    first = client.get("/api/putlab/sweep", params=params)
    assert first.status_code == 200

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the grid was re-swept on a cache hit")

    monkeypatch.setattr(putlab_routes, "run_sweep", _boom)
    second = client.get("/api/putlab/sweep", params=params)
    assert second.status_code == 200
    assert second.json() == first.json()


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
    assert set(cell) == {
        "moneyness_pct",
        "tenor_weeks",
        "roi_on_premium",
        "annualized_return",
        "n_cycles",
    }
    # Every cell that computed carries a real ROI and at least one roll.
    assert all(isinstance(c["roi_on_premium"], float) and c["n_cycles"] >= 1 for c in body["cells"])


def test_sweep_cell_annualizes_roi(client: TestClient) -> None:
    """Each cell's annualized_return is the geometric annualization of its total
    ROI over the lookback window (the helper the roll engine already uses)."""
    from tail_lab.research.backtest.put_roll import annualized_return

    resp = client.get("/api/putlab/sweep", params={"asset": "spy", "notional": 1000, "years": 2})
    body = resp.json()
    for c in body["cells"]:
        assert c["annualized_return"] == pytest.approx(annualized_return(c["roi_on_premium"], 2.0))


def test_sweep_benchmark_is_spy_buy_and_hold(client: TestClient) -> None:
    """The response carries the SPY buy-and-hold hurdle over the same window:
    last/first - 1 on the trailing round(years*252) closes, then annualized."""
    from tail_lab.research.backtest.put_roll import annualized_return, load_asof_series

    years = 1.0
    resp = client.get(
        "/api/putlab/sweep", params={"asset": "spy", "notional": 1000, "years": years}
    )
    body = resp.json()
    assert body["benchmark_symbol"] == "spy"
    store = app.dependency_overrides[putlab_get_lake_store]()
    prices, _ = load_asof_series(store, "spy", dt.datetime.now(dt.UTC).date())
    window = prices.iloc[-round(years * 252) :]
    total = float(window.iloc[-1]) / float(window.iloc[0]) - 1.0
    assert body["benchmark_total"] == pytest.approx(total)
    assert body["benchmark_annualized"] == pytest.approx(annualized_return(total, years))


def test_sweep_benchmark_none_when_spy_absent(tmp_path: Path) -> None:
    """When SPY history is missing, the benchmark fields degrade to None rather
    than failing the sweep (the asset itself still has data)."""
    store = DeltaLakeStore(tmp_path)
    today = dt.datetime.now(dt.UTC).date()
    _seed_ohlcv(store, "aapl", today)  # no SPY seeded
    app.dependency_overrides[putlab_get_lake_store] = lambda: store
    try:
        body = TestClient(app).get("/api/putlab/sweep", params={"asset": "aapl", "years": 1}).json()
    finally:
        app.dependency_overrides.pop(putlab_get_lake_store, None)
    assert body["benchmark_symbol"] == "spy"
    assert body["benchmark_annualized"] is None
    assert body["benchmark_total"] is None


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
    assert {
        "regime",
        "n_cycles",
        "roi_on_premium",
        "paid_off",
        "hit_rate",
        "biggest_payoff_mult",
    } == set(slice0)


def test_regime_verdict_404_unknown_asset(client: TestClient) -> None:
    assert client.get("/api/putlab/regime-verdict", params={"asset": "nope"}).status_code == 404


def test_portfolio_endpoint(client: TestClient) -> None:
    resp = client.post(
        "/api/putlab/portfolio",
        json={
            "legs": [{"asset": "spy", "moneyness_pct": 5, "tenor_weeks": 4, "weight": 1}],
            "notional": 10000,
            "years": 1,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_premium"] == pytest.approx(10000.0)
    assert body["legs"][0]["asset"] == "spy"
    assert body["equity_curve"]
    assert body["verdict"] in {"confirmed", "regime_only", "failed", "untested"}
    assert len(body["snapshot_ids"]) == 2  # spy + vix


def test_portfolio_empty_legs_422(client: TestClient) -> None:
    resp = client.post("/api/putlab/portfolio", json={"legs": [], "notional": 10000, "years": 1})
    assert resp.status_code == 422


def test_universe_lists_members(client: TestClient) -> None:
    resp = client.get("/api/putlab/universe")
    assert resp.status_code == 200
    members = resp.json()
    assert len(members) >= 30  # broadened universe
    spy = next(m for m in members if m["symbol"] == "SPY")
    assert spy["name"] == "S&P 500 (SPY)"
    assert spy["cadence"] in {"weekly", "monthly"}


def test_leaderboard_ranks_seeded_universe(client: TestClient) -> None:
    resp = client.get(
        "/api/putlab/leaderboard", params={"moneyness_pct": 5, "tenor_weeks": 4, "years": 1}
    )
    assert resp.status_code == 200
    body = resp.json()
    # Only spy has data in the fixture, so only it is ranked; the rest skip.
    assert [r["asset"] for r in body["ranked"]] == ["spy"]
    row = body["ranked"][0]
    assert row["spot"] > 0
    assert set(row) >= {"asset", "name", "spot", "roi_on_premium", "verdict", "hit_rate"}


def test_metric_screen_returns_bakeoff(client: TestClient) -> None:
    resp = client.get(
        "/api/putlab/metric-screen",
        params={"moneyness_pct": 10, "tenor_weeks": 4, "years": 1, "top_k": 2},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {
        "as_of",
        "moneyness_pct",
        "tenor_weeks",
        "lookback_years",
        "top_k",
        "universe_size",
        "baseline_roi",
        "entries",
    }
    # Only spy has data in the fixture, so one name is scored across six screens.
    assert body["universe_size"] == 1
    assert len(body["entries"]) == 6
    entry = body["entries"][0]
    assert set(entry) >= {
        "metric",
        "label",
        "top_k_assets",
        "roi_on_premium",
        "hit_rate",
        "combined_max_drawdown",
        "verdict",
        "regime_slices",
        "spearman_vs_payoff",
        "lift_vs_baseline",
    }
    assert entry["top_k_assets"] == ["spy"]
    assert entry["verdict"] in {"confirmed", "regime_only", "failed", "untested"}


def test_metric_screen_404_without_vix(tmp_path: Path) -> None:
    """No VIX in the store -> the regime timeline is missing -> 404."""
    store = DeltaLakeStore(tmp_path)
    today = dt.datetime.now(dt.UTC).date()
    _seed_ohlcv(store, "spy", today)  # OHLCV but deliberately no VIX
    putlab_routes._METRIC_SCREEN_CACHE.clear()
    app.dependency_overrides[putlab_get_lake_store] = lambda: store
    try:
        resp = TestClient(app).get("/api/putlab/metric-screen", params={"years": 1})
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(putlab_get_lake_store, None)


def test_backtest_carries_spot(client: TestClient) -> None:
    body = client.get("/api/putlab/backtest", params={"asset": "spy", "years": 1}).json()
    assert body["spot"] > 0
    # strike at 5% OOM is spot below-by-5%; the UI shows this in real $.
    assert body["cycles"][0]["strike"] == pytest.approx(body["cycles"][0]["spot"] * 0.95)


def test_regimes_endpoint(client: TestClient) -> None:
    resp = client.get("/api/putlab/regimes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] in {"calm", "elevated", "crisis"}
    assert body["segments"]
    seg = body["segments"][0]
    assert set(seg) == {"regime", "start", "end", "n_days"}
    assert sum(body["day_counts"].values()) == sum(s["n_days"] for s in body["segments"])


def test_data_quality_endpoint(client: TestClient) -> None:
    resp = client.get("/api/putlab/data-quality", params={"asset": "spy"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["asset"] == "spy"
    assert body["n_bars"] > 0
    assert body["n_suspicious"] == len(body["flags"])


def test_data_quality_404_unknown(client: TestClient) -> None:
    assert client.get("/api/putlab/data-quality", params={"asset": "nope"}).status_code == 404


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


def test_accuracy_never_404s_even_with_nothing_to_report(client: TestClient) -> None:
    """The constitutional guarantee. A 404 here would let the frontend render
    an empty space where the size of the error belongs, and an empty space
    reads as "no concerns"."""
    resp = client.get(
        "/api/putlab/accuracy",
        params={"asset": "nosuchticker", "moneyness_pct": 5, "tenor_weeks": 4, "years": 1},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["model"]["applicability"] == "unmeasured"
    assert body["model"]["expected_optimism"] is None
    assert body["data_quality_note"] == "no price snapshot to scan"
    assert body["assumptions"]  # never depends on the lake


def test_accuracy_reports_the_regime_mix_and_the_input_scan(client: TestClient) -> None:
    resp = client.get(
        "/api/putlab/accuracy",
        params={"asset": "spy", "moneyness_pct": 5, "tenor_weeks": 4, "years": 1},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["asset"] == "SPY"
    # The seeded VIX oscillates across all three buckets, so the mix is real.
    assert sum(s["share"] for s in body["model"]["regime_mix"]) == pytest.approx(1.0)
    assert body["data_quality_flags"] is not None
    assert body["model"]["caveat"]


def test_accuracy_is_cached_per_parameter_set(client: TestClient) -> None:
    params = {"asset": "spy", "moneyness_pct": 5, "tenor_weeks": 4, "years": 1}
    first = client.get("/api/putlab/accuracy", params=params).json()
    assert putlab_routes._ACCURACY_CACHE
    assert client.get("/api/putlab/accuracy", params=params).json() == first


def test_a_lake_with_no_cboe_snapshot_caches_the_absent_residual(client: TestClient) -> None:
    """Rediscovering "there is no snapshot" by replaying 438 rolls on every
    request would be the slowest possible way to serve a null."""
    client.get(
        "/api/putlab/accuracy",
        params={"asset": "spy", "moneyness_pct": 5, "tenor_weeks": 4, "years": 1},
    )
    assert putlab_routes._REPLICATION_CACHE
    assert all(cached[1] == [] for cached in putlab_routes._REPLICATION_CACHE.values())
