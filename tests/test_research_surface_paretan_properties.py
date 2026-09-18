"""Hypothesis property tests for the Paretan kernel (``docs/STANDARDS.md``,
"Property tests for numeric code") -- invariants that must hold across the
whole valid input space, not only at the hand-picked pins in
``test_research_surface_paretan.py``.

The most important one here is **butterfly convexity**. The paper invokes
Breeden-Litzenberger: the second derivative of the option price with respect to
strike is the implied density, so a generated strike ladder is arbitrage-free
exactly when it is convex. Stating it as a property rather than as a formula is
deliberate -- an earlier draft of the plan carried an analytic second
derivative that was wrong by ``(alpha-1)/S_0^alpha`` while the property itself
was fine.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tail_lab.research.surface.paretan import (
    ParetanTail,
    anchor_l_put,
    call_ratio,
    put_ratio,
)

# Bounds stay strictly inside the documented domain: alpha above 1 (finite first
# moment) and l small enough that a put ladder has room beneath the anchor.
_alpha = st.floats(min_value=1.01, max_value=8.0, allow_nan=False, allow_infinity=False)
_l = st.floats(min_value=1e-4, max_value=0.5, allow_nan=False, allow_infinity=False)
_spot = st.floats(min_value=1.0, max_value=5_000.0, allow_nan=False, allow_infinity=False)
_depth = st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False)


@given(alpha=_alpha, karamata_l=_l, spot=_spot, depth=_depth)
def test_put_price_is_finite_non_negative_and_below_the_strike(
    alpha: float, karamata_l: float, spot: float, depth: float
) -> None:
    """A put is never worth less than nothing nor more than its strike.

    Also the standing guard against the ``(S_0 - K)`` sign convention: a
    negative base raised to a non-integer power yields a **complex** number from
    the float operator and **nan** from numpy, both silently. Every result here
    must be a plain finite float.
    """
    tail = ParetanTail(alpha=alpha, karamata_l=karamata_l, basis="returns")
    strike = depth * tail.deepest_valid_put_strike(spot=spot)
    price = tail.put_price(strike=strike, spot=spot)
    assert isinstance(price, float)
    assert math.isfinite(price)
    assert 0.0 <= price <= strike + 1e-12


@given(alpha=_alpha, karamata_l=_l, spot=_spot, depth=_depth)
def test_put_ladder_is_convex_so_the_implied_density_is_non_negative(
    alpha: float, karamata_l: float, spot: float, depth: float
) -> None:
    """Breeden-Litzenberger: ``P(K1) + P(K3) >= 2 P(K2)`` for equally spaced
    strikes. A violation would be a butterfly arbitrage inside our own
    generated ladder."""
    tail = ParetanTail(alpha=alpha, karamata_l=karamata_l, basis="returns")
    deepest = tail.deepest_valid_put_strike(spot=spot)
    middle = depth * deepest
    step = min(middle, deepest - middle) * 0.5
    assume(step > 1e-9)
    low = tail.put_price(strike=middle - step, spot=spot)
    mid = tail.put_price(strike=middle, spot=spot)
    high = tail.put_price(strike=middle + step, spot=spot)
    assert low + high >= 2.0 * mid - 1e-9 * max(1.0, mid)


@given(alpha=_alpha, karamata_l=_l, spot=_spot, depth=_depth)
def test_put_price_increases_with_strike(
    alpha: float, karamata_l: float, spot: float, depth: float
) -> None:
    """Deeper out of the money is cheaper -- monotone in strike over the whole
    valid domain."""
    tail = ParetanTail(alpha=alpha, karamata_l=karamata_l, basis="returns")
    deepest = tail.deepest_valid_put_strike(spot=spot)
    lower = depth * deepest
    upper = min(deepest, lower + 0.25 * deepest)
    assume(upper - lower > 1e-9)
    assert tail.put_price(strike=upper, spot=spot) >= tail.put_price(strike=lower, spot=spot)


@given(alpha=_alpha, karamata_l=_l, spot=_spot, depth=_depth)
@settings(max_examples=200)
def test_anchor_calibration_round_trips(
    alpha: float, karamata_l: float, spot: float, depth: float
) -> None:
    """Price a put from ``(alpha, l)``, invert it, recover ``l``.

    This is what proves ``l^alpha = u/(1+u)`` -- the closed form that makes the
    apparently implicit ``lambda`` dependence go away.
    """
    tail = ParetanTail(alpha=alpha, karamata_l=karamata_l, basis="returns")
    strike = depth * tail.deepest_valid_put_strike(spot=spot)
    price = tail.put_price(strike=strike, spot=spot)
    assume(price > 1e-12 * spot)
    recovered = anchor_l_put(price=price, strike=strike, spot=spot, alpha=alpha)
    assert recovered.karamata_l == pytest.approx(karamata_l, rel=1e-6)


@given(
    alpha=_alpha,
    spot=_spot,
    d1=st.floats(min_value=0.05, max_value=0.4),
    d2=st.floats(min_value=0.05, max_value=0.4),
)
def test_relative_pricing_composes(alpha: float, spot: float, d1: float, d2: float) -> None:
    """``ratio(K1->K3) == ratio(K1->K2) * ratio(K2->K3)``.

    This is what "relative to an anchor" means formally: the map from strike to
    price is defined only up to a scale, so composing two hops must equal the
    single hop. It is the algebraic form of the paper's own scope limit.
    """
    k1 = spot * (1.0 - d1 * 0.5)
    k2 = spot * (1.0 - d1)
    k3 = spot * (1.0 - d1 - d2 * 0.5)
    assume(k3 > 0.01 * spot)
    direct = put_ratio(k_from=k1, k_to=k3, spot=spot, alpha=alpha)
    composed = put_ratio(k_from=k1, k_to=k2, spot=spot, alpha=alpha) * put_ratio(
        k_from=k2, k_to=k3, spot=spot, alpha=alpha
    )
    assert direct == pytest.approx(composed, rel=1e-9)


@given(
    alpha=_alpha,
    k1=st.floats(min_value=1.0, max_value=1_000.0),
    factor=st.floats(min_value=1.01, max_value=10.0),
)
def test_call_ratio_is_scale_free(alpha: float, k1: float, factor: float) -> None:
    """The tail index is scale-free: scaling both strikes leaves the ratio
    unchanged. This is the half of the paper's log-return remark that *does*
    transfer -- invariance under multiplication by a constant. It says nothing
    about invariance under temporal aggregation, which does not hold.
    """
    plain = call_ratio(k_from=k1, k_to=k1 * factor, alpha=alpha)
    scaled = call_ratio(k_from=10.0 * k1, k_to=10.0 * k1 * factor, alpha=alpha)
    assert plain == pytest.approx(scaled, rel=1e-12)
