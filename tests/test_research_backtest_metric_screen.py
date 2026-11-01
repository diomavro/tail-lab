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
    _unfloored,
    compare_metric_screens,
)
from tail_lab.research.backtest.put_roll import (
    PutBacktestResult,
    PutRollCycle,
    annualized_return,
    load_asof_series,
    run_put_roll,
)
from tail_lab.research.backtest.strike_rule import ByMoneyness

_ALL_SCREENS = {*_METRIC_FUNCS, "vol_beta", "fragility_score"}


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
        rule=ByMoneyness(10.0),
        tenor_weeks=4.0,
        years=1.0,
        top_k=2,
    )

    # Every one of the seven screens appears exactly once.
    assert {e.metric for e in cmp.entries} == _ALL_SCREENS
    # 'missing' has no data -> skipped. 'calm' is so quiet that every 10%-OOM
    # roll was priced at the premium floor, which is not a price -> not scored.
    # SPY keeps 10 of its 12 rolls. Every floored roll is counted: SPY's 2
    # and calm's 12.
    assert cmp.universe_size == 3
    assert cmp.n_names_all_floored == 1
    assert cmp.n_floored_legs_excluded == 14
    assert isinstance(cmp.baseline_roi, float)
    assert cmp.top_k == 2

    # Entries are sorted best-first by blended ROI on premium.
    rois = [e.roi_on_premium for e in cmp.entries]
    assert rois == sorted(rois, reverse=True)

    for e in cmp.entries:
        assert e.label
        assert 0 < len(e.top_k_assets) <= 2  # top_k picks, all scored names
        assert set(e.top_k_assets) <= {"spy", "wild", "mid"}
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
        rule=ByMoneyness(10.0),
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


def _independent_roi(
    store: DeltaLakeStore, symbol: str, ingest: dt.date, *, commission: float
) -> float | None:
    """A name's put ROI recomputed outside the bake-off, from its unfloored
    rolls only (``None`` when every roll was floored)."""
    prices, realized_vol = load_asof_series(store, symbol, ingest)
    res = run_put_roll(
        prices,
        realized_vol,
        asset=symbol,
        as_of=ingest,
        notional=1.0,
        rule=ByMoneyness(10.0),
        tenor_weeks=4.0,
        lookback_years=1.0,
        commission_per_contract=commission,
    )
    kept = [c for c in res.cycles if not c.premium_floored]
    if not kept:
        return None
    return sum(c.net for c in kept) / sum(c.payoff - c.net - c.cost for c in kept)


def _roll(day: int, *, floored: bool, payoff: float) -> PutRollCycle:
    """One unit-budget roll: premium budget 1.0, half-spread cost 0.03."""
    entry = dt.date(2025, 1, 1) + dt.timedelta(days=28 * day)
    return PutRollCycle(
        entry_date=entry,
        expiry_date=entry + dt.timedelta(days=28),
        spot=100.0,
        strike=90.0,
        sigma=0.06,
        premium=0.01,
        premium_floored=floored,
        contracts=100.0,
        cost=0.03,
        payoff=payoff,
        net=payoff - 1.0 - 0.03,
    )


def test_a_floored_roll_never_reaches_a_names_roi() -> None:
    """The partial-floor case, pinned directly: a floored roll that paid a
    fortune (the Feb 2020 SPY case paid ~2,122x) must not lift the name's ROI.
    Kept: two rolls, one paying 3.0 -> ROI (1.97 - 1.03) / 2 = 0.47."""
    cycles = [
        _roll(0, floored=False, payoff=3.0),
        _roll(1, floored=True, payoff=2122.0),
        _roll(2, floored=False, payoff=0.0),
    ]
    result = PutBacktestResult.model_construct(cycles=cycles)
    name = _unfloored("spy", {}, result)
    assert [c.entry_date for c in name.cycles] == [cycles[0].entry_date, cycles[2].entry_date]
    assert name.n_floored == 1
    assert name.roi == pytest.approx((3.0 - 1.03 - 1.03) / 2.0)


