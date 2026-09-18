"""Pinned tests for the Paretan kernel (``docs/STANDARDS.md``: every number a
human reads is checked against an *independently derivable* answer, never a
snapshot of the function's own output).

Three independent references are used, deliberately:

* **mpmath quadrature of the defining integral.** ``_reference_put_price``
  integrates ``E[(K - S)^+]`` under the truncated Pareto density directly, with
  no closed form anywhere in it. ``scipy.integrate.quad`` is NOT usable here:
  on this integrand (``r^(-alpha-1)`` over a dynamic range reaching ~1e27) it
  returns 2.24e-11 against a true 7.33e-11 at ``alpha=8`` near the domain edge
  -- a 128% error in the *reference*, failing silently. A test built on it
  would condemn correct code.
* **Hand arithmetic**, worked in the docstrings below so a reader can check the
  pin without running anything.
* **The boundary condition ``P(0) = 0``**, which is what distinguishes the
  derived formula from the one printed in the paper (``docs/adr/0026``).
"""

from __future__ import annotations

import math

import pytest
from mpmath import mp

from tail_lab.research.surface.paretan import (
    LAMBDA_GUARD_MAX_SIGMA_ROOT_T,
    MIN_ALPHA,
    ParetanTail,
    anchor_l_call,
    anchor_l_call_returns,
    anchor_l_put,
    call_ratio,
    call_ratio_returns,
    heuristic_is_valid,
    put_ratio,
)


def _reference_put_price(*, strike: float, spot: float, karamata_l: float, alpha: float) -> float:
    """``E[(K - S)^+]`` by arbitrary-precision quadrature, S = (1-r)S0,
    r ~ Pareto(l, alpha) truncated to [l, 1]. No closed form used."""
    if strike > (1.0 - karamata_l) * spot:
        raise AssertionError(
            f"strike {strike} is outside the Paretan domain for l={karamata_l}, spot={spot}; "
            "the reference must refuse rather than silently integrate a different quantity"
        )
    mp.dps = 50
    k, s0, ell, a = (mp.mpf(x) for x in (strike, spot, karamata_l, alpha))
    lam = 1 / (1 - ell**a)
    lower = 1 - k / s0
    integrand = lambda r: (k - (1 - r) * s0) * lam * a * ell**a * r ** (-a - 1)  # noqa: E731
    return float(mp.quad(integrand, [lower, 1]))


# --------------------------------------------------------------------------
# put_price against the defining integral
# --------------------------------------------------------------------------


@pytest.mark.parametrize("alpha", [1.05, 2.0, 3.0, 8.0])
@pytest.mark.parametrize("karamata_l", [1e-4, 0.05, 0.5])
@pytest.mark.parametrize("spot", [1.0, 100.0, 4567.89])
@pytest.mark.parametrize("depth", [0.999, 0.9, 0.5, 0.1])
def test_put_price_matches_the_defining_integral(
    alpha: float, karamata_l: float, spot: float, depth: float
) -> None:
    """The closed form is a *derivation*; quadrature of the integral it was
    derived from is the independent check. They must agree to the precision of
    the float arithmetic, not merely approximately."""
    tail = ParetanTail(alpha=alpha, karamata_l=karamata_l, basis="returns")
    strike = depth * tail.deepest_valid_put_strike(spot=spot)
    got = tail.put_price(strike=strike, spot=spot)
    want = _reference_put_price(strike=strike, spot=spot, karamata_l=karamata_l, alpha=alpha)
    assert got == pytest.approx(want, rel=1e-9)


@pytest.mark.parametrize("alpha", [1.05, 2.0, 2.75, 3.0, 3.85, 8.0])
@pytest.mark.parametrize("spot", [0.5, 100.0, 4567.89])
def test_zero_strike_put_is_worthless(alpha: float, spot: float) -> None:
    """A put struck at zero can never pay, so it is worth nothing.

    This is the test that discriminates the derived ``S_0^(-alpha)`` form from
    the ``S_0^(1-alpha)`` printed in the paper: the derived one gives
    ``S_0^alpha * S_0^(1-alpha) - S_0 = 0``, the printed one gives -99.99 at
    ``S_0=100, alpha=3``.

    It is asserted with a tolerance, not as ``== 0.0``. The shape is a
    difference of two quantities that agree in exact arithmetic but not in
    IEEE754: swept over 525 ``(alpha, S_0, l)`` combinations, 140 return a
    non-zero residual (~1e-14 relative to the price scale). Only alpha=2 is
    clean everywhere. Demanding exact equality would fail on correct code.
    """
    tail = ParetanTail(alpha=alpha, karamata_l=0.05, basis="returns")
    scale = tail.lambda_correction() * 0.05**alpha * spot / (alpha - 1.0)
    assert tail.put_price(strike=0.0, spot=spot) == pytest.approx(0.0, abs=1e-9 * max(scale, 1e-12))


