"""Tests for the metric bake-off (``research/backtest/metric_screen.py``)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.metric_screen import (
    _METRIC_FUNCS,
    compare_metric_screens,
)
from tail_lab.research.backtest.put_roll import annualized_return

_ALL_SCREENS = {*_METRIC_FUNCS, "fragility_score"}


def _write_closes(store: DeltaLakeStore, symbol: str, ingest: dt.date, closes: np.ndarray) -> None:
    dates = pd.date_range(end=ingest, periods=len(closes), freq="B")
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
                "volume": np.full(len(closes), 1_000_000, dtype=int),
                "adj_close": closes,
            }
        ),
    )


def _seed_symbol(
    store: DeltaLakeStore, symbol: str, ingest: dt.date, drift: float, vol: float, n: int = 320
) -> None:
    """Seed one independent geometric-random-walk price path (fixed seed so the
    path is deterministic across processes, unlike ``hash(symbol)``)."""
    rng = np.random.default_rng(len(symbol) * 7 + 1)
    steps = rng.normal(drift, vol, size=n)
    closes = np.clip(100 * np.exp(np.cumsum(steps / 100)), 5, None)
    _write_closes(store, symbol, ingest, closes)


def _seed_vix(store: DeltaLakeStore, ingest: dt.date, n: int = 320) -> None:
    vix = 14 + 12 * (np.sin(np.linspace(0, 12, n)) + 1)
    dates = pd.date_range(end=ingest, periods=n, freq="B")
    store.write_bronze("vix", ingest, pd.DataFrame({"date": dates, "close": vix}))


def _seeded_store(tmp_path: Path, ingest: dt.date, n: int = 320) -> DeltaLakeStore:
    """A universe built from a *shared market factor* so fragility is
    unambiguous: each name's returns are beta*market + small idiosyncratic
    noise, making 'wild' (beta 3) strictly the highest-downside-beta name and
    'calm' (beta 0.3) the lowest — deterministic across processes."""
    store = DeltaLakeStore(tmp_path)
    _seed_vix(store, ingest, n)
    rng = np.random.default_rng(0)
    market = rng.normal(0.0002, 0.011, size=n)  # SPY daily returns

    def _from_beta(beta: float, noise: float, seed: int) -> np.ndarray:
        r = np.random.default_rng(seed)
        ret = beta * market + r.normal(0.0, noise, size=n)
        closes: np.ndarray = 100.0 * np.exp(np.cumsum(ret))
        return np.clip(closes, 5, None)

    _write_closes(store, "spy", ingest, np.clip(100.0 * np.exp(np.cumsum(market)), 5, None))
    _write_closes(store, "wild", ingest, _from_beta(3.0, 0.004, 1))  # high-beta / fragile
    _write_closes(store, "calm", ingest, _from_beta(0.3, 0.004, 2))  # defensive
    _write_closes(store, "mid", ingest, _from_beta(1.5, 0.004, 3))  # in between
    return store


def test_bakeoff_covers_all_screens_and_is_sorted(tmp_path: Path) -> None:
    ingest = dt.date(2026, 3, 2)
    store = _seeded_store(tmp_path, ingest)

    cmp = compare_metric_screens(
        store,
        symbols=("spy", "wild", "calm", "mid", "missing"),
        as_of=ingest,
        moneyness_pct=10.0,
        tenor_weeks=4.0,
        years=1.0,
        top_k=2,
    )

    # Every one of the six screens appears exactly once.
    assert {e.metric for e in cmp.entries} == _ALL_SCREENS
    # 'missing' has no data -> skipped; the other four scored.
    assert cmp.universe_size == 4
    assert isinstance(cmp.baseline_roi, float)
    assert cmp.top_k == 2

    # Entries are sorted best-first by blended ROI on premium.
    rois = [e.roi_on_premium for e in cmp.entries]
    assert rois == sorted(rois, reverse=True)

    for e in cmp.entries:
        assert e.label
        assert 0 < len(e.top_k_assets) <= 2  # top_k picks, all scored names
        assert set(e.top_k_assets) <= {"spy", "wild", "calm", "mid"}
        assert 0.0 <= e.hit_rate <= 1.0
        assert e.combined_max_drawdown <= 0.0  # drawdown is a peak-to-trough drop
        assert e.verdict in {"confirmed", "regime_only", "failed", "untested"}
        assert e.spearman_vs_payoff is None or -1.0 <= e.spearman_vs_payoff <= 1.0
        # lift is ROI measured against the shared baseline.
        assert e.lift_vs_baseline == pytest.approx(e.roi_on_premium - cmp.baseline_roi)
        assert e.annualized_return == pytest.approx(
            annualized_return(e.roi_on_premium, cmp.lookback_years)
        )


def test_downside_beta_basket_holds_the_wild_name(tmp_path: Path) -> None:
    """A magnitude metric like downside beta should rank the high-vol 'wild'
    name as fragile and put it in its top-k basket."""
    ingest = dt.date(2026, 3, 2)
    store = _seeded_store(tmp_path, ingest)

    cmp = compare_metric_screens(
        store,
        symbols=("spy", "wild", "calm", "mid"),
        as_of=ingest,
        moneyness_pct=10.0,
        tenor_weeks=4.0,
        years=1.0,
        top_k=2,
    )
    db = next(e for e in cmp.entries if e.metric == "downside_beta")
    assert "wild" in db.top_k_assets

    # The composite screen produces a real per-regime breakdown.
    composite = next(e for e in cmp.entries if e.metric == "fragility_score")
    assert composite.regime_slices
    for sl in composite.regime_slices:
        assert sl.regime in {"calm", "elevated", "crisis"}


def test_deterministic_baseline_equals_mean_roi(tmp_path: Path) -> None:
    """Pinned property: baseline_roi is exactly the mean of the per-name put
    ROIs (the 'buy puts on everyone' baseline), independent of screen."""
    from tail_lab.research.backtest.put_roll import load_asof_series, run_put_roll

    ingest = dt.date(2026, 3, 2)
    store = _seeded_store(tmp_path, ingest)
    symbols = ("spy", "wild", "calm", "mid")

    cmp = compare_metric_screens(
        store,
        symbols=symbols,
        as_of=ingest,
        moneyness_pct=10.0,
        tenor_weeks=4.0,
        years=1.0,
        top_k=3,
    )

    rois = []
    for sym in symbols:
        prices, iv = load_asof_series(store, sym, ingest)
        res = run_put_roll(
            prices,
            iv,
            asset=sym,
            as_of=ingest,
            notional=1.0,
            moneyness_pct=10.0,
            tenor_weeks=4.0,
            lookback_years=1.0,
        )
        rois.append(res.roi_on_premium)
    assert cmp.baseline_roi == pytest.approx(sum(rois) / len(rois))


def test_missing_vix_raises(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_symbol(store, "spy", ingest, drift=0.2, vol=1.0)  # OHLCV but no VIX
    with pytest.raises(LookupError, match="no VIX"):
        compare_metric_screens(
            store,
            symbols=("spy",),
            as_of=ingest,
            moneyness_pct=10.0,
            tenor_weeks=4.0,
            years=1.0,
        )


def test_missing_benchmark_raises(tmp_path: Path) -> None:
    """No SPY benchmark -> the fragility metrics can't be computed at all."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "wild", ingest, drift=-0.1, vol=4.0)
    with pytest.raises(LookupError, match="benchmark"):
        compare_metric_screens(
            store,
            symbols=("wild",),
            as_of=ingest,
            moneyness_pct=10.0,
            tenor_weeks=4.0,
            years=1.0,
        )


def test_no_scorable_name_raises(tmp_path: Path) -> None:
    """VIX + SPY exist, but every requested name lacks data -> LookupError."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)
    with pytest.raises(LookupError, match="no universe name could be scored"):
        compare_metric_screens(
            store,
            symbols=("nope", "alsonope"),
            as_of=ingest,
            moneyness_pct=10.0,
            tenor_weeks=4.0,
            years=1.0,
        )
