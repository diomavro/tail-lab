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
from tail_lab.research.backtest.ranking import FRAGILE_HIGH, RankedAsset, rank_universe
from tail_lab.research.backtest.sizing import FixedPremium, WealthFraction
from tail_lab.research.backtest.strike_rule import ByMoneyness


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
    # rank_universe never passes a PricingBasis, so every row is honestly
    # labelled model-priced, not silently defaulted.
    assert all(r.priced_from == "model" for r in ranking.ranked)
    for r in ranking.ranked:
        # Paced by the span actually traded, not the 1.0 year requested — so a
        # losing name's honest figure is MORE negative than the nominal one,
        # never less. Pinning `annualized_return(roi, 1.0)` here would
        # re-assert the flatterer this module's own MIN_WINDOW_COVERAGE
        # comment was written about.
        nominal = annualized_return(r.roi_on_premium, 1.0)
        assert r.annualized_return <= nominal
        assert r.annualized_return == pytest.approx(nominal, rel=0.2)


def test_rank_universe_snapshot_ids_cover_vix_and_every_ranked_name(tmp_path: Path) -> None:
    """`docs/STANDARDS.md`: a result without its bronze snapshot id(s) is not
    trustworthy and should not be surfaced. The SPY benchmark is cited though
    it is not ranked (every row read it). `missing` never lands in `ranked`
    (no data), so its snapshot must not appear either -- the list traces what
    was actually READ into the screen, not what was asked for."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)
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

    expected = {store.bronze_snapshot_id("vix", ingest)}
    expected |= {store.bronze_snapshot_id(dataset_id(r.asset), ingest) for r in ranking.ranked}
    # SPY is not ranked here, but every row's fragility columns read it as the
    # benchmark, so it was read into the screen and must be cited.
    assert all(r.downside_beta is not None for r in ranking.ranked)
    expected.add(store.bronze_snapshot_id(dataset_id("spy"), ingest))
    assert set(ranking.snapshot_ids) == expected
    assert len(ranking.snapshot_ids) == len(set(ranking.snapshot_ids))  # no duplicates
    assert dataset_id("missing") not in "".join(ranking.snapshot_ids)
    # `code_sha` is a deploy identity `research/` cannot read (it may not
    # import `config`) -- the API route stamps it on; here it is the honest
    # local default, never `None`.
    assert ranking.code_sha == "unknown"


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
    different strategies in one line -- and the Workspace's best-return view
    ranks on exactly that row.
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

    prices, realized_vol = load_asof_series(store, "wild", ingest)
    at_best = run_put_roll(
        prices,
        realized_vol,
        asset="wild",
        as_of=ingest,
        notional=1000.0,
        rule=ByMoneyness(row.best_moneyness_pct),
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
        realized_vol,
        asset="wild",
        as_of=ingest,
        notional=1000.0,
        rule=ByMoneyness(2.0),
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


def test_the_best_cells_dividend_basis_is_the_best_cells_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``best_q_source`` must come from the re-roll at the best cell, not the
    screened run. Unseeded, both runs price at an unknown q and agree, so the
    re-roll (the only call passing ``include_curves=False``) is made to differ."""
    from tail_lab.research.backtest import ranking as ranking_mod

    real = ranking_mod.run_put_roll

    def fake(*args: object, **kwargs: object) -> object:
        result = real(*args, **kwargs)  # type: ignore[arg-type]
        if kwargs.get("include_curves") is False:
            return result.model_copy(update={"q_source": "measured", "priced_from": "market"})
        return result

    monkeypatch.setattr(ranking_mod, "run_put_roll", fake)
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)
    _seed_symbol(store, "wild", ingest, drift=-0.1, vol=4.0)
    row = rank_universe(
        store, symbols=("wild",), as_of=ingest, moneyness_pct=2.0, tenor_weeks=1.0, years=1.0
    ).ranked[0]
    assert row.best_q_source == "measured"
    assert row.q_source == "none"
    # The pricing tag follows the same run (the ranking is model-priced today;
    # the fake makes the best cell's run "market" so the two can differ).
    assert row.best_priced_from == "market"
    assert row.priced_from == "model"


