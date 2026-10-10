"""The strike rule: the delta inversion must land exactly on its target, under
the one convention the platform uses, and refuse what has no answer.

Why it matters: a strike picked "by delta" that misses its delta is a different
option from the one every verdict and page claims it is.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tail_lab.research.backtest.strike_rule import (
    MAX_TARGET_DELTA,
    MIN_TARGET_DELTA,
    ByDelta,
    ByMoneyness,
    put_delta,
)
from tail_lab.research.option_pricer import BlackScholesPricer


def test_moneyness_is_a_fixed_distance_below_spot() -> None:
    assert ByMoneyness(10.0).strike(spot=200.0, sigma=0.2, t_years=0.1, r=0.04, q=0.01) == 180.0


@settings(max_examples=300, deadline=None)
@given(
    target=st.floats(MIN_TARGET_DELTA, MAX_TARGET_DELTA),
    sigma=st.floats(0.06, 1.5),
    t_years=st.floats(5 / 252, 1.0),
    q=st.floats(0.0, 0.08),
    r=st.floats(0.0, 0.06),
)
def test_the_strike_round_trips_through_the_pricers_own_delta(
    target: float, sigma: float, t_years: float, q: float, r: float
) -> None:
    spot = 100.0
    strike = ByDelta(target).strike(spot=spot, sigma=sigma, t_years=t_years, r=r, q=q)
    greeks = BlackScholesPricer().greeks_put(
        spot=spot, strike=strike, t_years=t_years, r=r, sigma=sigma, q=q
    )
    assert greeks.delta == pytest.approx(-target, abs=1e-9)
    assert put_delta(spot=spot, strike=strike, sigma=sigma, t_years=t_years, r=r, q=q) == (
        pytest.approx(-target, abs=1e-12)
    )


def test_a_pinned_case_matches_a_hand_computation() -> None:
    # d1 = -N^-1(0.10 * e^{0.012*20/252}) and K from the closed form, by hand.
    t = 20 / 252
    scaled = 0.10 * math.exp(0.012 * t)
    from statistics import NormalDist

    d1 = -NormalDist().inv_cdf(scaled)
    k = 600.0 * math.exp(-d1 * 0.12 * math.sqrt(t) + (0.04 - 0.012 + 0.5 * 0.12**2) * t)
    assert ByDelta(0.10).strike(
        spot=600.0, sigma=0.12, t_years=t, r=0.04, q=0.012
    ) == pytest.approx(k)
    assert k == pytest.approx(576.18, abs=0.01)  # 4.0% below spot at 12% vol


def test_realised_vol_strikes_sit_closer_to_spot_than_the_markets() -> None:
    # The disclosed gap: at 8% realised vol a 0.10-delta 21-day put is only
    # ~2.6% below spot; at a skewed ~22% implied vol it is ~7% below.
    t = 21 / 252
    calm = ByDelta(0.10).strike(spot=100.0, sigma=0.08, t_years=t, r=0.04, q=0.0)
    market = ByDelta(0.10).strike(spot=100.0, sigma=0.22, t_years=t, r=0.04, q=0.0)
    assert 1 - calm / 100 == pytest.approx(0.0257, abs=0.0005)
    assert 1 - market / 100 > 0.06


def test_a_target_with_no_strike_is_refused_not_clipped() -> None:
    with pytest.raises(ValueError, match="not below 1"):
        ByDelta(0.50).strike(spot=100.0, sigma=0.3, t_years=30.0, r=0.0, q=0.04)


@pytest.mark.parametrize("bad", [0.0, 0.005, 0.6, -0.1])
def test_targets_outside_the_tail_hedge_range_are_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="target delta"):
        ByDelta(bad)


@pytest.mark.parametrize("bad", [0.0, 100.0, -5.0])
def test_moneyness_outside_zero_to_hundred_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="moneyness"):
        ByMoneyness(bad)
