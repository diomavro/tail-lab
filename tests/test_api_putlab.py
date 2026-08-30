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
from tail_lab.contracts.options_expiry import dataset_id as options_expiry_dataset_id
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
