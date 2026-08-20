"""Tests for universe ranking (``research/backtest/ranking.py``)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.put_roll import annualized_return
from tail_lab.research.backtest.ranking import rank_universe


def _seed_symbol(
    store: DeltaLakeStore, symbol: str, ingest: dt.date, drift: float, vol: float, n: int = 320
) -> None:
    rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
    steps = rng.normal(drift, vol, size=n)
    closes = np.clip(100 * np.exp(np.cumsum(steps / 100)), 5, None)
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


def test_rank_universe_sorts_by_fragility_and_skips_missing(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)  # the benchmark
    _seed_symbol(store, "calm", ingest, drift=0.4, vol=0.8)
    _seed_symbol(store, "wild", ingest, drift=-0.1, vol=4.0)

    ranking = rank_universe(
        store,
        symbols=("calm", "wild", "missing"),
        as_of=ingest,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
        years=1.0,
    )

    ranked_symbols = [r.asset for r in ranking.ranked]
    assert set(ranked_symbols) == {"calm", "wild"}  # 'missing' has no data -> skipped
    # Fragility is estimated (benchmark present) and drives the sort, most first.
    scores = [r.fragility_score for r in ranking.ranked]
    assert all(s is not None for s in scores)
    assert scores == sorted(scores, key=lambda s: s or -1.0, reverse=True)
    # Each row carries the fragility metrics + the put backtest fields.
    row = ranking.ranked[0]
    assert row.spot > 0
    assert row.downside_beta is not None and row.co_kurtosis is not None
    assert row.verdict in {"confirmed", "regime_only", "failed", "untested"}
    assert row.n_cycles >= 1
    for r in ranking.ranked:
        assert r.annualized_return == pytest.approx(annualized_return(r.roi_on_premium, 1.0))


def test_rank_universe_without_benchmark_leaves_fragility_none(tmp_path: Path) -> None:
    """No SPY benchmark -> fragility can't be estimated; the put backtest still
    ranks (fragility fields just come back None)."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "calm", ingest, drift=0.4, vol=0.8)
    ranking = rank_universe(
        store, symbols=("calm",), as_of=ingest, moneyness_pct=5.0, tenor_weeks=4.0, years=1.0
    )
    assert ranking.ranked[0].downside_beta is None
    assert ranking.ranked[0].fragility_score is None
    assert ranking.ranked[0].n_cycles >= 1  # backtest still ran


def test_rank_universe_missing_vix_raises(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_symbol(store, "spy", ingest, drift=0.2, vol=1.0)  # OHLCV but no VIX
    with pytest.raises(LookupError, match="no VIX"):
        rank_universe(
            store,
            symbols=("spy",),
            as_of=ingest,
            moneyness_pct=5.0,
            tenor_weeks=4.0,
            years=1.0,
        )
