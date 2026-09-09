"""Tests for universe ranking (``research/backtest/ranking.py``)."""

from __future__ import annotations

import datetime as dt
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.put_roll import annualized_return
from tail_lab.research.backtest.ranking import rank_universe
from tail_lab.research.backtest.sizing import FixedPremium, WealthFraction


def _seed_symbol(
    store: DeltaLakeStore, symbol: str, ingest: dt.date, drift: float, vol: float, n: int = 320
) -> None:
    # crc32, not hash(): str hashing is randomised per process (PYTHONHASHSEED),
    # so `default_rng(hash(symbol))` reads as seeded while reseeding differently
    # on every run -- which made this module's synthetic paths, and any test
    # asserting on their shape, quietly non-deterministic. crc32 is stable
    # across processes and distinct per symbol (unlike the `len(symbol) * 7 + 1`
    # used in test_research_backtest_metric_screen.py, which collides for
    # equal-length names).
    rng = np.random.default_rng(zlib.crc32(symbol.encode()))
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
    assert row.vol_beta is not None
    assert row.verdict in {"confirmed", "regime_only", "failed", "untested"}
    assert row.n_cycles >= 1
    for r in ranking.ranked:
        # Paced by the span actually traded, not the 1.0 year requested — so a
        # losing name's honest figure is MORE negative than the nominal one,
        # never less. Pinning `annualized_return(roi, 1.0)` here would
        # re-assert the flatterer this module's own MIN_WINDOW_COVERAGE
        # comment was written about.
        nominal = annualized_return(r.roi_on_premium, 1.0)
        assert r.annualized_return <= nominal
        assert r.annualized_return == pytest.approx(nominal, rel=0.2)


def test_rank_universe_sizing_mode_overrides_notional(tmp_path: Path) -> None:
    """``sizing_mode`` resolves the per-name budget and ``notional`` is
    ignored; it resolves with ``n_legs=len(symbols)`` since every screened
    name is priced independently at the same budget, so ``FixedPremium(x)``
    reproduces plain ``notional=x`` and ``WealthFraction(alpha, wealth)``
    resolves to ``alpha * wealth / len(symbols)``."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)
    _seed_symbol(store, "calm", ingest, drift=0.4, vol=0.8)
    _seed_symbol(store, "wild", ingest, drift=-0.1, vol=4.0)
    kw = dict(symbols=("calm", "wild"), as_of=ingest, moneyness_pct=5.0, tenor_weeks=4.0, years=1.0)

    plain = rank_universe(store, notional=1000.0, **kw)  # type: ignore[arg-type]
    via_fixed = rank_universe(store, notional=1.0, sizing_mode=FixedPremium(1000.0), **kw)  # type: ignore[arg-type]
    assert via_fixed.model_dump() == plain.model_dump()

    # 2 symbols -> each gets alpha * wealth / 2 == 0.5 * 4000 / 2 == 1000.
    via_wealth = rank_universe(
        store, notional=1.0, sizing_mode=WealthFraction(alpha=0.5, wealth=4000.0), **kw
    )  # type: ignore[arg-type]
    assert via_wealth.notional == pytest.approx(1000.0)
    assert via_wealth.model_dump() == plain.model_dump()


def test_rank_universe_without_benchmark_leaves_fragility_none(tmp_path: Path) -> None:
    """No SPY benchmark -> the SPY-regressed fragility metrics can't be
    estimated; the put backtest still ranks (those fields just come back
    None). Vol beta is the exception: it regresses against VIX, not SPY, so
    it is still estimable and the composite still gets one metric to rank on."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "calm", ingest, drift=0.4, vol=0.8)
    ranking = rank_universe(
        store, symbols=("calm",), as_of=ingest, moneyness_pct=5.0, tenor_weeks=4.0, years=1.0
    )
    assert ranking.ranked[0].downside_beta is None
    assert ranking.ranked[0].vol_beta is not None
    assert ranking.ranked[0].fragility_score is not None
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


def test_a_short_history_name_is_dropped_rather_than_flattered(tmp_path: Path) -> None:
    """`annualized_return` divides the total ROI by the *requested* lookback,
    not by the window actually traded. A name listed two years ago therefore
    has a two-year loss annualized as if over four — which shrinks it toward
    zero and floats the name up a ranking sorted on that number.

    Silently comparing it against names with the full window is the bug; the
    honest move is to leave it out until it has the history.
    """
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)
    # 320 business days covers ~1.07y of rolls; 260 covers ~0.84y. Against a
    # 1-year ask that is the same ratio the real case has against a 4-year one,
    # and it keeps the fixture small.
    _seed_symbol(store, "full", ingest, drift=-0.1, vol=3.0)
    _seed_symbol(store, "young", ingest, drift=-0.1, vol=3.0, n=260)

    ranking = rank_universe(
        store,
        symbols=("full", "young"),
        as_of=ingest,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
        years=1.0,
    )

    assert [r.asset for r in ranking.ranked] == ["full"]


