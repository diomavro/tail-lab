"""Tests for the sensitivity leaderboard orchestrator
(``research/leaderboard.py``).

The pinned case constructs a benchmark return series and an asset whose
down-day returns are an exact multiple of the benchmark's, so the expected
downside beta is derivable by construction and cross-checked against the
independently-tested ``research/metrics/downside_beta.py`` (treated as a
tested black box, mirroring the ``BlackScholesPricer`` convention in
``tests/test_research_backtest_put_roll.py``). The point-in-time test is the
adversarial no-look-ahead guard required for anything a backtest/mart reads
(``docs/adr/0009``).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.leaderboard import (
    MIN_DOWNSIDE_OBSERVATIONS,
    compute_sensitivity_leaderboard,
)
from tail_lab.research.metrics.downside_beta import downside_beta

#: 24 daily returns with 8 negative ("down") days -- comfortably above
#: MIN_DOWNSIDE_OBSERVATIONS. returns[0] is lost to pct_change().dropna(),
#: so the effective pattern readers see is returns[1:].
BENCH_RETURNS = [
    0.0,
    0.01,
    -0.02,
    0.015,
    -0.01,
    0.02,
    -0.03,
    -0.005,
    0.01,
    -0.015,
    0.005,
    0.02,
    -0.02,
    0.01,
    -0.01,
    0.03,
    -0.025,
    0.015,
    -0.005,
    0.02,
    -0.01,
    0.008,
    -0.012,
    0.006,
]


def _asset_returns(multiplier_down: float, multiplier_up: float) -> list[float]:
    return [r * multiplier_down if r < 0 else r * multiplier_up for r in BENCH_RETURNS]


def _write_ohlcv(
    store: DeltaLakeStore,
    symbol: str,
    ingest_date: dt.date,
    returns: list[float],
    start_price: float = 100.0,
) -> None:
    n = len(returns)
    prices = start_price * np.cumprod(1.0 + np.array(returns, dtype=float))
    dates = pd.bdate_range(end=ingest_date, periods=n)
    df = pd.DataFrame(
        {
            "symbol": symbol.upper(),
            "trade_date": dates,
            "open": prices,
            "high": prices * 1.001,
            "low": prices * 0.999,
            "close": prices,
            "volume": np.full(n, 1_000_000, dtype=int),
            "adj_close": prices,
        }
    )
    store.write_bronze(dataset_id(symbol), ingest_date, df)


def test_leaderboard_ranks_by_downside_beta_against_independent_computation(
    tmp_path: Path,
) -> None:
    store = DeltaLakeStore(tmp_path)
    as_of = dt.date(2026, 6, 1)
    _write_ohlcv(store, "spy", as_of, BENCH_RETURNS)
    # BBB: 2x the benchmark on down days -> downside beta == 2.0 by construction.
    _write_ohlcv(store, "bbb", as_of, _asset_returns(2.0, 0.3))
    # CCC: 0.5x the benchmark on down days -> downside beta == 0.5 by construction.
    _write_ohlcv(store, "ccc", as_of, _asset_returns(0.5, 1.5))

    result = compute_sensitivity_leaderboard(
        store, as_of=as_of, universe=("spy", "bbb", "ccc"), benchmark="spy"
    )

    assert result.metric == "downside_beta"
    assert result.benchmark == "SPY"
    assert [row.symbol for row in result.rows] == ["BBB", "SPY", "CCC"]
    assert [row.rank for row in result.rows] == [1, 2, 3]

    scores = {row.symbol: row.score for row in result.rows}
    # SPY vs itself is exact self-beta = 1.0.
    assert scores["SPY"] == pytest.approx(1.0)
    assert scores["BBB"] == pytest.approx(2.0, rel=1e-6)
    assert scores["CCC"] == pytest.approx(0.5, rel=1e-6)

    # Cross-check against the independently-tested metric function directly.
    bench_prices = 100.0 * np.cumprod(1.0 + np.array(BENCH_RETURNS))
    bbb_prices = 100.0 * np.cumprod(1.0 + np.array(_asset_returns(2.0, 0.3)))
    idx = pd.bdate_range(end=as_of, periods=len(BENCH_RETURNS))
    bench_ret = pd.Series(bench_prices, index=idx).pct_change().dropna()
    bbb_ret = pd.Series(bbb_prices, index=idx).pct_change().dropna()
    expected_bbb_beta = downside_beta(
        bbb_ret, bench_ret, min_observations=MIN_DOWNSIDE_OBSERVATIONS
    )
    assert scores["BBB"] == pytest.approx(expected_bbb_beta)


def test_leaderboard_skips_unscorable_symbols(tmp_path: Path) -> None:
    """A universe symbol with no bronze snapshot yet is skipped, not fatal."""
    store = DeltaLakeStore(tmp_path)
    as_of = dt.date(2026, 6, 1)
    _write_ohlcv(store, "spy", as_of, BENCH_RETURNS)
    _write_ohlcv(store, "bbb", as_of, _asset_returns(2.0, 0.3))

    result = compute_sensitivity_leaderboard(
        store, as_of=as_of, universe=("spy", "bbb", "not_ingested"), benchmark="spy"
    )
    assert {row.symbol for row in result.rows} == {"SPY", "BBB"}


def test_leaderboard_skips_symbol_with_too_few_downside_observations(tmp_path: Path) -> None:
    """A symbol whose bronze history barely overlaps the benchmark's calendar
    (e.g. a recent listing) aligns to too few observations to support a
    downside-beta estimate; it's skipped, not fatal."""
    store = DeltaLakeStore(tmp_path)
    as_of = dt.date(2026, 6, 1)
    _write_ohlcv(store, "spy", as_of, BENCH_RETURNS)
    # Only the last 4 trading days -- 3 aligned returns, under MIN_DOWNSIDE_OBSERVATIONS.
    _write_ohlcv(store, "ddd", as_of, [0.0, 0.01, -0.01, 0.01])

    result = compute_sensitivity_leaderboard(
        store, as_of=as_of, universe=("spy", "ddd"), benchmark="spy"
    )
    assert {row.symbol for row in result.rows} == {"SPY"}


