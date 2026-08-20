"""Tests for the portfolio-of-puts backtest (``research/backtest/portfolio.py``)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.portfolio import PortfolioLeg, run_portfolio


def _seed_ohlcv(
    store: DeltaLakeStore, symbol: str, ingest: dt.date, seed: int, vol: float, n: int = 320
) -> None:
    rng = np.random.default_rng(seed)
    closes = np.clip(100 * np.exp(np.cumsum(rng.normal(0, vol / 100, size=n))), 5, None)
    dates = pd.date_range(end=ingest, periods=n, freq="B")
    store.write_bronze(
        dataset_id(symbol),
        ingest,
        pd.DataFrame(
            {
                "symbol": symbol.upper(),
                "trade_date": dates,
                "open": closes,
                "high": closes * 1.003,
                "low": closes * 0.997,
                "close": closes,
                "volume": np.full(n, 1_000_000, dtype=int),
                "adj_close": closes,
            }
        ),
    )


def _seed_vix(store: DeltaLakeStore, ingest: dt.date, n: int = 320) -> None:
    vix = 14 + 12 * (np.sin(np.linspace(0, 12, n)) + 1)
    dates = pd.date_range(end=ingest, periods=n, freq="B")
    store.write_bronze("vix", ingest, pd.DataFrame({"date": dates, "close": vix}))


def _seed_two(store: DeltaLakeStore, ingest: dt.date) -> None:
    _seed_vix(store, ingest)
    _seed_ohlcv(store, "spy", ingest, seed=1, vol=1.0)
    _seed_ohlcv(store, "tsla", ingest, seed=2, vol=3.5)


def test_portfolio_premium_matches_notional_and_nets_sum(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_two(store, ingest)
    legs = [
        PortfolioLeg(asset="spy", moneyness_pct=5, tenor_weeks=4, weight=1),
        PortfolioLeg(asset="tsla", moneyness_pct=10, tenor_weeks=4, weight=1),
    ]
    res = run_portfolio(store, legs=legs, as_of=ingest, notional=10000.0, years=1.0)

    # Weights normalize to shares that sum to 1 -> total premium == notional.
    assert res.total_premium == pytest.approx(10000.0)
    assert all(r.weight == pytest.approx(0.5) for r in res.legs)
    assert all(r.total_premium == pytest.approx(5000.0) for r in res.legs)
    # Combined net = sum of leg nets; roi consistent.
    assert res.net_pnl == pytest.approx(sum(r.net_pnl for r in res.legs))
    assert res.roi_on_premium == pytest.approx(res.net_pnl / res.total_premium)
    assert res.verdict in {"confirmed", "regime_only", "failed", "untested"}
    assert res.equity_curve
    # Two OHLCV snapshots (spy, tsla) + the vix snapshot are recorded (§a/§f).
    assert len(res.snapshot_ids) == 3


def test_weights_are_normalized_by_share(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_two(store, ingest)
    legs = [
        PortfolioLeg(asset="spy", moneyness_pct=5, tenor_weeks=4, weight=1),
        PortfolioLeg(asset="tsla", moneyness_pct=10, tenor_weeks=4, weight=3),
    ]
    res = run_portfolio(store, legs=legs, as_of=ingest, notional=10000.0, years=1.0)
    by = {r.asset: r for r in res.legs}
    assert by["spy"].total_premium == pytest.approx(2500.0)  # 1/4 of capital
    assert by["tsla"].total_premium == pytest.approx(7500.0)  # 3/4 of capital


def test_diversification_combined_dd_not_worse_than_sum(tmp_path: Path) -> None:
    """Combined max drawdown is never worse than the sum of the legs' — the
    diversification identity the cockpit shows."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_two(store, ingest)
    legs = [
        PortfolioLeg(asset="spy", moneyness_pct=5, tenor_weeks=4, weight=1),
        PortfolioLeg(asset="tsla", moneyness_pct=8, tenor_weeks=8, weight=1),
    ]
    res = run_portfolio(store, legs=legs, as_of=ingest, notional=10000.0, years=1.0)
    # Both are <= 0; combined is the shallower (>=) drop.
    assert res.combined_max_drawdown >= res.sum_individual_max_drawdown - 1e-6


def test_portfolio_respects_no_look_ahead(tmp_path: Path) -> None:
    """§a adversarial: a later restated snapshot must not change an earlier
    as-of portfolio backtest."""
    store = DeltaLakeStore(tmp_path)
    day1, day2 = dt.date(2026, 3, 2), dt.date(2026, 3, 3)
    _seed_two(store, day1)
    legs = [
        PortfolioLeg(asset="spy", moneyness_pct=5, tenor_weeks=4, weight=1),
        PortfolioLeg(asset="tsla", moneyness_pct=10, tenor_weeks=4, weight=1),
    ]
    res1 = run_portfolio(store, legs=legs, as_of=day1, notional=10000.0, years=10.0)

    # Restate everything wildly at a later snapshot; as-of day1 must be unmoved.
    _seed_vix(store, day2)
    _seed_ohlcv(store, "spy", day2, seed=99, vol=6.0)
    _seed_ohlcv(store, "tsla", day2, seed=98, vol=8.0)
    res1_again = run_portfolio(store, legs=legs, as_of=day1, notional=10000.0, years=10.0)
    assert res1_again.model_dump() == res1.model_dump()


def test_portfolio_errors(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_two(store, ingest)
    with pytest.raises(ValueError, match="at least one leg"):
        run_portfolio(store, legs=[], as_of=ingest, notional=10000.0, years=1.0)
    with pytest.raises(ValueError, match="positive"):
        run_portfolio(
            store,
            legs=[PortfolioLeg(asset="spy", moneyness_pct=5, tenor_weeks=4, weight=0)],
            as_of=ingest,
            notional=10000.0,
            years=1.0,
        )
    # A leg with no data is skipped; if none score, LookupError.
    with pytest.raises(LookupError, match="no portfolio leg"):
        run_portfolio(
            store,
            legs=[PortfolioLeg(asset="nope", moneyness_pct=5, tenor_weeks=4, weight=1)],
            as_of=ingest,
            notional=10000.0,
            years=1.0,
        )