def _metric_row(asset: str, **metrics: float) -> RankedAsset:
    # Unset metrics are None, so they drop out of the composite: equal values
    # would not, because `_frac_rank` breaks ties by position.
    base: dict[str, float | None] = dict.fromkeys(
        ("downside_beta", "co_skewness", "co_kurtosis", "tail_beta", "downside_capture"), None
    )
    return RankedAsset(
        asset=asset,
        name=asset,
        spot=100.0,
        **{**base, **metrics},
        fragility_score=None,
        roi_on_premium=0.0,
        annualized_return=0.0,
        verdict="untested",
        hit_rate=0.0,
        biggest_payoff_mult=0.0,
        n_cycles=1,
        priced_from="model",
    )


def test_the_composite_reads_its_directions_from_fragile_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One table of directions serves the composite, the bake-off and the
    Workspace's shading: flipping it must flip the order here."""
    from tail_lab.research.backtest.ranking import _score_and_sort

    def rows() -> list[RankedAsset]:
        # No other metric is set, so vol beta alone decides the order.
        return [
            _metric_row("a", vol_beta=-1.0),
            _metric_row("b", vol_beta=0.0),
            _metric_row("c", vol_beta=1.0),
        ]

    assert [r.asset for r in _score_and_sort(rows())] == ["a", "b", "c"]
    monkeypatch.setitem(FRAGILE_HIGH, "vol_beta", True)
    assert [r.asset for r in _score_and_sort(rows())] == ["c", "b", "a"]


def _reference_composite(rows: list[dict[str, float | None]]) -> list[float | None]:
    """The pre-extraction fold, written out longhand: average each row's
    cross-sectional fragility ranks over COMPOSITE_METRICS it has."""
    from tail_lab.research.backtest.ranking import COMPOSITE_METRICS, _frac_rank

    ranks = {
        m: _frac_rank([r.get(m) for r in rows], fragile_high=FRAGILE_HIGH[m])
        for m in COMPOSITE_METRICS
    }
    out: list[float | None] = []
    for i in range(len(rows)):
        parts = [ranks[m][i] for m in COMPOSITE_METRICS if i in ranks[m]]
        out.append(sum(parts) / len(parts) if parts else None)
    return out


def test_composite_scores_pins_a_hand_computed_case() -> None:
    """Three names, one metric each way: the most fragile on every metric
    scores 1.0, the least 0.0, and a name with no metrics scores None --
    co-kurtosis is ignored even when present (it is excluded by design)."""
    from tail_lab.research.backtest.ranking import COMPOSITE_METRICS, composite_scores

    fragile = {m: (10.0 if FRAGILE_HIGH[m] else -10.0) for m in COMPOSITE_METRICS}
    calm = {m: (-10.0 if FRAGILE_HIGH[m] else 10.0) for m in COMPOSITE_METRICS}
    mid = dict.fromkeys(COMPOSITE_METRICS, 0.0)
    empty: dict[str, float | None] = {"co_kurtosis": 99.0}
    scores = composite_scores([calm, fragile, mid, empty])
    assert scores == [0.0, 1.0, 0.5, None]


def test_composite_scores_equals_the_old_fold_and_the_shipped_sort() -> None:
    from tail_lab.research.backtest.ranking import (
        COMPOSITE_METRICS,
        _score_and_sort,
        composite_scores,
    )

    rng = np.random.default_rng(5)
    rows: list[dict[str, float | None]] = []
    for _ in range(12):
        rows.append(
            {m: (None if rng.random() < 0.25 else float(rng.normal())) for m in COMPOSITE_METRICS}
        )
    assert composite_scores(rows) == _reference_composite(rows)

    assets = [
        RankedAsset.model_construct(asset=f"a{i}", fragility_score=None, **row)
        for i, row in enumerate(rows)
    ]
    ordered = _score_and_sort(list(assets))
    expected = composite_scores(rows)
    for asset, score in zip(assets, expected, strict=True):
        assert asset.fragility_score == score
    keys = [a.fragility_score if a.fragility_score is not None else -1.0 for a in ordered]
    assert keys == sorted(keys, reverse=True)  # the sort is kept