def test_put_price_hand_arithmetic() -> None:
    """S0=100, alpha=3, l=0.05, K=80, worked by hand:

    ``g = 100^3 * 20^-2 - 2*80 - 100 = 2500 - 160 - 100 = 2240``
    ``lambda = 1/(1 - 0.05^3) = 1/0.999875``
    ``P = lambda * 1.25e-4 * 2240 / 2 = 0.14 / 0.999875 = 0.1400175021877735``
    """
    tail = ParetanTail(alpha=3.0, karamata_l=0.05, basis="returns")
    assert tail.put_price(strike=80.0, spot=100.0) == pytest.approx(0.1400175021877735, rel=1e-12)


def test_put_ratio_eliminates_every_parameter_but_alpha() -> None:
    """``g(80)=2240``, ``g(90)=10000-180-100=9720``, ratio ``2240/9720``.

    Neither ``l`` nor ``lambda`` appears, which is the entire point: one market
    quote plus a tail index prices the deeper strike.
    """
    assert put_ratio(k_from=90.0, k_to=80.0, spot=100.0, alpha=3.0) == pytest.approx(
        2240.0 / 9720.0, rel=1e-14
    )
    assert pytest.approx(0.23045267489711935, rel=1e-14) == 2240.0 / 9720.0


def test_dropping_the_truncation_term_is_an_eight_percent_error() -> None:
    """The naive ``(S0-K)^(1-alpha)`` ratio ignores ``-(alpha-1)K - S0``.

    ``(20/10)^-2 = 0.25`` against the correct ``0.23045...`` -- 8.5% of the
    right answer. ``lambda`` is negligible; this term is not (assumption 4).
    """
    naive = (20.0 / 10.0) ** (1.0 - 3.0)
    correct = put_ratio(k_from=90.0, k_to=80.0, spot=100.0, alpha=3.0)
    assert naive == pytest.approx(0.25, rel=1e-14)
    assert abs(naive - correct) / correct == pytest.approx(0.0848, abs=5e-4)


@pytest.mark.parametrize("karamata_l", [1e-4, 0.01, 0.05])
def test_put_ratio_agrees_with_the_quadrature_reference(karamata_l: float) -> None:
    """The ratio is parameter-free, so it must equal the ratio of two
    independently integrated prices at any ``l``."""
    ratio = put_ratio(k_from=90.0, k_to=80.0, spot=100.0, alpha=3.0)
    ref_from = _reference_put_price(strike=90.0, spot=100.0, karamata_l=karamata_l, alpha=3.0)
    ref_to = _reference_put_price(strike=80.0, spot=100.0, karamata_l=karamata_l, alpha=3.0)
    assert ratio == pytest.approx(ref_to / ref_from, rel=1e-9)


# --------------------------------------------------------------------------
# calls
# --------------------------------------------------------------------------


def test_call_ratio_is_the_power_law() -> None:
    """``(K2/K1)^(1-alpha)``: at alpha=3, doubling the strike quarters the
    price -- ``2^-2 = 0.25``, exactly."""
    assert call_ratio(k_from=100.0, k_to=200.0, alpha=3.0) == 0.25


def test_call_prices_are_log_log_linear_with_slope_one_minus_alpha() -> None:
    """The paper's central picture: on log-log axes Paretan option prices lie
    on a straight line of slope ``1 - alpha`` while Black-Scholes prices fall
    off a cliff. Checked by least-squares slope over a 20-rung ladder.
    """
    alpha = 2.75
    tail = anchor_l_call(price=1.0, strike=100.0, alpha=alpha)
    strikes = [100.0 * 1.05**i for i in range(20)]
    xs = [math.log(k) for k in strikes]
    ys = [math.log(tail.call_price(strike=k)) for k in strikes]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sum(
        (x - mean_x) ** 2 for x in xs
    )
    assert slope == pytest.approx(1.0 - alpha, abs=1e-12)


def test_call_ratio_returns_basis() -> None:
    """``((K2-S0)/(K1-S0))^(1-alpha)``: S0=100, K1=110, K2=120, alpha=3
    gives ``(20/10)^-2 = 0.25``."""
    assert call_ratio_returns(k_from=110.0, k_to=120.0, spot=100.0, alpha=3.0) == pytest.approx(
        0.25, rel=1e-14
    )


# --------------------------------------------------------------------------
# anchor calibration
# --------------------------------------------------------------------------


def test_anchor_l_call_matches_the_papers_inversion() -> None:
    """``l = ((alpha-1) C_m K^(alpha-1))^(1/alpha)``; at alpha=2, C_m=1, K=100
    that is ``(1 * 1 * 100)^(1/2) = 10`` exactly."""
    tail = anchor_l_call(price=1.0, strike=100.0, alpha=2.0)
    assert tail.karamata_l == pytest.approx(10.0, rel=1e-14)
    assert tail.basis == "price"