def test_a_name_with_the_full_window_is_kept(tmp_path: Path) -> None:
    """The guard must not quietly empty the universe: a name whose history
    covers the asked-for window still ranks."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)
    _seed_symbol(store, "full", ingest, drift=-0.1, vol=3.0)

    ranking = rank_universe(
        store,
        symbols=("full",),
        as_of=ingest,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
        years=1.0,
    )

    assert [r.asset for r in ranking.ranked] == ["full"]


def test_the_best_cells_stats_all_describe_the_best_cell(tmp_path: Path) -> None:
    """A recommendation is a whole strategy, so every figure on its row has to
    come from the same (strike, tenor) run. Reporting `best_annualized` from
    the argmax next to a `hit_rate` from the screened cell describes two
    different strategies in one line -- and the recommendations page ranks on
    exactly that row.
    """
    from tail_lab.research.backtest.put_roll import load_asof_series, run_put_roll

    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)
    _seed_symbol(store, "wild", ingest, drift=-0.1, vol=4.0)

    ranking = rank_universe(
        store,
        symbols=("wild",),
        as_of=ingest,
        moneyness_pct=2.0,  # deliberately NOT where the argmax lands
        tenor_weeks=1.0,
        years=1.0,
    )
    row = ranking.ranked[0]
    assert row.best_moneyness_pct is not None and row.best_tenor_weeks is not None

    prices, iv = load_asof_series(store, "wild", ingest)
    at_best = run_put_roll(
        prices,
        iv,
        asset="wild",
        as_of=ingest,
        notional=1000.0,
        moneyness_pct=row.best_moneyness_pct,
        tenor_weeks=row.best_tenor_weeks,
        lookback_years=1.0,
        include_curves=False,
    )

    assert row.best_roi_on_premium == pytest.approx(at_best.roi_on_premium)
    assert row.best_hit_rate == pytest.approx(at_best.hit_rate)
    assert row.best_n_cycles == at_best.n_cycles
    assert row.best_annualized == pytest.approx(annualized_return(at_best.roi_on_premium, 1.0))
    # ...and the screened-cell figures are still their own, unmixed. Asserted
    # positively -- against an independent run at the *screened* parameters --
    # rather than as "the best cell differs from the screened one", which was
    # only true when the argmax happened not to land on 2% OOM and so failed
    # on roughly one seed in ten.
    at_screened = run_put_roll(
        prices,
        iv,
        asset="wild",
        as_of=ingest,
        notional=1000.0,
        moneyness_pct=2.0,
        tenor_weeks=1.0,
        lookback_years=1.0,
        include_curves=False,
    )
    assert row.roi_on_premium == pytest.approx(at_screened.roi_on_premium)
    assert row.hit_rate == pytest.approx(at_screened.hit_rate)
    assert row.n_cycles == at_screened.n_cycles


def test_the_ranking_does_not_sweep_cells_it_could_never_pick(tmp_path: Path) -> None:
    """`best_point` is bounded to the priced band, so the deep half of the grid
    can only ever be discarded. At 70 names that is most of the ranking's
    runtime spent computing numbers it is required to ignore."""
    from tail_lab.research.backtest import ranking as ranking_mod
    from tail_lab.research.backtest.sweep import MODEL_PRICED_SWEEP_MONEYNESS, run_sweep

    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)
    _seed_symbol(store, "wild", ingest, drift=-0.1, vol=4.0)

    swept: list[tuple[float, ...]] = []

    def _spy_on_sweep(*args: object, **kwargs: object) -> object:
        swept.append(tuple(kwargs.get("moneyness_grid", ())))
        return run_sweep(*args, **kwargs)  # type: ignore[arg-type]

    original = ranking_mod.run_sweep
    ranking_mod.run_sweep = _spy_on_sweep  # type: ignore[assignment]
    try:
        rank_universe(
            store,
            symbols=("wild",),
            as_of=ingest,
            moneyness_pct=5.0,
            tenor_weeks=4.0,
            years=1.0,
        )
    finally:
        ranking_mod.run_sweep = original  # type: ignore[assignment]

    assert swept == [MODEL_PRICED_SWEEP_MONEYNESS]