def test_baseline_and_baskets_are_commission_free_and_floor_free(tmp_path: Path) -> None:
    """The cost rules must not be what the bake-off sorts (found 2026-10-31:
    co-kurtosis "won" at +116% on floored legs and commission). Every name's
    ROI, the baseline and every basket are judged on unfloored legs without
    commission. In this fixture 'calm' has all 12 rolls floored and commission
    moves every ROI. SPY's 2 floored rolls lose everything like its others,
    so they cannot pin the partial-floor case: the test below does."""
    ingest = dt.date(2026, 3, 2)
    store = _seeded_store(tmp_path, ingest)
    symbols = ("spy", "wild", "calm", "mid")

    cmp = compare_metric_screens(
        store,
        symbols=symbols,
        as_of=ingest,
        rule=ByMoneyness(10.0),
        tenor_weeks=4.0,
        years=1.0,
        top_k=2,
    )

    free = {s: _independent_roi(store, s, ingest, commission=0.0) for s in symbols}
    charged = {s: _independent_roi(store, s, ingest, commission=0.65) for s in symbols}
    assert free["calm"] is None  # all floored: not scored at all
    rois = {s: r for s, r in free.items() if r is not None}
    # The fixture discriminates: commission really moves the ROIs.
    assert all(abs(rois[s] - (charged[s] or 0.0)) > 1e-3 for s in rois)

    assert cmp.baseline_roi == pytest.approx(sum(rois.values()) / len(rois))
    for e in cmp.entries:
        # Equal-weight basket, each name's premium == its budget, so the
        # basket ROI is the mean of its picks' ROIs.
        assert e.roi_on_premium == pytest.approx(
            sum(rois[a] for a in e.top_k_assets) / len(e.top_k_assets)
        ), e.metric