@pytest.mark.parametrize("alpha", [1.05, 2.0, 2.75, 5.0, 8.0])
@pytest.mark.parametrize("karamata_l", [1e-4, 0.01, 0.2, 0.5])
def test_anchor_l_put_round_trips(alpha: float, karamata_l: float) -> None:
    """Price a put from ``(alpha, l)``, then recover ``l`` from that price.

    ``lambda`` depends on ``l``, so the inversion looks implicit; writing
    ``u = P(alpha-1)/g`` gives ``l^alpha = u/(1+u)`` in closed form. The
    round-trip is what proves that algebra.
    """
    spot = 100.0
    tail = ParetanTail(alpha=alpha, karamata_l=karamata_l, basis="returns")
    strike = 0.5 * tail.deepest_valid_put_strike(spot=spot)
    price = tail.put_price(strike=strike, spot=spot)
    recovered = anchor_l_put(price=price, strike=strike, spot=spot, alpha=alpha)
    assert recovered.karamata_l == pytest.approx(karamata_l, rel=1e-9)


def test_anchor_l_call_returns_round_trips() -> None:
    tail = ParetanTail(alpha=2.75, karamata_l=0.08, basis="returns")
    strike, spot = 130.0, 100.0
    price = tail.call_price(strike=strike, spot=spot)
    recovered = anchor_l_call_returns(price=price, strike=strike, spot=spot, alpha=2.75)
    assert recovered.karamata_l == pytest.approx(0.08, rel=1e-12)


def test_anchor_inside_the_karamata_point_is_refused() -> None:
    """An anchor that is not itself beyond the Karamata constant invalidates
    everything extrapolated from it, so it is refused rather than priced."""
    with pytest.raises(ValueError, match="inside the calibrated Karamata point"):
        anchor_l_put(price=40.0, strike=95.0, spot=100.0, alpha=3.0)


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------


def test_lambda_correction_hand_value() -> None:
    """``1/(1 - 0.5^2) = 1/0.75 = 4/3`` exactly."""
    tail = ParetanTail(alpha=2.0, karamata_l=0.5, basis="returns")
    assert tail.lambda_correction() == pytest.approx(4.0 / 3.0, rel=1e-15)


def test_heuristic_validity_boundary() -> None:
    """``sigma*sqrt(t) <= 1/2``. At sigma=0.2 that is t = 6.25 years exactly."""
    assert heuristic_is_valid(sigma=0.2, t_years=6.25)
    assert not heuristic_is_valid(sigma=0.2, t_years=6.26)
    assert 0.2 * math.sqrt(6.25) == LAMBDA_GUARD_MAX_SIGMA_ROOT_T


@pytest.mark.parametrize("alpha", [0.9, 1.0])
def test_alpha_at_or_below_one_is_refused(alpha: float) -> None:
    """Only a finite *first* moment is required -- but it IS required.
    Finite variance is not (assumption 1)."""
    with pytest.raises(ValueError, match="finite first moment"):
        ParetanTail(alpha=alpha, karamata_l=0.05, basis="returns")
    assert MIN_ALPHA == 1.0
    ParetanTail(alpha=1.0001, karamata_l=0.05, basis="returns")


def test_price_basis_and_returns_basis_cannot_be_mixed() -> None:
    """A price-basis ``l`` is in price units. Feeding one to the put path
    would give ``(1 - 10) * 100 = -900`` as a strike bound, so it raises."""
    price_basis = anchor_l_call(price=1.0, strike=100.0, alpha=2.0)
    assert price_basis.karamata_l == pytest.approx(10.0)
    with pytest.raises(ValueError, match="price-basis"):
        price_basis.put_price(strike=50.0, spot=100.0)
    with pytest.raises(ValueError, match="price-basis"):
        price_basis.deepest_valid_put_strike(spot=100.0)


def test_strike_inside_the_karamata_point_is_refused() -> None:
    tail = ParetanTail(alpha=3.0, karamata_l=0.05, basis="returns")
    assert tail.deepest_valid_put_strike(spot=100.0) == pytest.approx(95.0)
    with pytest.raises(ValueError, match="outside the Paretan put domain"):
        tail.put_price(strike=96.0, spot=100.0)


# --------------------------------------------------------------------------
# Every guard clause. These are the safety mechanism -- an unreachable-looking
# refusal that silently does not fire is how a nonsense price reaches a
# surface, so each one is exercised.
# --------------------------------------------------------------------------


_RETURNS_TAIL = ParetanTail(alpha=3.0, karamata_l=0.05, basis="returns")
_PRICE_TAIL = ParetanTail(alpha=3.0, karamata_l=10.0, basis="price")