def test_fragility_from_returns_keeps_the_two_pairs_separately_joined() -> None:
    """Vol beta regresses on VIX changes, whose dates differ from SPY's. If the
    two pairs shared one inner join, every benchmark metric would silently
    shrink to the VIX overlap. The wrapper must equal an independent
    computation that joins each pair on its own."""
    from tail_lab.research.backtest.ranking import (
        FRAGILITY_METRIC_FUNCS,
        _returns,
        fragility_from_returns,
        trailing_fragility,
    )
    from tail_lab.research.metrics.vol_beta import vol_beta

    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2024-01-02", periods=300)
    market = pd.Series(rng.normal(0, 0.01, 300), index=idx)
    asset_px = pd.Series(
        100 * np.exp(np.cumsum(1.2 * market + rng.normal(0, 0.005, 300))), index=idx
    )
    holes = idx[250:255]  # the asset trades, SPY has no row: dropped, never 0-filled
    bench_ret = market.iloc[1:].drop(holes)
    vix_dates = idx[100::2]  # every other date: a different overlap than SPY's
    vix_chg = pd.Series(rng.normal(0, 0.05, len(vix_dates)), index=vix_dates)

    got = trailing_fragility(asset_px, bench_ret=bench_ret, vol_changes=vix_chg, lookback_days=120)

    a = _returns(asset_px)
    bench = pd.concat({"a": a, "b": bench_ret}, axis=1).dropna().iloc[-120:]
    vol = pd.concat({"a": a, "v": vix_chg}, axis=1).dropna().iloc[-120:]
    expected = {n: fn(bench["a"], bench["b"]) for n, fn in FRAGILITY_METRIC_FUNCS.items()}
    expected["vol_beta"] = vol_beta(vol["a"], vol["v"])
    assert got == pytest.approx(expected)
    assert fragility_from_returns(bench, vol) == got
    assert len(bench) == 120 and len(vol) == 100  # the windows are NOT the same rows
    assert not bench.index.isin(holes).any()
    assert not bench.index.equals(vol.index)


def test_fragility_from_returns_reports_an_unestimable_metric_as_none() -> None:
    from tail_lab.research.backtest.ranking import FRAGILITY_METRIC_FUNCS, fragility_from_returns

    empty_b = pd.DataFrame({"a": [0.0], "b": [0.0]})
    empty_v = pd.DataFrame({"a": [0.0], "v": [0.0]})
    out = fragility_from_returns(empty_b, empty_v)
    assert set(out) == {*FRAGILITY_METRIC_FUNCS, "vol_beta"}
    assert all(v is None for v in out.values())


def _independent_fragility(
    asset_px: pd.Series, bench_px: pd.Series | None, vix: pd.Series, lookback_days: int
) -> dict[str, float | None]:
    """Each metric called directly on its own hand-joined window -- no
    ranking helper involved, so a key swapped between a metric and its column
    shows up as a mismatch."""
    from tail_lab.research.metrics.co_kurtosis import co_kurtosis
    from tail_lab.research.metrics.co_skewness import co_skewness
    from tail_lab.research.metrics.downside_beta import downside_beta
    from tail_lab.research.metrics.downside_capture import downside_capture
    from tail_lab.research.metrics.tail_beta import tail_beta
    from tail_lab.research.metrics.vol_beta import vol_beta

    a = asset_px.pct_change().dropna()
    vol = pd.concat({"a": a, "v": vix.pct_change().dropna()}, axis=1).dropna()
    vol = vol.iloc[-lookback_days:]
    out: dict[str, float | None] = {"vol_beta": vol_beta(vol["a"], vol["v"])}
    funcs = {
        "downside_beta": downside_beta,
        "co_skewness": co_skewness,
        "co_kurtosis": co_kurtosis,
        "tail_beta": tail_beta,
        "downside_capture": downside_capture,
    }
    if bench_px is None:
        return out | dict.fromkeys(funcs)
    bench = pd.concat({"a": a, "b": bench_px.pct_change().dropna()}, axis=1).dropna()
    bench = bench.iloc[-lookback_days:]
    return out | {n: fn(bench["a"], bench["b"]) for n, fn in funcs.items()}


