"""Hypothesis property tests for the premium-sizing seam (STANDARDS.md
"Property tests for numeric code"). Complements the pinned known-answer cases
in ``test_research_backtest_sizing.py`` with invariants that hold for *any*
valid input, not just the hand-picked ones.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tail_lab.research.backtest.sizing import FixedPremium, WealthFraction

_positive = st.floats(min_value=1e-3, max_value=1e9, allow_nan=False, allow_infinity=False)
_alpha = st.floats(min_value=1e-6, max_value=1.0, allow_nan=False, allow_infinity=False)
_n_legs = st.integers(min_value=1, max_value=1000)


@given(amount=_positive, n_legs=_n_legs)
def test_fixed_premium_resolve_is_always_the_amount(amount: float, n_legs: int) -> None:
    assert FixedPremium(amount).resolve(n_legs=n_legs) == amount


@given(alpha=_alpha, wealth=_positive, n_legs=_n_legs)
def test_wealth_fraction_resolve_is_never_more_than_alpha_times_wealth(
    alpha: float, wealth: float, n_legs: int
) -> None:
    resolved = WealthFraction(alpha=alpha, wealth=wealth).resolve(n_legs=n_legs)
    ceiling = alpha * wealth
    assert 0.0 < resolved <= ceiling * (1.0 + 1e-9) + 1e-9


@given(alpha=_alpha, wealth=_positive, n_legs=_n_legs)
def test_wealth_fraction_resolve_is_linear_in_wealth(
    alpha: float, wealth: float, n_legs: int
) -> None:
    """WealthFraction(alpha, c*wealth).resolve(n) == c * WealthFraction(alpha,
    wealth).resolve(n) -- a direct consequence of resolve being
    alpha * wealth / n_legs."""
    base = WealthFraction(alpha=alpha, wealth=wealth).resolve(n_legs=n_legs)
    doubled = WealthFraction(alpha=alpha, wealth=2.0 * wealth).resolve(n_legs=n_legs)
    assert doubled == pytest.approx(2.0 * base)


@given(alpha=_alpha, wealth=_positive, n_legs=st.integers(min_value=1, max_value=100))
def test_wealth_fraction_resolve_decreases_as_n_legs_grows(
    alpha: float, wealth: float, n_legs: int
) -> None:
    """More legs sharing the same wealth fraction means a smaller slice each,
    monotonically -- resolve(n) > resolve(n+1) for the same alpha/wealth."""
    mode = WealthFraction(alpha=alpha, wealth=wealth)
    assert mode.resolve(n_legs=n_legs) > mode.resolve(n_legs=n_legs + 1)