def test_karamata_l_must_be_positive() -> None:
    with pytest.raises(ValueError, match="karamata_l must be positive"):
        ParetanTail(alpha=3.0, karamata_l=0.0, basis="returns")


def test_basis_must_be_one_of_the_two() -> None:
    with pytest.raises(ValueError, match="basis must be"):
        ParetanTail(alpha=3.0, karamata_l=0.05, basis="log")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "call",
    [
        lambda: _RETURNS_TAIL.put_price(strike=10.0, spot=0.0),
        lambda: _RETURNS_TAIL.deepest_valid_put_strike(spot=-1.0),
        lambda: _RETURNS_TAIL.call_price(strike=200.0, spot=0.0),
    ],
)
def test_non_positive_spot_is_refused(call: object) -> None:
    with pytest.raises(ValueError, match="spot must be positive"):
        call()  # type: ignore[operator]


def test_call_price_guards() -> None:
    with pytest.raises(ValueError, match="strike must be positive"):
        _RETURNS_TAIL.call_price(strike=0.0, spot=100.0)
    with pytest.raises(ValueError, match="spot is not used"):
        _PRICE_TAIL.call_price(strike=200.0, spot=100.0)
    with pytest.raises(ValueError, match="needs spot"):
        _RETURNS_TAIL.call_price(strike=200.0)
    with pytest.raises(ValueError, match="inside the Karamata point"):
        _RETURNS_TAIL.call_price(strike=101.0, spot=100.0)


def test_price_basis_cannot_produce_a_lambda_correction() -> None:
    with pytest.raises(ValueError, match="price-basis"):
        _PRICE_TAIL.lambda_correction()


def test_put_ratio_guards() -> None:
    with pytest.raises(ValueError, match="alpha must exceed"):
        put_ratio(k_from=90.0, k_to=80.0, spot=100.0, alpha=1.0)
    with pytest.raises(ValueError, match="spot must be positive"):
        put_ratio(k_from=90.0, k_to=80.0, spot=0.0, alpha=3.0)
    with pytest.raises(ValueError, match="must lie in"):
        put_ratio(k_from=110.0, k_to=80.0, spot=100.0, alpha=3.0)


def test_put_ratio_refuses_a_worthless_anchor() -> None:
    """At ``K`` where the model assigns no value there is nothing to scale
    from, so the ratio refuses rather than dividing by ~zero."""
    with pytest.raises(ValueError, match="carries no Paretan value"):
        put_ratio(k_from=0.0, k_to=10.0, spot=100.0, alpha=3.0)


def test_call_ratio_guards() -> None:
    with pytest.raises(ValueError, match="alpha must exceed"):
        call_ratio(k_from=100.0, k_to=200.0, alpha=1.0)
    with pytest.raises(ValueError, match="strikes must be positive"):
        call_ratio(k_from=0.0, k_to=200.0, alpha=3.0)
    with pytest.raises(ValueError, match="alpha must exceed"):
        call_ratio_returns(k_from=110.0, k_to=120.0, spot=100.0, alpha=1.0)
    with pytest.raises(ValueError, match="must exceed spot"):
        call_ratio_returns(k_from=90.0, k_to=120.0, spot=100.0, alpha=3.0)


def test_anchor_guards() -> None:
    with pytest.raises(ValueError, match="alpha must exceed"):
        anchor_l_call(price=1.0, strike=100.0, alpha=1.0)
    with pytest.raises(ValueError, match="must be positive"):
        anchor_l_call(price=0.0, strike=100.0, alpha=3.0)
    with pytest.raises(ValueError, match="alpha must exceed"):
        anchor_l_call_returns(price=1.0, strike=120.0, spot=100.0, alpha=1.0)
    with pytest.raises(ValueError, match="price must be positive"):
        anchor_l_call_returns(price=0.0, strike=120.0, spot=100.0, alpha=3.0)
    with pytest.raises(ValueError, match="must exceed spot"):
        anchor_l_call_returns(price=1.0, strike=90.0, spot=100.0, alpha=3.0)
    with pytest.raises(ValueError, match="alpha must exceed"):
        anchor_l_put(price=1.0, strike=80.0, spot=100.0, alpha=1.0)
    with pytest.raises(ValueError, match="price must be positive"):
        anchor_l_put(price=0.0, strike=80.0, spot=100.0, alpha=3.0)
    with pytest.raises(ValueError, match="must lie in"):
        anchor_l_put(price=1.0, strike=110.0, spot=100.0, alpha=3.0)
    with pytest.raises(ValueError, match="carries no Paretan value"):
        anchor_l_put(price=1.0, strike=0.0, spot=100.0, alpha=3.0)


def test_heuristic_validity_rejects_negative_inputs() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        heuristic_is_valid(sigma=-0.1, t_years=1.0)