def test_leaderboard_raises_when_benchmark_missing(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(LookupError, match="no OHLCV for benchmark"):
        compute_sensitivity_leaderboard(store, as_of=dt.date(2026, 6, 1))


def test_leaderboard_raises_when_nothing_scorable(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    as_of = dt.date(2026, 6, 1)
    _write_ohlcv(store, "spy", as_of, BENCH_RETURNS)
    with pytest.raises(LookupError, match="no scorable symbols"):
        compute_sensitivity_leaderboard(store, as_of=as_of, universe=("nope",), benchmark="spy")


def test_leaderboard_respects_no_look_ahead(tmp_path: Path) -> None:
    """A leaderboard as-of an earlier ingest date must not reflect a later
    (restated) snapshot's prices."""
    store = DeltaLakeStore(tmp_path)
    day1, day2 = dt.date(2026, 3, 1), dt.date(2026, 3, 2)
    _write_ohlcv(store, "spy", day1, BENCH_RETURNS)
    _write_ohlcv(store, "bbb", day1, _asset_returns(2.0, 0.3))

    res1 = compute_sensitivity_leaderboard(
        store, as_of=day1, universe=("spy", "bbb"), benchmark="spy"
    )

    # A later, entirely different snapshot must not change the day1 read.
    rng = np.random.default_rng(7)
    restated = list(rng.normal(0, 0.05, size=len(BENCH_RETURNS)))
    _write_ohlcv(store, "spy", day2, restated)
    _write_ohlcv(store, "bbb", day2, restated)
    res1_again = compute_sensitivity_leaderboard(
        store, as_of=day1, universe=("spy", "bbb"), benchmark="spy"
    )
    assert res1_again.model_dump() == res1.model_dump()