def test_missing_vix_raises(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_symbol(store, "spy", ingest, drift=0.2, vol=1.0)  # OHLCV but no VIX
    with pytest.raises(LookupError, match="no VIX"):
        compare_metric_screens(
            store,
            symbols=("spy",),
            as_of=ingest,
            rule=ByMoneyness(10.0),
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
            rule=ByMoneyness(10.0),
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
            rule=ByMoneyness(10.0),
            tenor_weeks=4.0,
            years=1.0,
        )


def _seeded_noise_store(
    tmp_path: Path, ingest: dt.date, *, n_symbols: int, n: int = 320
) -> DeltaLakeStore:
    """A universe with **no real relationship** between any fragility metric
    and subsequent put payoff: every name (including the benchmark) is an
    independent geometric random walk, so any metric-vs-ROI correlation the
    bake-off measures is sampling noise by construction -- the "pure noise
    through the grid" case `docs/AGENT_TODO.md`'s multiple-testing item asks
    for, pinned against Benjamini-Hochberg's own textbook behavior rather than
    a hand-derived analytic value (STANDARDS.md's "deterministic seeded
    fixture" pattern, same shape as `_seeded_store` above)."""
    store = DeltaLakeStore(tmp_path)
    _seed_vix(store, ingest, n)
    # Seed 9 is pinned deliberately (re-pinned 2026-11-01, when the bake-off
    # dropped commission and floored legs and seed 12 stopped showing it): it is one of
    # several draws where the uncorrected per-screen hurdle names a winner by
    # chance (exactly what seven simultaneous tests on pure noise sometimes
    # does) while Benjamini-Hochberg still declares none -- the contrast the
    # test below exists to demonstrate, not just "no relationship anywhere".
    rng = np.random.default_rng(9)
    spy_ret = rng.normal(0.0002, 0.011, size=n)
    _write_closes(store, "spy", ingest, np.clip(100.0 * np.exp(np.cumsum(spy_ret)), 5, None))
    for i in range(n_symbols):
        r = np.random.default_rng(9_000 + i)
        ret = r.normal(
            0.0, 0.011, size=n
        )  # zero drift, zero beta to spy -- pure idiosyncratic noise
        closes = np.clip(100.0 * np.exp(np.cumsum(ret)), 5, None)
        _write_closes(store, f"noise{i}", ingest, closes)
    return store


def test_bakeoff_multiple_testing_correction_on_pure_noise(tmp_path: Path) -> None:
    """The `docs/END_STATE.md` §4 Q1 multiple-testing item, pinned end to end:
    with no real metric-vs-payoff relationship anywhere in the universe, the
    uncorrected per-screen hurdle (`significant_raw`, p < fdr_alpha) can still
    call a screen a winner by chance across seven simultaneous tests, but the
    Benjamini-Hochberg-corrected reading (`significant_corrected`) must not
    -- that gap is the entire point of running the correction at all."""
    ingest = dt.date(2026, 3, 2)
    store = _seeded_noise_store(tmp_path, ingest, n_symbols=16)
    symbols = ("spy", *(f"noise{i}" for i in range(16)))

    cmp = compare_metric_screens(
        store,
        symbols=symbols,
        as_of=ingest,
        rule=ByMoneyness(10.0),
        tenor_weeks=4.0,
        years=1.0,
        top_k=3,
    )

    assert cmp.fdr_alpha == pytest.approx(0.05)
    assert cmp.n_comparisons == sum(1 for e in cmp.entries if e.spearman_pvalue is not None)
    assert cmp.n_comparisons > 0  # enough names for every metric to get >=3 usable pairs

    # Internal consistency: significant_raw is exactly the uncorrected hurdle,
    # and BH can only ever be stricter than it (never flags something raw
    # didn't already flag) -- the property `benjamini_hochberg` itself pins,
    # re-checked here through the real wiring rather than the pure function.
    for e in cmp.entries:
        assert e.significant_raw == (
            e.spearman_pvalue is not None and e.spearman_pvalue < cmp.fdr_alpha
        )
        if e.significant_corrected:
            assert e.significant_raw

    # The actual point: on pure noise, the FDR-corrected reading declares no
    # winner at all, even though the uncorrected reading may -- deterministic
    # given the fixed seeds above, not a hand-derived value.
    assert not any(e.significant_corrected for e in cmp.entries)
    assert any(e.significant_raw for e in cmp.entries)


def test_the_bakeoff_reads_its_directions_from_ranking_fragile_high(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bake-off ranks each metric by the one shared table of directions
    (``ranking.FRAGILE_HIGH``), not a private copy: flipping it flips which
    end of vol beta the screen calls fragile, and so its correlation's sign.
    At 5% OOM all four names keep unfloored rolls; at 10% 'calm' is floored
    out and three names leave vol beta's correlation at exactly zero."""
    from tail_lab.research.backtest.ranking import FRAGILE_HIGH

    ingest = dt.date(2026, 3, 2)
    store = _seeded_store(tmp_path, ingest)

    def rhos() -> tuple[float, float]:
        cmp = compare_metric_screens(
            store,
            symbols=("spy", "wild", "calm", "mid"),
            as_of=ingest,
            rule=ByMoneyness(5.0),
            tenor_weeks=4.0,
            years=1.0,
            top_k=2,
        )
        (entry,) = [e for e in cmp.entries if e.metric == "vol_beta"]
        (composite,) = [e for e in cmp.entries if e.metric == "fragility_score"]
        assert entry.spearman_vs_payoff is not None and entry.spearman_vs_payoff != 0.0
        assert composite.spearman_vs_payoff is not None
        return entry.spearman_vs_payoff, composite.spearman_vs_payoff

    before, composite_before = rhos()
    monkeypatch.setitem(FRAGILE_HIGH, "vol_beta", not FRAGILE_HIGH["vol_beta"])
    after, _ = rhos()
    assert after == pytest.approx(-before)
    # ...and the composite -- the row the Workspace's verdict line reads -- is
    # built from the same table: flipping every composite direction reverses
    # its order, so its correlation changes sign.
    from tail_lab.research.backtest.ranking import COMPOSITE_METRICS

    for m in COMPOSITE_METRICS:
        monkeypatch.setitem(FRAGILE_HIGH, m, not FRAGILE_HIGH[m])
    monkeypatch.setitem(
        FRAGILE_HIGH, "vol_beta", not FRAGILE_HIGH["vol_beta"]
    )  # undo the first flip
    _, composite_flipped = rhos()
    assert composite_flipped == pytest.approx(-composite_before)


def test_the_bakeoff_cites_the_snapshots_it_read(tmp_path: Path) -> None:
    ingest = dt.date(2026, 3, 2)
    store = _seeded_store(tmp_path, ingest)
    cmp = compare_metric_screens(
        store,
        symbols=("spy", "wild", "missing"),
        as_of=ingest,
        rule=ByMoneyness(10.0),
        tenor_weeks=4.0,
        years=1.0,
        top_k=2,
    )
    # VIX first, then each SCORED name (not the one with no data).
    assert [s.split("@")[0] for s in cmp.snapshot_ids] == ["vix", "ohlcv_spy", "ohlcv_wild"]


def test_the_bakeoff_cites_its_benchmark_even_when_it_is_not_scored(tmp_path: Path) -> None:
    """The fragility metrics are read against SPY whether or not SPY is one of
    the screened names, so its snapshot is cited either way."""
    ingest = dt.date(2026, 3, 2)
    store = _seeded_store(tmp_path, ingest)
    cmp = compare_metric_screens(
        store,
        symbols=("wild", "calm", "mid"),
        as_of=ingest,
        rule=ByMoneyness(10.0),
        tenor_weeks=4.0,
        years=1.0,
        top_k=2,
    )
    assert "ohlcv_spy" in [s.split("@")[0] for s in cmp.snapshot_ids]


def test_fragility_from_returns_keeps_the_two_pairs_separately_joined() -> None:
    """Vol beta regresses on VIX changes, whose dates differ from SPY's. If the
    two pairs shared one inner join, every benchmark metric would silently
    shrink to the VIX overlap. The wrapper must equal an independent
    computation that joins each pair on its own."""
    from tail_lab.research.backtest.metric_screen import _fragility, fragility_from_returns
    from tail_lab.research.backtest.ranking import _returns
    from tail_lab.research.metrics.vol_beta import vol_beta

    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2024-01-02", periods=300)
    market = pd.Series(rng.normal(0, 0.01, 300), index=idx)
    asset_px = pd.Series(
        100 * np.exp(np.cumsum(1.2 * market + rng.normal(0, 0.005, 300))), index=idx
    )
    bench_ret = market.iloc[1:]
    vix_dates = idx[100::2]  # every other date: a different overlap than SPY's
    vix_chg = pd.Series(rng.normal(0, 0.05, len(vix_dates)), index=vix_dates)

    got = _fragility(asset_px, bench_ret=bench_ret, vol_changes=vix_chg, lookback_days=120)

    a = _returns(asset_px)
    bench = pd.concat({"a": a, "b": bench_ret}, axis=1).dropna().iloc[-120:]
    vol = pd.concat({"a": a, "v": vix_chg}, axis=1).dropna().iloc[-120:]
    expected = {n: fn(bench["a"], bench["b"]) for n, fn in _METRIC_FUNCS.items()}
    expected["vol_beta"] = vol_beta(vol["a"], vol["v"])
    assert got == pytest.approx(expected)
    assert fragility_from_returns(bench, vol) == got
    assert len(bench) == 120 and len(vol) == 100  # the windows are NOT the same rows
    assert not bench.index.equals(vol.index)


def test_fragility_from_returns_reports_an_unestimable_metric_as_none() -> None:
    from tail_lab.research.backtest.metric_screen import fragility_from_returns

    empty_b = pd.DataFrame({"a": [0.0], "b": [0.0]})
    empty_v = pd.DataFrame({"a": [0.0], "v": [0.0]})
    out = fragility_from_returns(empty_b, empty_v)
    assert set(out) == {*_METRIC_FUNCS, "vol_beta"}
    assert all(v is None for v in out.values())
