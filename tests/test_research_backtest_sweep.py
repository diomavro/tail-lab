"""Tests for the strike x tenor sweep and its argmax.

The argmax is what the fragility ranking reports and what a click on a row
lands the user on, so a wrong or unstable one shows up directly on the screen
as a number that does not match the backtest it opens.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.backtest.put_roll import trailing_realized_vol
from tail_lab.research.backtest.sweep import (
    SWEEP_MONEYNESS,
    SWEEP_TENORS_WEEKS,
    SweepPoint,
    best_point,
    run_sweep,
)

AS_OF = dt.date(2024, 12, 31)


def _series(n: int = 1200, seed: int = 7) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    closes = np.clip(100 + np.cumsum(rng.normal(0, 1.0, size=n)), 20, None)
    prices = pd.Series(closes, index=pd.date_range(end=AS_OF, periods=n, freq="B"))
    return prices, trailing_realized_vol(prices)


def _point(m: float, t: float, annualized: float) -> SweepPoint:
    return SweepPoint(
        moneyness_pct=m,
        tenor_weeks=t,
        roi_on_premium=annualized,
        annualized_return=annualized,
        n_cycles=10,
    )


def test_the_sweep_covers_the_whole_grid() -> None:
    prices, iv = _series()
    points = run_sweep(prices, iv, asset="spy", as_of=AS_OF, notional=1000.0, years=4.0)

    assert len(points) == len(SWEEP_MONEYNESS) * len(SWEEP_TENORS_WEEKS)
    assert {p.moneyness_pct for p in points} == set(SWEEP_MONEYNESS)
    assert {p.tenor_weeks for p in points} == set(SWEEP_TENORS_WEEKS)


def test_a_tenor_too_long_for_the_window_drops_its_cell_not_the_grid() -> None:
    """A short history should degrade to a smaller heatmap, never an error —
    otherwise one recently-listed name breaks the whole ranking."""
    # 70 bars: after the 20-bar IV warm-up there is no room for a 12-week
    # (60 trading day) roll to complete, but the short tenors still fit.
    prices, iv = _series(n=70)

    points = run_sweep(prices, iv, asset="spy", as_of=AS_OF, notional=1000.0, years=0.25)

    assert points  # the short tenors still score
    assert max(p.tenor_weeks for p in points) < max(SWEEP_TENORS_WEEKS)


def test_skipping_the_per_day_curves_does_not_change_the_verdict() -> None:
    """`include_curves=False` is a speed path, not a different backtest. If it
    ever changed a score, the ranking and the heatmap would disagree."""
    from tail_lab.research.backtest.put_roll import run_put_roll

    prices, iv = _series()
    common = dict(
        asset="spy",
        as_of=AS_OF,
        notional=1000.0,
        moneyness_pct=8.0,
        tenor_weeks=4.0,
        lookback_years=4.0,
    )
    full = run_put_roll(prices, iv, **common, include_curves=True)
    lean = run_put_roll(prices, iv, **common, include_curves=False)

    assert lean.roi_on_premium == full.roi_on_premium
    assert lean.n_cycles == full.n_cycles
    assert lean.hit_rate == full.hit_rate
    assert [c.net for c in lean.cycles] == [c.net for c in full.cycles]
    # ...and it really did skip the expensive per-day outputs.
    assert lean.mtm_curve == [] and lean.price_path == []
    assert full.mtm_curve and full.price_path


def test_best_point_picks_the_highest_annualized_return() -> None:
    points = [_point(4, 2, 0.10), _point(12, 8, 0.31), _point(8, 4, 0.22)]
    best = best_point(points)
    assert best is not None
    assert (best.moneyness_pct, best.tenor_weeks) == (12, 8)


def test_ties_break_toward_the_cheaper_shorter_contract() -> None:
    """Two cells that earned the same return are not equally good: the
    shallower, shorter one is cheaper and more liquid. An arbitrary tie-break
    would also make the ranking flicker between reloads."""
    points = [_point(18, 12, 0.25), _point(6, 2, 0.25), _point(6, 8, 0.25)]

    best = best_point(points)

    assert best is not None
    assert (best.moneyness_pct, best.tenor_weeks) == (6, 2)


def test_the_tie_break_is_deterministic_regardless_of_input_order() -> None:
    tied = [_point(18, 12, 0.25), _point(6, 2, 0.25), _point(6, 8, 0.25)]
    picks = {
        (best_point(order).moneyness_pct, best_point(order).tenor_weeks)  # type: ignore[union-attr]
        for order in (tied, list(reversed(tied)), [tied[1], tied[2], tied[0]])
    }
    assert picks == {(6.0, 2.0)}


def test_an_empty_grid_has_no_best_cell() -> None:
    assert best_point([]) is None


def test_the_best_cell_of_a_real_sweep_is_actually_in_the_sweep() -> None:
    prices, iv = _series()
    points = run_sweep(prices, iv, asset="spy", as_of=AS_OF, notional=1000.0, years=4.0)

    best = best_point(points)

    assert best is not None
    assert best in points
    assert best.annualized_return == pytest.approx(max(p.annualized_return for p in points))
