"""Pinned tests for the smile slope at a put anchor."""

from __future__ import annotations

import numpy as np
import pytest

from tail_lab.research.surface.smile import select_strikes, smile_slope

SPY_GRID = [570.0, 575.0, 580.0, 585.0, 590.0, 594.0, 595.0, 596.0, 597.0, 598.0, 599.0, 600.0]


def quadratic_iv(k: float, anchor: float = 594.0) -> float:
    x = k - anchor
    return 0.25 - 0.001 * x + 0.00002 * x * x


def test_recovers_the_exact_slope_of_a_quadratic_smile() -> None:
    result = smile_slope(SPY_GRID, [quadratic_iv(k) for k in SPY_GRID], 594.0)
    assert result.refusal is None
    assert result.slope == pytest.approx(-0.001, abs=1e-12)
    assert result.std_error == pytest.approx(0.0, abs=1e-12)


def test_selection_matches_the_spec_example_on_a_spy_grid() -> None:
    # 3 strictly below and 3 strictly above the anchor are guaranteed, the rest
    # filled by distance: 580, 585, 590, 594, 595..599.
    assert select_strikes(SPY_GRID, 594.0) == [
        580.0,
        585.0,
        590.0,
        594.0,
        595.0,
        596.0,
        597.0,
        598.0,
        599.0,
    ]


def test_each_side_keeps_its_minimum_even_when_one_side_is_nearer() -> None:
    chosen = select_strikes(
        [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 90.0, 80.0, 70.0], 100.0
    )
    assert chosen is not None
    assert sum(k < 100.0 for k in chosen) >= 3
    assert sum(k > 100.0 for k in chosen) >= 3


def test_one_sided_chain_is_refused_not_zero() -> None:
    strikes = [590.0, 592.0, 594.0, 596.0, 598.0, 600.0]
    result = smile_slope(strikes, [0.25] * 6, 599.0)
    assert result.slope is None and result.refusal is not None
    assert result.strikes == ()


def test_too_few_usable_rows_is_refused() -> None:
    # 3 + 3 around the anchor but the anchor's own row is absent: 6 < MIN_ROWS.
    strikes = [591.0, 592.0, 593.0, 595.0, 596.0, 597.0]
    result = smile_slope(strikes, [quadratic_iv(k) for k in strikes], 594.0)
    assert result.slope is None
    assert "only 6 usable rows" in (result.refusal or "")


def test_null_and_zero_ivs_are_dropped_before_selection() -> None:
    ivs: list[float | None] = [quadratic_iv(k) for k in SPY_GRID]
    ivs[3] = None  # 585
    ivs[4] = 0.0  # 590
    result = smile_slope(SPY_GRID, ivs, 594.0)
    assert result.refusal is None
    assert 585.0 not in result.strikes and 590.0 not in result.strikes
    assert result.slope == pytest.approx(-0.001, abs=1e-12)


def test_noise_shows_up_in_the_standard_error() -> None:
    rng = np.random.default_rng(7)
    ivs = [quadratic_iv(k) + rng.normal(0.0, 0.002) for k in SPY_GRID]
    result = smile_slope(SPY_GRID, ivs, 594.0)
    assert result.std_error is not None and result.std_error > 0.0
    assert result.slope == pytest.approx(-0.001, abs=5 * result.std_error)
