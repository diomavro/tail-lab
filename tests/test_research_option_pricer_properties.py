"""Hypothesis property tests for the option pricer (STANDARDS.md "Property
tests for numeric code"). Complements the pinned textbook-value tests in
``test_research_option_pricer.py`` with invariants that hold for *any*
valid input, not just the hand-picked cases.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from scipy.stats import norm

from tail_lab.research.option_pricer import BlackScholesPricer

# Bounds chosen to stay comfortably inside the domain BlackScholesPricer
# documents as valid (spot, strike, t_years, sigma all strictly positive)
# while avoiding regions where BS itself becomes numerically degenerate
# (e.g. sigma*sqrt(T) ~ 0, or spot/strike ratios so extreme that d1/d2
# overflow) -- those are pricer-numerics-robustness concerns, not the
# no-look-ahead/parity properties under test here.
_price = st.floats(min_value=0.01, max_value=1_000.0, allow_nan=False, allow_infinity=False)
_t_years = st.floats(min_value=1 / 365, max_value=5.0, allow_nan=False, allow_infinity=False)
_rate = st.floats(min_value=-0.05, max_value=0.20, allow_nan=False, allow_infinity=False)
_sigma = st.floats(min_value=0.02, max_value=3.0, allow_nan=False, allow_infinity=False)


def _bs_call(*, spot: float, strike: float, t_years: float, r: float, sigma: float) -> float:
    """Independent (not calling BlackScholesPricer) closed-form BS call,
    used only to check put-call parity against the production put price."""
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * t_years) / (sigma * math.sqrt(t_years))
    d2 = d1 - sigma * math.sqrt(t_years)
    return float(spot * norm.cdf(d1) - strike * math.exp(-r * t_years) * norm.cdf(d2))


@given(spot=_price, strike=_price, t_years=_t_years, r=_rate, sigma=_sigma)
def test_put_price_is_never_negative(
    spot: float, strike: float, t_years: float, r: float, sigma: float
) -> None:
    put = BlackScholesPricer().price_put(
        spot=spot, strike=strike, t_years=t_years, r=r, sigma=sigma
    )
    assert put >= -1e-9  # tiny float slack; must not be meaningfully negative


@given(spot=_price, strike=_price, t_years=_t_years, r=_rate, sigma=_sigma)
def test_put_price_never_exceeds_discounted_strike(
    spot: float, strike: float, t_years: float, r: float, sigma: float
) -> None:
    put = BlackScholesPricer().price_put(
        spot=spot, strike=strike, t_years=t_years, r=r, sigma=sigma
    )
    discounted_strike = strike * math.exp(-r * t_years)
    assert put <= discounted_strike + 1e-6 * max(1.0, discounted_strike)


@given(spot=_price, strike=_price, t_years=_t_years, r=_rate, sigma=_sigma)
def test_put_call_parity_holds(
    spot: float, strike: float, t_years: float, r: float, sigma: float
) -> None:
    """C - P == S - K*e^{-rT} for any valid (S, K, T, r, sigma) -- a
    model-free identity independent of BS specifically, so checking it
    against an independently-implemented call formula pins the put
    pricer's correctness across the whole input space, not just one case."""
    put = BlackScholesPricer().price_put(
        spot=spot, strike=strike, t_years=t_years, r=r, sigma=sigma
    )
    call = _bs_call(spot=spot, strike=strike, t_years=t_years, r=r, sigma=sigma)

    lhs = call - put
    rhs = spot - strike * math.exp(-r * t_years)
    # Relative + absolute tolerance: values range from cents to hundreds.
    assert lhs == pytest.approx(rhs, rel=1e-6, abs=1e-6)
