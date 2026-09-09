"""Tests for the premium-sizing seam (``research/backtest/sizing.py``).

Pinned against hand-computable values, mirroring
``test_research_option_pricer.py``'s "pin against an independent computation"
discipline (``docs/STANDARDS.md``) for the ``OptionPricer`` seam this one is
built to match.
"""

from __future__ import annotations

import pytest

from tail_lab.research.backtest.sizing import FixedPremium, WealthFraction


def test_fixed_premium_returns_the_same_amount_regardless_of_n_legs() -> None:
    mode = FixedPremium(1000.0)
    assert mode.resolve(n_legs=1) == 1000.0
    assert mode.resolve(n_legs=1) == mode.resolve(n_legs=50)


def test_fixed_premium_rejects_nonpositive_amount() -> None:
    with pytest.raises(ValueError, match="amount"):
        FixedPremium(0.0)
    with pytest.raises(ValueError, match="amount"):
        FixedPremium(-100.0)


def test_wealth_fraction_splits_alpha_times_wealth_evenly() -> None:
    # 10% of $1,000,000 split across 5 legs -> $20,000 each.
    mode = WealthFraction(alpha=0.10, wealth=1_000_000.0)
    assert mode.resolve(n_legs=5) == pytest.approx(20_000.0)
    assert mode.resolve(n_legs=1) == pytest.approx(100_000.0)


def test_wealth_fraction_at_full_alpha_and_one_leg_returns_all_wealth() -> None:
    mode = WealthFraction(alpha=1.0, wealth=50_000.0)
    assert mode.resolve(n_legs=1) == pytest.approx(50_000.0)


def test_wealth_fraction_rejects_alpha_outside_zero_one() -> None:
    with pytest.raises(ValueError, match="alpha"):
        WealthFraction(alpha=0.0, wealth=1000.0)
    with pytest.raises(ValueError, match="alpha"):
        WealthFraction(alpha=1.5, wealth=1000.0)
    with pytest.raises(ValueError, match="alpha"):
        WealthFraction(alpha=-0.1, wealth=1000.0)


def test_wealth_fraction_rejects_nonpositive_wealth() -> None:
    with pytest.raises(ValueError, match="wealth"):
        WealthFraction(alpha=0.1, wealth=0.0)
    with pytest.raises(ValueError, match="wealth"):
        WealthFraction(alpha=0.1, wealth=-1.0)


@pytest.mark.parametrize("mode", [FixedPremium(1000.0), WealthFraction(alpha=0.2, wealth=5000.0)])
def test_resolve_rejects_nonpositive_n_legs(mode: FixedPremium | WealthFraction) -> None:
    with pytest.raises(ValueError, match="n_legs"):
        mode.resolve(n_legs=0)
    with pytest.raises(ValueError, match="n_legs"):
        mode.resolve(n_legs=-3)


def test_sizing_modes_are_frozen() -> None:
    fixed = FixedPremium(1000.0)
    with pytest.raises(AttributeError):
        fixed.amount = 2000.0  # type: ignore[misc]
    fraction = WealthFraction(alpha=0.1, wealth=1000.0)
    with pytest.raises(AttributeError):
        fraction.alpha = 0.2  # type: ignore[misc]
