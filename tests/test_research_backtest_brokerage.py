"""Tests for the retail brokerage cost model (``research/backtest/brokerage.py``).

The pinned cases are recomputed from the documented formula by hand, and the
magnitude bands assert the calibration matches realistic US retail figures (a
few-percent half-spread on a liquid monthly OOM put, ~10-15% on a 1-week
deep-OOM put), per ``docs/STANDARDS.md`` (pin against an independent
computation, not a snapshot of the function's own output).
"""

from __future__ import annotations

import pytest

from tail_lab.research.backtest.brokerage import (
    COMMISSION_PER_CONTRACT,
    SPREAD_MAX_FRAC,
    half_spread_frac,
    roll_cost,
)


def test_roll_cost_pins_commission_and_spread() -> None:
    """5000 share-equiv (50 real contracts), $1000 notional, 4-week 10%-OOM put.

    commission = 0.65 * (5000/100) = 32.50
    half_spread = 0.010 + 0.020/4 + 0.10*0.10 + 0.30*0.10/4 = 0.0325
    spread_cost = 0.0325 * 1000 = 32.50  -> total 65.00
    """
    cost = roll_cost(contracts=5000.0, notional=1000.0, tenor_weeks=4.0, moneyness_pct=10.0)
    assert cost == pytest.approx(32.5 + 32.5)


def test_commission_scales_with_real_contracts() -> None:
    """Commission is $0.65 per 100-share contract, independent of the spread."""
    only_commission = roll_cost(
        contracts=100.0, notional=1000.0, tenor_weeks=4.0, moneyness_pct=5.0, spread_scale=0.0
    )
    assert only_commission == pytest.approx(COMMISSION_PER_CONTRACT)  # exactly one real contract


def test_half_spread_is_wider_for_shorter_tenor_and_deeper_oom() -> None:
    """Monotone: illiquidity rises as the tenor shrinks and the strike moves
    further out-of-the-money."""
    assert half_spread_frac(1.0, 10.0) > half_spread_frac(4.0, 10.0)  # shorter tenor, wider
    assert half_spread_frac(4.0, 20.0) > half_spread_frac(4.0, 5.0)  # deeper OOM, wider


def test_half_spread_magnitudes_are_realistic() -> None:
    """A liquid ~monthly lightly-OOM put quotes a few-percent half-spread; a
    1-week deep-OOM put is an order of magnitude wider, and the model is
    clamped so it never runs away."""
    liquid = half_spread_frac(4.0, 4.0)
    assert 0.02 <= liquid <= 0.04
    illiquid = half_spread_frac(1.0, 20.0)
    assert 0.10 <= illiquid <= 0.15
    # A pathological short/deep corner is clamped, not unbounded.
    assert half_spread_frac(0.25, 50.0) == pytest.approx(SPREAD_MAX_FRAC)


def test_roll_cost_is_cost_free_when_zeroed() -> None:
    """The test override (commission 0, spread 0) yields exactly zero cost, so a
    backtest can isolate the cost drag."""
    assert (
        roll_cost(
            contracts=5000.0,
            notional=1000.0,
            tenor_weeks=1.0,
            moneyness_pct=20.0,
            commission_per_contract=0.0,
            spread_scale=0.0,
        )
        == 0.0
    )
