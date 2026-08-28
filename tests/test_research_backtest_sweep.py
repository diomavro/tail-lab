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
    MODEL_PRICED_MAX_MONEYNESS_PCT,
    SWEEP_MONEYNESS,
    SWEEP_TENORS_WEEKS,
    SweepPoint,
    best_point,
    is_model_priced,
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
    points = [_point(4, 2, 0.10), _point(10, 8, 0.31), _point(8, 4, 0.22)]
    best = best_point(points)
    assert best is not None
    assert (best.moneyness_pct, best.tenor_weeks) == (10, 8)


def test_the_grid_reaches_deeper_than_the_model_can_price() -> None:
    """The heatmap deliberately shows strikes the pricer cannot handle, so the
    reader can see the whole surface. It is the *argmax* that must not wander
    into them, not the display."""
    assert max(SWEEP_MONEYNESS) > MODEL_PRICED_MAX_MONEYNESS_PCT
    assert any(not is_model_priced(m) for m in SWEEP_MONEYNESS)
    assert is_model_priced(MODEL_PRICED_MAX_MONEYNESS_PCT)
    assert not is_model_priced(MODEL_PRICED_MAX_MONEYNESS_PCT + 0.1)


def test_best_point_ignores_cells_the_model_cannot_price() -> None:
    """A flat-vol model prices a 22%-OOM put at essentially nothing (measured
    median market/model premium ratio 21,663x, docs/MODEL_RESIDUAL.md), so a
    fixed premium budget buys an absurd number of contracts and any payoff is
    inflated by the same factor. Those cells win the raw argmax almost by
    construction; they must not become a name's headline."""
    points = [_point(6, 4, 0.18), _point(22, 4, 4.90), _point(30, 2, 12.0)]

    best = best_point(points)

    assert best is not None
    assert (best.moneyness_pct, best.tenor_weeks) == (6, 4)


def test_best_point_can_still_be_asked_for_the_unbounded_argmax() -> None:
    """The sweep endpoint still reports the true grid maximum for display; only
    the ranking headline is bounded."""
    points = [_point(6, 4, 0.18), _point(22, 4, 4.90)]

    best = best_point(points, priced_only=False)

    assert best is not None
    assert best.moneyness_pct == 22


def test_best_point_is_none_when_no_cell_is_model_priced() -> None:
    """Better no headline than a fictional one."""
    assert best_point([_point(22, 4, 4.90), _point(30, 2, 12.0)]) is None


def test_ties_break_toward_the_cheaper_shorter_contract() -> None:
    """Two cells that earned the same return are not equally good: the
    shallower, shorter one is cheaper and more liquid. An arbitrary tie-break
    would also make the ranking flicker between reloads."""
    points = [_point(10, 12, 0.25), _point(6, 2, 0.25), _point(6, 8, 0.25)]

    best = best_point(points)

    assert best is not None
    assert (best.moneyness_pct, best.tenor_weeks) == (6, 2)


def test_the_tie_break_is_deterministic_regardless_of_input_order() -> None:
    tied = [_point(10, 12, 0.25), _point(6, 2, 0.25), _point(6, 8, 0.25)]
    picks = {
        (best_point(order).moneyness_pct, best_point(order).tenor_weeks)  # type: ignore[union-attr]
        for order in (tied, list(reversed(tied)), [tied[1], tied[2], tied[0]])
    }
    assert picks == {(6.0, 2.0)}


def test_an_empty_grid_has_no_best_cell() -> None:
    assert best_point([]) is None


def test_the_best_cell_of_a_real_sweep_is_actually_in_the_sweep() -> None:
    """The headline is a cell of this grid — not a number computed some other
    way — because a click on it opens the backtest at exactly those params."""
    prices, iv = _series()
    points = run_sweep(prices, iv, asset="spy", as_of=AS_OF, notional=1000.0, years=4.0)

    best = best_point(points)

    assert best is not None
    assert best in points
    priced = [p.annualized_return for p in points if is_model_priced(p.moneyness_pct)]
    assert best.annualized_return == pytest.approx(max(priced))


def test_the_headline_never_advertises_a_strike_the_model_cannot_price() -> None:
    """Over a real grid, not just hand-built points: whatever the deep cells
    do, the reported best stays inside the priced band."""
    prices, iv = _series()
    points = run_sweep(prices, iv, asset="spy", as_of=AS_OF, notional=1000.0, years=4.0)

    best = best_point(points)

    assert best is not None
    assert is_model_priced(best.moneyness_pct)
    # And the grid really did contain deeper cells it could have picked.
    assert any(not is_model_priced(p.moneyness_pct) for p in points)


def test_the_priced_grid_is_the_full_grid_minus_what_cannot_be_priced() -> None:
    """The ranking's argmax can never land outside the priced band, so sweeping
    the deep cells for it is pure waste — and at 70 names it is most of the
    ranking's cost. The two grids stay derived from one another rather than
    hand-maintained, or they drift the first time a strike is added."""
    from tail_lab.research.backtest.sweep import MODEL_PRICED_SWEEP_MONEYNESS

    assert tuple(m for m in SWEEP_MONEYNESS if is_model_priced(m)) == MODEL_PRICED_SWEEP_MONEYNESS
    assert MODEL_PRICED_SWEEP_MONEYNESS
    assert len(MODEL_PRICED_SWEEP_MONEYNESS) < len(SWEEP_MONEYNESS)