@pytest.mark.parametrize("with_benchmark", [True, False])
def test_rank_universe_columns_are_the_shared_fragility_fold(
    tmp_path: Path, with_benchmark: bool
) -> None:
    """The Screen's six metric columns equal each metric computed on its own
    window, with SPY present and absent. Without SPY the five benchmark
    metrics are None, but vol beta (VIX only) is still estimated."""
    from tail_lab.research.backtest.put_roll import load_asof_series
    from tail_lab.research.regimes.timeline import load_vix_close

    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    if with_benchmark:
        _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)
    _seed_symbol(store, "wild", ingest, drift=-0.1, vol=4.0)

    ranking = rank_universe(
        store, symbols=("wild",), as_of=ingest, moneyness_pct=5.0, tenor_weeks=4.0, years=1.0
    )

    (row,) = ranking.ranked
    asset_px, _ = load_asof_series(store, "wild", ingest)
    bench_px = load_asof_series(store, "spy", ingest)[0] if with_benchmark else None
    vix = load_vix_close(store, as_of=ingest)
    expected = _independent_fragility(asset_px, bench_px, vix, lookback_days=252)
    got = {m: getattr(row, m) for m in expected}
    assert got == pytest.approx(expected)
    assert row.vol_beta is not None
    if not with_benchmark:
        assert all(got[m] is None for m in expected if m != "vol_beta")


def _seed_chain(store: DeltaLakeStore, session: dt.date, underlying: str, spot: float) -> None:
    """One listed 30-day expiry, whole-dollar strikes, a flat 1.25 ask."""
    strikes = np.arange(np.floor(spot * 0.7), np.ceil(spot), 1.0)
    store.write_bronze(
        "option_chain_snapshot",
        session,
        pd.DataFrame(
            {
                "underlying": underlying,
                "quote_date": pd.Timestamp(session),
                "expiration": pd.Timestamp(session + dt.timedelta(days=30)),
                "strike": strikes,
                "spot": spot,
                "iv": 0.3,
                "ask": 1.25,
            }
        ),
    )


def test_the_screen_scores_its_strike_by_convexity_both_ways(tmp_path: Path) -> None:
    """Each row's two convexity ratios are today's screened strike, against an
    independent Black-Scholes price (model) and the listed ask (market); a
    name outside the chain has no market ratio, and the chain is cited."""
    from statistics import NormalDist

    from tail_lab.research.backtest.put_roll import load_asof_series

    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "spy", ingest, drift=0.1, vol=1.0)
    _seed_symbol(store, "wild", ingest, drift=-0.1, vol=4.0)
    _seed_chain(store, ingest, "SPY", spot=200.0)

    ranking = rank_universe(
        store, symbols=("spy", "wild"), as_of=ingest, moneyness_pct=5.0, tenor_weeks=4.0, years=1.0
    )
    rows = {r.asset: r for r in ranking.ranked}

    # Market: 5% below the chain's 200 is 190, listed; a 20% fall leaves 160,
    # so 30 of intrinsic over 1.25 x 365/30 of a year's asks.
    assert rows["spy"].market_convexity_ratio == pytest.approx(30.0 / (1.25 * 365 / 30))
    assert rows["wild"].market_convexity_ratio is None
    assert ranking.chain_session == ingest
    assert store.bronze_snapshot_id("option_chain_snapshot", ingest) in ranking.snapshot_ids

    # Model: K = 0.95 S on the latest close, the latest realised vol clamped to
    # [0.06, 2.0], 20/252 of a year, r = 4%, q = 0 (no dividend snapshot).
    prices, rv = load_asof_series(store, "wild", ingest)
    s, t, r = float(prices.iloc[-1]), 20 / 252, 0.04
    v = min(max(float(rv.iloc[-1]), 0.06), 2.0)
    k = 0.95 * s
    n = NormalDist().cdf
    d1 = (np.log(s / k) + (r + v * v / 2) * t) / (v * np.sqrt(t))
    bs = k * np.exp(-r * t) * n(-(d1 - v * np.sqrt(t))) - s * n(-d1)
    assert rows["wild"].convexity_ratio == pytest.approx((k - 0.8 * s) / (bs / t), rel=1e-9)


def test_no_collected_chain_leaves_every_market_ratio_empty(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest)
    _seed_symbol(store, "wild", ingest, drift=-0.1, vol=4.0)

    ranking = rank_universe(
        store, symbols=("wild",), as_of=ingest, moneyness_pct=5.0, tenor_weeks=4.0, years=1.0
    )

    (row,) = ranking.ranked
    assert row.market_convexity_ratio is None and row.convexity_ratio is not None
    assert ranking.chain_session is None
    assert not any("option_chain" in snap for snap in ranking.snapshot_ids)
