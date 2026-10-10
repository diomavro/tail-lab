"""API tests for the Put Lab routes (``api/putlab_routes.py``), exercised
through the real HTTP path with a seeded in-memory-ish Delta store."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tail_lab.api import putlab_routes
from tail_lab.api.main import app
from tail_lab.api.putlab_routes import get_lake_store as putlab_get_lake_store
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.contracts.options_expiry import dataset_id as options_expiry_dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.hedge_overlay import HedgeOverlayResult


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
    putlab_routes._SURFACE_CACHE.clear()
    putlab_routes._PREVIEW_CACHE.clear()
    putlab_routes._OVERLAY_CACHE.clear()
    putlab_routes._PLAN_CACHE.clear()
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
    # A cycle carries the premium it was filled at and its realized payoff.
    # `quote_basis` rides along so the mark-to-market lookup can find the same
    # contract again on a split-adjusted name (1.0 on the model path). `q` and
    # `q_source` are the dividend yield the roll was priced with and where it came
    # from -- this test lake has no Tiingo snapshot, so every roll says "unknown".
    first = body["cycles"][0]
    assert first["q"] == 0.0
    assert first["q_source"] == "unknown"
    assert body["q_source"] == "none"
    assert set(first) == {
        "q",
        "q_source",
        "t_years",
        "premium_floored",
        "entry_moneyness_pct",
        "entry_delta",
        "entry_date",
        "expiry_date",
        "spot",
        "strike",
        "sigma",
        "premium",
        "quote_basis",
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
    # docs/STANDARDS.md: a result without its snapshot id(s) and code SHA is
    # not trustworthy and should not be surfaced -- the screen (Dio's
    # standing north-star directive) now carries both in the payload, not
    # only the run log.
    assert body["code_sha"] == "unknown"  # honest local default, never None
    assert body["snapshot_ids"]  # VIX + spy's own OHLCV snapshot


def test_leaderboard_and_roll_schedule_share_one_screen(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The screen is minutes cold in production; a second read of the same
    question, from either route, must be served from the memo."""
    calls: list[float] = []
    real = putlab_routes.rank_universe

    def counted(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(putlab_routes, "rank_universe", counted)
    params = {"moneyness_pct": 5, "tenor_weeks": 4, "years": 1}
    assert client.get("/api/putlab/leaderboard", params=params).status_code == 200
    assert client.get("/api/putlab/leaderboard", params=params).status_code == 200
    assert client.get("/api/putlab/roll-schedule", params=params).status_code == 200
    assert len(calls) == 1


def test_startup_warms_the_opening_ranking_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A deploy restarts with an empty memo; production (fly.toml) starts the
    daily warm at startup, on the routes' own store; tests and local runs do not."""
    import threading

    from tail_lab.api import main
    from tail_lab.config import Settings

    warmed: list[object] = []
    ran = threading.Event()

    def record(store: object) -> None:
        warmed.append(store)
        ran.set()

    store = DeltaLakeStore(tmp_path)
    monkeypatch.setattr(main, "warm_leaderboard_daily", record)
    monkeypatch.setattr(main, "putlab_lake_store", lambda: store)
    monkeypatch.setattr(main, "get_settings", lambda: Settings(warm_ranking=False))
    with TestClient(app):
        pass
    assert warmed == []
    monkeypatch.setattr(main, "get_settings", lambda: Settings(warm_ranking=True))
    with TestClient(app):
        assert ran.wait(5)
    assert warmed == [store]


def test_the_daily_warm_rewarms_just_after_each_utc_midnight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The as-of date is in the memo key, so each UTC day needs its own warm or
    its first visitor waits for the cold screen."""
    warms: list[object] = []
    sleeps: list[float] = []
    monkeypatch.setattr(putlab_routes, "warm_leaderboard", warms.append)
    clock = iter(
        [
            dt.datetime(2026, 10, 8, 15, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 10, 9, 0, 1, tzinfo=dt.UTC),
        ]
    )
    store = DeltaLakeStore(Path("unused"))
    putlab_routes.warm_leaderboard_daily(
        store, now=lambda: next(clock), sleep=sleeps.append, rounds=2
    )
    assert warms == [store, store]
    # 15:00 -> 00:01 next day, then 00:01 -> 00:01 the day after.
    assert sleeps == [9 * 3600 + 60, 24 * 3600]


def test_warm_leaderboard_screens_the_workspace_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        putlab_routes._LEADERBOARD_CACHE, "warm", lambda key, compute: seen.append(key)
    )
    putlab_routes.warm_leaderboard(DeltaLakeStore(Path("unused")))
    today = dt.datetime.now(dt.UTC).date().isoformat()
    # PUTLAB_DEFAULT_CONTROLS in the frontend, and the route's query defaults.
    assert seen == [(5.0, 4.0, 4.0, today)]


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
    # Only spy has data in the fixture, so one name is scored across seven screens.
    assert body["universe_size"] == 1
    assert len(body["entries"]) == 7
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


def test_cadence_prefers_a_live_snapshot_over_the_static_table(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    today = dt.datetime.now(dt.UTC).date()
    dates = [today + dt.timedelta(days=d) for d in range(0, 90, 30)]  # monthly gaps
    df = pd.DataFrame(
        {
            "symbol": pd.Series(["IWM"] * len(dates), dtype="object"),
            "expiration_date": pd.Series(pd.to_datetime(dates), dtype="datetime64[ns]"),
        }
    )
    store.write_bronze(options_expiry_dataset_id("iwm"), today, df)
    app.dependency_overrides[putlab_get_lake_store] = lambda: store
    try:
        resp = TestClient(app).get("/api/putlab/cadence", params={"asset": "iwm"})
    finally:
        app.dependency_overrides.pop(putlab_get_lake_store, None)

    assert resp.status_code == 200
    body = resp.json()
    # IWM is "weekly" in the static table; the live snapshot must override it.
    assert body["cadence"] == "monthly"
    assert body["label"] == "Monthlies (live)"


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


def test_accuracy_tells_the_frontend_what_priced_the_result(client: TestClient) -> None:
    """The provenance block on the surface is served from here.

    Asserted at the API boundary rather than only in the research layer
    because the frontend reads THIS shape: a key silently renamed or dropped
    would blank the block, and a blank provenance block reads as "priced from
    real quotes" -- the one conclusion it exists to prevent.
    """
    resp = client.get(
        "/api/putlab/accuracy",
        params={"asset": "spy", "moneyness_pct": 5, "tenor_weeks": 4, "years": 1},
    )

    assert resp.status_code == 200
    cov = resp.json()["quote_coverage"]
    # The roll engine is still Black-Scholes at a flat vol (docs/adr/0004), so
    # this must read "model" no matter how much vendor history the lake holds.
    assert cov["priced_from"] == "model"
    assert set(cov) == {
        "priced_from",
        "real_quotes_available",
        "window_months",
        "months_present",
        "months_missing",
        "complete",
        "panel_first_month",
        "panel_last_month",
        "note",
    }
    # Windowed, like every other block in the report.
    assert cov["months_present"] + cov["months_missing"] == cov["window_months"]
    assert cov["note"]


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


def test_sweep_tells_the_client_where_the_pricer_stops_being_trustworthy(
    client: TestClient,
) -> None:
    """The grid deliberately shows strikes deeper than the flat-vol model can
    price, so the client has to be able to mark them. It reads the threshold
    from the server rather than hard-coding a second copy of the number that
    would drift from `research/backtest/sweep.py` (docs/adr/0018)."""
    from tail_lab.research.backtest.sweep import MODEL_PRICED_MAX_MONEYNESS_PCT

    resp = client.get("/api/putlab/sweep", params={"asset": "spy", "notional": 1000, "years": 1})
    assert resp.status_code == 200
    body = resp.json()

    assert body["model_priced_max_moneyness_pct"] == MODEL_PRICED_MAX_MONEYNESS_PCT
    # ...and there is something for the client to mark.
    assert any(c["moneyness_pct"] > MODEL_PRICED_MAX_MONEYNESS_PCT for c in body["cells"])


def test_leaderboards_best_cell_carries_its_own_stats(client: TestClient) -> None:
    """Every ``best_*`` field describes the same run, so the recommendations
    view can print a whole strategy per row without mixing two of them."""
    resp = client.get(
        "/api/putlab/leaderboard", params={"moneyness_pct": 5, "tenor_weeks": 4, "years": 1}
    )
    assert resp.status_code == 200
    ranked = resp.json()["ranked"]
    assert ranked
    scored = [r for r in ranked if r["best_annualized"] is not None]
    assert scored, "no name scored a best cell"
    for row in scored:
        for field in (
            "best_moneyness_pct",
            "best_tenor_weeks",
            "best_roi_on_premium",
            "best_hit_rate",
            "best_n_cycles",
            "best_verdict",
        ):
            assert row[field] is not None, field
        # The headline never advertises a strike the model cannot price.
        assert row["best_moneyness_pct"] <= 10.0


def test_roll_schedule_is_placeable_order_intent(client: TestClient) -> None:
    """The one artefact that crosses adr/0007's wall. It has to be actionable by
    something that cannot see this codebase, and honest about what it is."""
    resp = client.get(
        "/api/putlab/roll-schedule",
        params={"moneyness_pct": 5, "tenor_weeks": 4, "years": 1, "notional": 1000, "top_k": 3},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["schedule_id"]) == 16
    assert body["premium_budget_per_leg"] == 1000
    assert 0 < len(body["legs"]) <= 3
    assert body["execution_notes"]
    assert "not advice" in body["basis"].lower()

    leg = body["legs"][0]
    assert leg["action"] == "BUY_PUT"
    assert leg["rank"] == 1
    # A strike a broker could snap to, derived from this leg's own moneyness.
    assert leg["target_strike"] == pytest.approx(leg["spot"] * (1 - leg["moneyness_pct"] / 100))
    assert leg["premium_budget"] == 1000
    # The model's own price travels with the order so the fill can be compared
    # against it -- that gap is the open question in adr/0018.
    assert leg["model_premium"] is not None and leg["model_premium"] > 0
    assert leg["model_contracts"] >= 1
    # Never a strike the pricer cannot price (adr/0018).
    assert leg["moneyness_pct"] <= 10.0


def test_marked_schedule_reports_no_quotes_when_no_chain_was_collected(
    client: TestClient,
) -> None:
    """The fixture lake carries OHLCV and VIX but no option_chain_snapshot, which
    is exactly the state this route must survive: it is the state the platform
    was in until 2026-08-26, and the state it returns to for any as-of before
    the first sweep. Every leg marks not_collected -- a gap in the data, never a
    silent zero and never a crash."""
    resp = client.get(
        "/api/putlab/roll-schedule/marked",
        params={"moneyness_pct": 5, "tenor_weeks": 4, "years": 1, "notional": 1000, "top_k": 3},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["quoted_legs"] == 0
    assert body["quote_session"] is None
    assert body["marks_notes"]
    for leg in body["legs"]:
        assert leg["quote_status"] == "not_collected"
        assert leg["listed_strike"] is None
        assert leg["market_contracts"] is None


def test_marking_preserves_the_screen_identity(client: TestClient) -> None:
    """An executor dedupes on schedule_id. Marking says what the market thinks
    of a recommendation; it must not look like a different recommendation."""
    params = {"moneyness_pct": 5, "tenor_weeks": 4, "years": 1, "notional": 1000, "top_k": 3}
    plain = client.get("/api/putlab/roll-schedule", params=params).json()
    marked = client.get("/api/putlab/roll-schedule/marked", params=params).json()

    assert marked["schedule_id"] == plain["schedule_id"]
    assert [leg["asset"] for leg in marked["legs"]] == [leg["asset"] for leg in plain["legs"]]
    # ...and the screen's own fields are carried through untouched.
    assert marked["legs"][0]["target_strike"] == plain["legs"][0]["target_strike"]
    assert marked["legs"][0]["model_premium"] == plain["legs"][0]["model_premium"]


def test_roll_schedule_is_stable_for_the_same_screen(client: TestClient) -> None:
    """An executor dedupes on schedule_id; re-fetching must not look like a new
    set of orders to place."""
    params = {"moneyness_pct": 5, "tenor_weeks": 4, "years": 1, "notional": 1000, "top_k": 3}
    first = client.get("/api/putlab/roll-schedule", params=params).json()
    second = client.get("/api/putlab/roll-schedule", params=params).json()
    assert first["schedule_id"] == second["schedule_id"]

    bigger = client.get("/api/putlab/roll-schedule", params={**params, "notional": 5000}).json()
    assert bigger["schedule_id"] != first["schedule_id"]


def _seed_chain(store: DeltaLakeStore, ingest_date: dt.date) -> None:
    from tail_lab.contracts.option_chain import DATASET
    from tail_lab.research.surface.paretan import ParetanTail

    tail = ParetanTail(alpha=3.0, karamata_l=0.05, basis="returns")
    rows = []
    for k in range(945, 600, -5):
        p = tail.put_price(strike=float(k), spot=1000.0)
        rows.append(
            {
                "underlying": "SPY",
                "quote_date": pd.Timestamp(ingest_date),
                "expiration": pd.Timestamp(ingest_date + dt.timedelta(days=30)),
                "strike": float(k),
                "bid": p * 0.98,
                "ask": p * 1.02,
                "volume": 1,
                "open_interest": 500,
                "spot": 1000.0,
                "iv": 0.25,
                "delta": None,
                "theo": None,
            }
        )
    store.write_bronze(DATASET, ingest_date, pd.DataFrame(rows))


def test_surface_serves_implied_side_with_refused_realised_and_provenance(
    client: TestClient,
) -> None:
    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    _seed_chain(store, today)
    resp = client.get(
        "/api/putlab/surface",
        params={"asset": "spy", "moneyness_pct": 7, "as_of": today.isoformat()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["chain_snapshot"] and body["ohlcv_snapshot"] and body["code_sha"]
    surface = body["surface"]
    assert "fixed moneyness" in surface["parameterisation"]
    assert surface["r"] == 0.04 and surface["q"] == 0.019
    assert surface["anchors"]["dispersion"] < 1e-6
    assert surface["anchors"]["readings"][0]["fit"]["alpha"] == pytest.approx(3.0, abs=1e-3)
    assert surface["realised"]["alpha"] is None and surface["realised"]["refusal"]
    assert surface["alpha_gap"] is None and surface["alpha_gap_reason"]
    assert 0 < len(surface["ladder"]) <= 8


def test_surface_404s_without_a_chain_or_a_listed_name(client: TestClient) -> None:
    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    params = {"asset": "spy", "as_of": today.isoformat()}
    assert client.get("/api/putlab/surface", params=params).status_code == 404
    _seed_chain(store, today)
    putlab_routes._SURFACE_CACHE.clear()
    assert client.get("/api/putlab/surface", params={**params, "asset": "qqq"}).status_code == 404


def test_surface_never_reads_the_optionsdx_panel(client: TestClient) -> None:
    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    _seed_chain(store, today)
    seen: list[str] = []
    real = store.read_bronze_as_of
    store.read_bronze_as_of = lambda d, a: (seen.append(d), real(d, a))[1]  # type: ignore[method-assign]
    resp = client.get("/api/putlab/surface", params={"asset": "spy", "as_of": today.isoformat()})
    assert resp.status_code == 200
    assert seen and not any(d.startswith("optionsdx") for d in seen)


# ---------------------------------------------------------------- Overlay


def _seed_cboe(store: DeltaLakeStore, ingest_date: dt.date) -> None:
    dates = pd.bdate_range(end=ingest_date, periods=400)
    rng = np.random.default_rng(5)
    level = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(dates)))
    frames = [
        pd.DataFrame({"index_symbol": s, "trade_date": dates, "close": level * k})
        for s, k in (("SPX", 1.0), ("PPUT", 0.5))
    ]
    store.write_bronze("cboe_strategy", ingest_date, pd.concat(frames, ignore_index=True))


def test_hedge_overlay_404s_without_a_cboe_snapshot(client: TestClient) -> None:
    resp = client.get("/api/putlab/hedge-overlay")
    assert resp.status_code == 404
    assert "no Cboe strategy indices" in resp.json()["detail"]


def test_hedge_overlay_carries_its_snapshot_and_logs_each_verdict(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    _seed_cboe(store, today)

    with caplog.at_level("INFO", logger="tail_lab"):
        resp = client.get("/api/putlab/hedge-overlay")

    assert resp.status_code == 200
    body = resp.json()
    # A result is never shown without the snapshot it was computed from.
    assert body["cboe_snapshot"]
    assert [p["index_symbol"] for p in body["overlay"]["programs"]] == ["PPUT"]
    line = next(r.getMessage() for r in caplog.records if "hedge_overlay" in r.getMessage())
    assert "cboe_snapshot=" in line
    assert "PPUT_full_outcome=" in line and "PPUT_full_margin=" in line
    assert "PPUT_full_clipped=false" in line
    assert f"code_sha={body['code_sha']}" in line
    for field in (
        "start",
        "end",
        "requested",
        "best_weight",
        "best_weight_risk_adjusted",
        "outcome_risk_adjusted",
        "sensitivity",
    ):
        assert f" PPUT_full_{field}=" in line, field
    # Each sensitivity entry carries yield, outcome and margin.
    sens = line.split(" PPUT_full_sensitivity=")[1].split()[0].split(",")
    assert [e.split(":")[0] for e in sens] == ["0.014", "0.019", "0.024"]
    assert all(e.split(":")[1] in {"holds", "inconclusive", "fails"} for e in sens)
    margins = [float(e.split(":")[2]) for e in sens]
    assert margins == pytest.approx(
        [r["margin"] for r in body["overlay"]["programs"][0]["windows"][-1]["sensitivity"]],
        abs=1e-6,
    )
    # 400 days of history cannot cover 2005-2016: the refusal is logged too.
    assert "PPUT_cole_unavailable=" in line
    assert "VXTH_missing=" in line
    assert "cole" in body["overlay"]["programs"][0]["unavailable"]
    assert putlab_routes._OVERLAY_CACHE


@pytest.mark.parametrize("error", [KeyError("store bug"), ValueError("corrupt parquet")])
def test_hedge_overlay_lets_a_store_bug_fail_loudly(error: Exception) -> None:
    """Only "the lake has no data" is a 404. A broken store is a 500, never
    relabelled as an empty lake (KeyError is a LookupError, hence the test)."""

    class BrokenStore:
        def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
            raise error

        def bronze_snapshot_id(self, dataset: str, as_of: dt.date) -> str:
            raise LookupError(dataset)

    putlab_routes._OVERLAY_CACHE.clear()
    app.dependency_overrides[putlab_get_lake_store] = lambda: BrokenStore()
    try:
        resp = TestClient(app, raise_server_exceptions=False).get("/api/putlab/hedge-overlay")
    finally:
        app.dependency_overrides.pop(putlab_get_lake_store, None)
    assert resp.status_code == 500


def test_hedge_overlay_logs_an_undefined_ratio_as_undefined_not_absent(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """log_event drops None, so a missing risk-adjusted verdict would vanish
    from the run record instead of reading as undefined (STANDARDS §f)."""
    store = app.dependency_overrides[putlab_get_lake_store]()
    _seed_cboe(store, dt.datetime.now(dt.UTC).date())
    real = putlab_routes.compute_hedge_overlay

    def undefined_ratio(*args: object, **kwargs: object) -> HedgeOverlayResult:
        result = real(*args, **kwargs)  # type: ignore[arg-type]
        for program in result.programs:
            for w in program.windows:
                w.best_weight_risk_adjusted = None
                w.outcome_risk_adjusted = None
        return result

    monkeypatch.setattr(putlab_routes, "compute_hedge_overlay", undefined_ratio)
    with caplog.at_level("INFO", logger="tail_lab"):
        assert client.get("/api/putlab/hedge-overlay").status_code == 200
    line = next(r.getMessage() for r in caplog.records if "hedge_overlay" in r.getMessage())
    assert "PPUT_full_outcome_risk_adjusted=undefined" in line
    assert "PPUT_full_best_weight_risk_adjusted=undefined" in line


# ---------------------------------------------------------------- Book plan


def test_book_plan_404s_without_a_cboe_snapshot(client: TestClient) -> None:
    resp = client.get("/api/putlab/book-plan")
    assert resp.status_code == 404
    assert "no Cboe strategy indices" in resp.json()["detail"]


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ({"program": "LTV"}, "program must be one of"),
        ({"comparator": "QQQ"}, "comparator must be one of"),
        ({"e0": 0, "monthly": 0}, "a plan needs"),
    ],
)
def test_book_plan_rejects_a_bad_request(
    client: TestClient, params: dict[str, object], detail: str
) -> None:
    resp = client.get("/api/putlab/book-plan", params=params)
    assert resp.status_code == 422
    assert detail in resp.json()["detail"]


def test_book_plan_carries_provenance_and_logs_the_verdict(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    store = app.dependency_overrides[putlab_get_lake_store]()
    _seed_cboe(store, dt.datetime.now(dt.UTC).date())
    with caplog.at_level("INFO", logger="tail_lab"):
        resp = client.get(
            "/api/putlab/book-plan", params={"horizon_years": 1, "monthly": 100, "e0": 1000}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cboe_snapshot"]
    assert body["rates_snapshot"] is None
    plan = body["plan"]
    assert plan["refusal"] is None and plan["rolling"]["n_starts"] > 0
    line = next(r.getMessage() for r in caplog.records if "book_plan" in r.getMessage())
    for field in (
        f"code_sha={body['code_sha']}",
        "rates_snapshot=none",
        "program=PPUT",
        "comparator=spx",
        "hedged_irr=",
        "share_ahead=",
        "by_yield=0.014:",
    ):
        assert field in line, field
    assert putlab_routes._PLAN_CACHE


def test_book_plan_refuses_t_bills_inside_a_200_until_rates_exist(client: TestClient) -> None:
    """A comparator the lake cannot offer is a value the page shows, not an
    error that blanks the whole tab."""
    store = app.dependency_overrides[putlab_get_lake_store]()
    _seed_cboe(store, dt.datetime.now(dt.UTC).date())
    resp = client.get("/api/putlab/book-plan", params={"comparator": "bills", "horizon_years": 1})
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    assert plan["window"] is None
    assert plan["refusal"] == "T-bills: no T-bill history in the lake yet (rates not ingested)"
    bills = next(o for o in plan["comparators"] if o["key"] == "bills")
    assert bills["available"] is False


# ---------------------------------------------------------------- Book model plan


def test_model_plan_offers_only_the_measured_depths(client: TestClient) -> None:
    resp = client.get("/api/putlab/book-plan/model", params={"moneyness_pct": 20})
    assert resp.status_code == 422
    assert "must be one of [5.0, 10.0]" in resp.json()["detail"]


def test_model_plan_404s_without_spx(client: TestClient) -> None:
    resp = client.get("/api/putlab/book-plan/model")
    assert resp.status_code == 404
    assert "no cboe_strategy known" in resp.json()["detail"]


def test_model_plan_carries_its_error_bar_and_logs_it(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    store = app.dependency_overrides[putlab_get_lake_store]()
    _seed_cboe(store, dt.datetime.now(dt.UTC).date())  # SPX; the fixture already has VIX
    with caplog.at_level("INFO", logger="tail_lab"):
        resp = client.get(
            "/api/putlab/book-plan/model",
            params={"moneyness_pct": 10, "horizon_years": 1, "monthly": 100, "put_share": 0.2},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["vix_snapshot"] and body["cboe_snapshot"]
    plan = body["plan"]
    assert plan["accuracy"]["vol_gap"] == 0.072
    assert plan["accounting"].startswith("contribution-funded")
    line = next(r.getMessage() for r in caplog.records if "book_plan_model" in r.getMessage())
    for field in (
        "vol_gap=0.072",
        "put_share=0.2",
        f"code_sha={body['code_sha']}",
        "vix_snapshot=",
    ):
        assert field in line, field


@pytest.mark.parametrize("params", [{"e0": "inf"}, {"monthly": "inf"}, {"e0": 1e12}])
def test_book_plan_refuses_absurd_amounts_before_the_solver(
    client: TestClient, params: dict[str, object]
) -> None:
    assert client.get("/api/putlab/book-plan", params=params).status_code == 422
    assert client.get("/api/putlab/book-plan/model", params=params).status_code == 422


def test_a_window_of_days_is_a_refusal_inside_a_200_not_a_500(client: TestClient) -> None:
    """A start one day before as_of used to meet the IRR bracket and crash."""
    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    _seed_cboe(store, today)
    first_of_month = today.replace(day=1)
    resp = client.get(
        "/api/putlab/book-plan",
        params={"start": first_of_month.isoformat(), "horizon_years": 1, "comparator": "cash"},
    )
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    assert plan["window"] is None
    assert plan["refusal"] in {
        "the window holds less than one full month",
        f"no month start on or after {first_of_month}",
    }


def test_book_plan_logs_its_start_and_its_404s(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="tail_lab"):
        assert client.get("/api/putlab/book-plan").status_code == 404
    refused = next(r.getMessage() for r in caplog.records if "refused_404=" in r.getMessage())
    assert "program=PPUT" in refused and "start=default" in refused
    store = app.dependency_overrides[putlab_get_lake_store]()
    _seed_cboe(store, dt.datetime.now(dt.UTC).date())
    caplog.clear()
    with caplog.at_level("INFO", logger="tail_lab"):
        client.get("/api/putlab/book-plan", params={"horizon_years": 1, "comparator": "cash"})
    line = next(r.getMessage() for r in caplog.records if "book_plan" in r.getMessage())
    assert "start=default" in line


def test_the_plan_cache_starts_over_instead_of_growing_per_keystroke(client: TestClient) -> None:
    putlab_routes._PLAN_CACHE.clear()
    for i in range(putlab_routes._PLAN_CACHE_MAX + 5):
        putlab_routes._PLAN_CACHE[(i,)] = (0.0, None)
    store = app.dependency_overrides[putlab_get_lake_store]()
    _seed_cboe(store, dt.datetime.now(dt.UTC).date())
    client.get("/api/putlab/book-plan", params={"horizon_years": 1, "comparator": "cash"})
    assert len(putlab_routes._PLAN_CACHE) == 1


def test_model_plan_refuses_a_plan_with_no_starting_book(client: TestClient) -> None:
    """With no book the puts arm is a standalone put (the Workspace's job),
    and its time-weighted drawdown would read -100% for a sleeve that wins."""
    assert client.get("/api/putlab/book-plan/model", params={"e0": 0}).status_code == 422


def test_model_plan_cache_keys_on_every_input(client: TestClient) -> None:
    store = app.dependency_overrides[putlab_get_lake_store]()
    _seed_cboe(store, dt.datetime.now(dt.UTC).date())
    base = {
        "horizon_years": 1,
        "put_share": 1.0,
        "e0": 1000.0,
        "monthly": 100.0,
        "moneyness_pct": 5,
    }
    for field, other in (
        ("put_share", 0.2),
        ("horizon_years", 2),
        ("e0", 2000.0),
        ("monthly", 200.0),
        ("moneyness_pct", 10),
    ):
        first = client.get("/api/putlab/book-plan/model", params=base).json()["plan"]
        varied = client.get("/api/putlab/book-plan/model", params=base | {field: other}).json()[
            "plan"
        ]
        assert varied[field] == other and first[field] == base[field], field


def test_model_plan_logs_its_404s_with_inputs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="tail_lab"):
        assert (
            client.get("/api/putlab/book-plan/model", params={"put_share": 0.3}).status_code == 404
        )
    line = next(r.getMessage() for r in caplog.records if "refused_404=" in r.getMessage())
    assert "book_plan_model" in line and "put_share=0.3" in line


# --- Dividend yields reach every pricing path (docs/DATA_CONTRACTS.md #14) ---
#
# Each test runs a route on the seeded lake with no Tiingo snapshot (every roll
# priced at an unknown q = 0), then seeds a 6% payer and runs it again. A path
# that forgot to pass the lookup would return the identical body both times --
# exactly the headline-measured / sibling-at-zero split this guards against.


def _clear_putlab_caches() -> None:
    from tail_lab.research import dividends

    for cache in (
        putlab_routes._LEADERBOARD_CACHE,
        putlab_routes._METRIC_SCREEN_CACHE,
        putlab_routes._BACKTEST_CACHE,
        putlab_routes._SWEEP_CACHE,
        putlab_routes._REGIME_VERDICT_CACHE,
        putlab_routes._SURFACE_CACHE,
    ):
        cache.clear()
    dividends._memo.clear()


def _before_and_after_dividends(
    call: Callable[[], dict[str, Any]], day: dt.date | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    from tests.test_research_dividends import seed_tiingo_eod

    _clear_putlab_caches()
    before = call()
    store = app.dependency_overrides[putlab_get_lake_store]()
    seed_tiingo_eod(store, ["spy"], day or dt.datetime.now(dt.UTC).date(), annual_yield=0.06)
    _clear_putlab_caches()
    return before, call()


def test_backtest_prices_every_roll_with_the_measured_yield(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    params = {"asset": "spy", "notional": 1000, "moneyness_pct": 5, "tenor_weeks": 4, "years": 1}
    before, after = _before_and_after_dividends(
        lambda: client.get("/api/putlab/backtest", params=params).json()
    )
    assert before["q_source"] == "none" and before["dividend_snapshot"] is None
    assert after["q_source"] == "measured"
    assert after["dividend_snapshot"].startswith("tiingo_eod@")
    assert all(c["q_source"] == "measured" and c["q"] > 0.05 for c in after["cycles"])
    # Same spot, strike and vol: a dividend lowers the forward, so the put is dearer.
    assert after["cycles"][0]["premium"] > before["cycles"][0]["premium"]
    # The Workspace lays the latest roll's yield out to be redone by hand:
    # q = -ln(1 - sum(adjusted) / close), from the four quarterly payments.
    basis = after["dividend_basis"]
    assert basis["source"] == "measured" and len(basis["payments"]) == 4
    total = sum(p["adjusted"] for p in basis["payments"])
    assert basis["q"] == pytest.approx(-np.log1p(-total / basis["close"]))
    assert before["dividend_basis"]["source"] == "unknown"
    # The structured run line names the dividend snapshot it priced with (§f).
    lines = [r.getMessage() for r in caplog.records if "event=putlab.backtest " in r.getMessage()]
    assert any("dividend_snapshot=tiingo_eod@" in m and "q_source=measured" in m for m in lines)


def test_sweep_cells_price_on_the_same_dividend_basis(client: TestClient) -> None:
    params = {"asset": "spy", "notional": 1000, "years": 1}
    before, after = _before_and_after_dividends(
        lambda: client.get("/api/putlab/sweep", params=params).json()
    )
    assert before["cells"] != after["cells"]


def test_portfolio_prices_with_dividends_and_cites_the_snapshot(client: TestClient) -> None:
    body = {
        "legs": [{"asset": "spy", "moneyness_pct": 5, "tenor_weeks": 4, "weight": 1}],
        "notional": 10000,
        "years": 1,
    }
    before, after = _before_and_after_dividends(
        lambda: client.post("/api/putlab/portfolio", json=body).json()
    )
    assert before["legs"] != after["legs"]
    assert any(s.startswith("tiingo_eod@") for s in after["snapshot_ids"])


def test_leaderboard_rows_price_with_dividends_and_say_so(client: TestClient) -> None:
    params = {"moneyness_pct": 5, "tenor_weeks": 4, "years": 1}
    before, after = _before_and_after_dividends(
        lambda: client.get("/api/putlab/leaderboard", params=params).json()
    )
    (row_before,) = [r for r in before["ranked"] if r["asset"] == "spy"]
    (row_after,) = [r for r in after["ranked"] if r["asset"] == "spy"]
    assert (row_before["q_source"], row_after["q_source"]) == ("none", "measured")
    assert row_before["roi_on_premium"] != row_after["roi_on_premium"]
    # The best cell is picked by the ranking's own sweep: it must be on the same basis.
    assert row_before["best_annualized"] != row_after["best_annualized"]
    # ...and every best_* figure comes from a re-roll at that cell: same basis too.
    assert row_before["best_roi_on_premium"] != row_after["best_roi_on_premium"]
    assert any(s.startswith("tiingo_eod@") for s in after["snapshot_ids"])


def test_roll_schedule_quotes_the_premium_with_dividends(client: TestClient) -> None:
    params = {"moneyness_pct": 5, "tenor_weeks": 4, "years": 1, "top_k": 1}
    before, after = _before_and_after_dividends(
        lambda: client.get("/api/putlab/roll-schedule", params=params).json()
    )
    leg_before, leg_after = before["legs"][0], after["legs"][0]
    assert leg_after["model_premium"] > leg_before["model_premium"]


def test_surface_uses_the_names_own_yield_and_says_where_it_came_from(
    client: TestClient,
) -> None:
    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    _seed_chain(store, today)
    params = {"asset": "spy", "moneyness_pct": 7, "as_of": today.isoformat()}
    # Seed on the same frozen day the request reads, so a run straddling UTC
    # midnight cannot date the snapshot after as_of.
    before, after = _before_and_after_dividends(
        lambda: client.get("/api/putlab/surface", params=params).json()["surface"], day=today
    )
    # Without a measured yield: the old flat fallback, labelled as assumed.
    assert (before["q"], before["q_source"]) == (0.019, "assumed")
    assert "assumed" in before["rate_note"]
    # "carried" on a weekend or holiday: the seeded history ends on the last
    # business day and the surface reads today.
    assert after["q_source"] in {"measured", "carried"}
    assert after["q"] == pytest.approx(-np.log1p(-0.06))
    assert "own dividend yield" in after["rate_note"]


def test_surface_says_a_stale_yield_is_stale_and_asks_for_a_refresh(
    client: TestClient,
) -> None:
    # The Surface passes the dividend source straight to the reader: a yield
    # carried more than CARRY_STALE_DAYS past Tiingo's last row must say so on
    # the page, not pass for a fresh measurement.
    from tests.test_research_dividends import seed_tiingo_eod

    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    _seed_chain(store, today)
    _clear_putlab_caches()
    seed_tiingo_eod(store, ["spy"], today - dt.timedelta(days=40), annual_yield=0.06)
    params = {"asset": "spy", "moneyness_pct": 7, "as_of": today.isoformat()}
    surface = client.get("/api/putlab/surface", params=params).json()["surface"]
    assert surface["q_source"] == "stale"
    # Still the name's own yield -- never dropped to zero for being old.
    assert surface["q"] == pytest.approx(-np.log1p(-0.06))
    assert "carried over three weeks" in surface["rate_note"]
    assert "Tiingo needs a refresh" in surface["rate_note"]


def test_metric_screen_baskets_price_with_dividends(client: TestClient) -> None:
    params = {"moneyness_pct": 10, "tenor_weeks": 4, "years": 1, "top_k": 2}
    before, after = _before_and_after_dividends(
        lambda: client.get("/api/putlab/metric-screen", params=params).json()
    )
    assert before["baseline_roi"] != after["baseline_roi"]
    assert before["entries"] != after["entries"]


def test_every_pricing_route_logs_the_dividend_snapshot_it_priced_with(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    # docs/STANDARDS.md §f: the structured run line, not just the response,
    # must name the data version -- including the dividend basis.
    from tests.test_research_dividends import seed_tiingo_eod

    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    _seed_chain(store, today)
    seed_tiingo_eod(store, ["spy"], today)
    _clear_putlab_caches()
    grid = {"moneyness_pct": 5, "tenor_weeks": 4, "years": 1}
    client.get("/api/putlab/leaderboard", params=grid)
    client.get("/api/putlab/roll-schedule", params={**grid, "top_k": 1})
    client.get("/api/putlab/metric-screen", params={**grid, "top_k": 2})
    client.post(
        "/api/putlab/portfolio",
        json={"legs": [{"asset": "spy", **grid, "weight": 1}], "notional": 1000, "years": 1},
    )
    client.get(
        "/api/putlab/surface",
        params={"asset": "spy", "moneyness_pct": 7, "as_of": today.isoformat()},
    )
    lines = [r.getMessage() for r in caplog.records]
    for event, field in [
        ("putlab.leaderboard", "dividend_snapshot"),
        ("putlab.roll_schedule", "ranking_dividend_snapshot"),
        ("putlab.roll_schedule", "premium_dividend_snapshot"),
        ("putlab.metric_screen", "dividend_snapshot"),
        ("putlab.portfolio", "dividend_snapshot"),
        ("api.putlab.surface", "dividend_snapshot"),
    ]:
        assert any(f"event={event} " in m and f"{field}=tiingo_eod@" in m for m in lines), (
            event,
            field,
        )


def test_a_memoised_ranking_never_logs_a_snapshot_it_did_not_price_with(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    # The leaderboard can serve the memo's copy priced before a Tiingo ingest:
    # its log must cite what that ranking used (none), not a fresh read.
    from tests.test_research_dividends import seed_tiingo_eod

    _clear_putlab_caches()
    grid = {"moneyness_pct": 5, "tenor_weeks": 4, "years": 1}
    client.get("/api/putlab/leaderboard", params=grid)
    seed_tiingo_eod(
        app.dependency_overrides[putlab_get_lake_store](), ["spy"], dt.datetime.now(dt.UTC).date()
    )
    caplog.clear()
    served = client.get("/api/putlab/leaderboard", params=grid).json()
    assert not any(s.startswith("tiingo_eod@") for s in served["snapshot_ids"])
    lines = [
        r.getMessage() for r in caplog.records if "event=putlab.leaderboard " in r.getMessage()
    ]
    assert lines and not any("dividend_snapshot=tiingo_eod@" in m for m in lines)


# --- strike rule: by delta (docs/adr/0029) ---


def test_backtest_by_delta_serves_the_rule_and_lands_on_the_target(client: TestClient) -> None:
    body = client.get(
        "/api/putlab/backtest",
        params={"asset": "spy", "strike_rule": "delta", "target_delta": 0.15, "years": 1},
    ).json()
    assert (body["strike_rule"], body["target_delta"], body["moneyness_pct"]) == (
        "delta",
        0.15,
        None,
    )
    assert all(c["entry_delta"] == pytest.approx(-0.15, abs=1e-9) for c in body["cycles"])
    assert body["beyond_model_depth_share"] is not None


def test_a_leftover_moneyness_never_splits_or_changes_a_delta_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The UI keeps both values, so one always arrives: in delta mode it is
    # ignored -- one compute, one cache entry -- and even an out-of-range
    # leftover never refuses the request.
    calls = {"n": 0}
    real = putlab_routes.compute_put_backtest

    def counted(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(putlab_routes, "compute_put_backtest", counted)
    a = {
        "asset": "spy",
        "strike_rule": "delta",
        "target_delta": 0.10,
        "years": 1,
        "moneyness_pct": 5,
    }
    first = client.get("/api/putlab/backtest", params=a).json()
    second = client.get("/api/putlab/backtest", params={**a, "moneyness_pct": 20}).json()
    third = client.get("/api/putlab/backtest", params={**a, "moneyness_pct": 400})
    assert third.status_code == 200
    assert first["cycles"] == second["cycles"] == third.json()["cycles"]
    assert calls["n"] == 1


def test_a_moneyness_and_a_delta_request_never_share_a_cache_entry(client: TestClient) -> None:
    # 10% below spot and 0.10 delta are different rules with numerically
    # similar parameters: the cache must keep them apart.
    m = client.get(
        "/api/putlab/backtest", params={"asset": "spy", "moneyness_pct": 10, "years": 1}
    ).json()
    d = client.get(
        "/api/putlab/backtest",
        params={"asset": "spy", "strike_rule": "delta", "target_delta": 0.10, "years": 1},
    ).json()
    assert (m["strike_rule"], d["strike_rule"]) == ("moneyness", "delta")
    assert [c["strike"] for c in m["cycles"]] != [c["strike"] for c in d["cycles"]]


def test_metric_screen_cache_keys_on_the_rule(client: TestClient) -> None:
    base = {"tenor_weeks": 4, "years": 1, "top_k": 2, "moneyness_pct": 10}
    m = client.get("/api/putlab/metric-screen", params=base).json()
    d = client.get(
        "/api/putlab/metric-screen", params={**base, "strike_rule": "delta", "target_delta": 0.1}
    ).json()
    assert (m["strike_rule"], d["strike_rule"]) == ("moneyness", "delta")


def test_strike_preview_cache_keys_on_the_target(client: TestClient) -> None:
    ten = client.get(
        "/api/putlab/strike-preview", params={"asset": "spy", "target_delta": 0.10}
    ).json()
    twenty = client.get(
        "/api/putlab/strike-preview", params={"asset": "spy", "target_delta": 0.20}
    ).json()
    assert ten["model"]["strike"] < twenty["model"]["strike"]


def test_only_the_active_rules_parameter_is_validated(client: TestClient) -> None:
    bad_moneyness = client.get(
        "/api/putlab/backtest", params={"asset": "spy", "moneyness_pct": 400}
    )
    assert bad_moneyness.status_code == 422
    leftover_delta = client.get(
        "/api/putlab/backtest",
        params={"asset": "spy", "moneyness_pct": 5, "target_delta": 9, "years": 1},
    )
    assert leftover_delta.status_code == 200


@pytest.mark.parametrize("bad", [0.0, 0.005, 0.6])
def test_a_target_delta_outside_the_hedge_range_is_rejected(client: TestClient, bad: float) -> None:
    resp = client.get(
        "/api/putlab/backtest", params={"asset": "spy", "strike_rule": "delta", "target_delta": bad}
    )
    assert resp.status_code == 422


def test_a_delta_regime_verdict_has_no_memory_identity(client: TestClient) -> None:
    body = client.get(
        "/api/putlab/regime-verdict",
        params={"asset": "spy", "strike_rule": "delta", "target_delta": 0.10, "years": 1},
    ).json()
    # Memory records moneyness rules only: a delta verdict carries no hash.
    assert body["rule_hash"] is None and body["rule_spec"] is None
    moneyness = client.get("/api/putlab/regime-verdict", params={"asset": "spy", "years": 1}).json()
    assert moneyness["rule_hash"].startswith("h-")


def test_metric_screen_by_delta_states_its_rule(client: TestClient) -> None:
    body = client.get(
        "/api/putlab/metric-screen",
        params={
            "strike_rule": "delta",
            "target_delta": 0.10,
            "tenor_weeks": 4,
            "years": 1,
            "top_k": 2,
        },
    ).json()
    assert (body["strike_rule"], body["target_delta"], body["moneyness_pct"]) == (
        "delta",
        0.10,
        None,
    )


def test_strike_preview_shows_the_model_and_the_market_strike(client: TestClient) -> None:
    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    _seed_chain(store, today)
    body = client.get(
        "/api/putlab/strike-preview",
        params={"asset": "spy", "target_delta": 0.10, "tenor_weeks": 4, "as_of": today.isoformat()},
    ).json()
    assert body["market_status"] == "quoted"
    model, market = body["model"], body["market"]
    # Each side lands on its target under the one convention, on its own vol.
    from tail_lab.research.backtest.strike_rule import put_delta

    assert put_delta(
        spot=model["spot"],
        strike=model["strike"],
        sigma=model["sigma"],
        t_years=model["t_years"],
        r=body["r"],
        q=body["q"],
    ) == pytest.approx(-0.10, abs=1e-9)
    assert market["iv"] == 0.25 and market["delta"] == pytest.approx(-0.10, abs=0.03)
    assert market["expiration"] == (today + dt.timedelta(days=30)).isoformat()


def test_strike_preview_without_a_chain_says_not_collected(client: TestClient) -> None:
    body = client.get("/api/putlab/strike-preview", params={"asset": "spy"}).json()
    assert body["model"] is not None
    assert body["market"] is None and body["market_status"] == "not_collected"


def test_strike_preview_answers_the_same_whatever_the_case_of_the_name(client: TestClient) -> None:
    lower = client.get("/api/putlab/strike-preview", params={"asset": "spy"}).json()
    upper = client.get("/api/putlab/strike-preview", params={"asset": "SPY"}).json()
    assert lower == upper and upper["asset"] == "spy"


@pytest.mark.parametrize("route", ["/api/putlab/backtest", "/api/putlab/regime-verdict"])
def test_two_delta_targets_never_share_a_cache_entry(client: TestClient, route: str) -> None:
    base = {"asset": "spy", "strike_rule": "delta", "years": 1}
    ten = client.get(route, params={**base, "target_delta": 0.10}).json()
    thirty = client.get(route, params={**base, "target_delta": 0.30}).json()
    if route.endswith("backtest"):
        assert ten["target_delta"] == 0.10 and thirty["target_delta"] == 0.30
        assert [c["strike"] for c in ten["cycles"]] != [c["strike"] for c in thirty["cycles"]]
    else:
        assert ten["slices"] != thirty["slices"]


def test_metric_screen_never_shares_an_entry_between_two_targets_of_one_rule(
    client: TestClient,
) -> None:
    base = {"tenor_weeks": 4, "years": 1, "top_k": 2}
    d10 = client.get(
        "/api/putlab/metric-screen", params={**base, "strike_rule": "delta", "target_delta": 0.1}
    ).json()
    d30 = client.get(
        "/api/putlab/metric-screen", params={**base, "strike_rule": "delta", "target_delta": 0.3}
    ).json()
    assert (d10["target_delta"], d30["target_delta"]) == (0.1, 0.3)
    m5 = client.get("/api/putlab/metric-screen", params={**base, "moneyness_pct": 5}).json()
    m10 = client.get("/api/putlab/metric-screen", params={**base, "moneyness_pct": 10}).json()
    assert (m5["moneyness_pct"], m10["moneyness_pct"]) == (5, 10)


def test_strike_preview_cache_keys_on_the_tenor(client: TestClient) -> None:
    four = client.get(
        "/api/putlab/strike-preview", params={"asset": "spy", "tenor_weeks": 4}
    ).json()
    twelve = client.get(
        "/api/putlab/strike-preview", params={"asset": "spy", "tenor_weeks": 12}
    ).json()
    assert (four["tenor_weeks"], twelve["tenor_weeks"]) == (4, 12)
    assert four["model"]["t_years"] != twelve["model"]["t_years"]


def test_strike_preview_strikes_on_the_measured_yield_and_the_backtests_rate(
    client: TestClient,
) -> None:
    # The page says the preview uses "the same q and r" as the backtest: a
    # preview struck at q = 0 or r = 0 would put a different strike beside the
    # backtest's and still look plausible, so pin both against the lookups.
    from tail_lab.research import dividends
    from tail_lab.research.backtest.put_roll import DEFAULT_RATE
    from tail_lab.research.backtest.strike_rule import ByDelta
    from tests.test_research_dividends import seed_tiingo_eod

    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    seed_tiingo_eod(store, ["spy"], today, annual_yield=0.06)
    dividends._memo.clear()
    expected = dividends.dividend_lookup(store, "spy", today).lookup(today)
    # "carried" on a weekend (the seed ends on the last business day).
    assert expected.source in ("measured", "carried") and expected.q > 0.05
    body = client.get(
        "/api/putlab/strike-preview",
        params={"asset": "spy", "target_delta": 0.10, "as_of": today.isoformat()},
    ).json()
    assert (body["q"], body["q_source"], body["r"]) == (
        expected.q,
        expected.source,
        DEFAULT_RATE,
    )
    assert body["as_of"] == today.isoformat()
    model = body["model"]
    assert model["strike"] == pytest.approx(
        ByDelta(0.10).strike(
            spot=model["spot"],
            sigma=model["sigma"],
            t_years=model["t_years"],
            r=DEFAULT_RATE,
            q=expected.q,
        ),
        rel=1e-12,
    )


@pytest.mark.parametrize(
    ("route", "extra"),
    [
        ("/api/putlab/strike-preview", {}),
        ("/api/putlab/backtest", {"strike_rule": "delta", "years": 1}),
        ("/api/putlab/regime-verdict", {"strike_rule": "delta", "years": 1}),
        ("/api/putlab/metric-screen", {"strike_rule": "delta", "years": 1, "top_k": 2}),
    ],
)
def test_a_delta_no_strike_can_reach_is_refused_not_a_server_error(
    client: TestClient, route: str, extra: dict[str, Any]
) -> None:
    # A yield of ~161% (D = 80% of the close) over a 26-week roll caps a put's
    # |delta| at e^(-qT) ~ 0.44: no strike has delta -0.50, and the rule says so. That is the request's inputs, not a
    # fault -- a 422 naming the cause, never a 500.
    from tail_lab.research import dividends
    from tests.test_research_dividends import seed_tiingo_eod

    store = app.dependency_overrides[putlab_get_lake_store]()
    today = dt.datetime.now(dt.UTC).date()
    seed_tiingo_eod(store, ["spy"], today, annual_yield=0.8)
    dividends._memo.clear()
    resp = TestClient(app, raise_server_exceptions=False).get(
        route,
        params={"asset": "spy", "target_delta": 0.5, "tenor_weeks": 26, **extra},
    )
    assert resp.status_code == 422, resp.text
    assert "no strike has put delta" in resp.json()["detail"]
