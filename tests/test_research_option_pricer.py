from __future__ import annotations

import math

import pytest

from tail_lab.research.option_pricer import BlackScholesPricer


def test_black_scholes_put_matches_textbook_value() -> None:
    """Hull, Options Futures and Other Derivatives: S=42, K=40, r=10%,
    sigma=20%, T=0.5y -> call ~= 4.76, put ~= 0.81 (put-call parity)."""
    pricer = BlackScholesPricer()
    put = pricer.price_put(spot=42.0, strike=40.0, t_years=0.5, r=0.10, sigma=0.20)
    assert put == pytest.approx(0.8085993729000922, abs=1e-9)
    assert put == pytest.approx(0.81, abs=5e-3)


def test_black_scholes_deep_itm_put_approaches_intrinsic_discounted() -> None:
    """A deep in-the-money put with tiny vol should price close to the
    discounted intrinsic value K*e^{-rT} - S."""
    pricer = BlackScholesPricer()
    put = pricer.price_put(spot=50.0, strike=100.0, t_years=0.01, r=0.0, sigma=0.01)
    assert put == pytest.approx(50.0, abs=0.5)


def test_black_scholes_rejects_nonpositive_inputs() -> None:
    pricer = BlackScholesPricer()
    with pytest.raises(ValueError):
        pricer.price_put(spot=0.0, strike=40.0, t_years=0.5, r=0.1, sigma=0.2)
    with pytest.raises(ValueError):
        pricer.price_put(spot=42.0, strike=40.0, t_years=0.5, r=0.1, sigma=0.0)


# ---- greeks -----------------------------------------------------------------
#
# The validation discipline here is borrowed from `docs/PRIOR_ART.md` §10: an
# analytic greek is checked against a finite difference of the price it claims
# to differentiate, against an analytic identity that must hold regardless of
# parameters, and at the boundaries where the formula is most likely to be
# wrong. A closed form that agrees with its own numerical derivative across the
# input space is hard to get wrong by accident.

_GREEK_CASES = [
    # (spot, strike, t_years, r, sigma, q) -- ATM, OOM, deep OOM, long-dated,
    # dividend-paying, high-vol.
    (100.0, 100.0, 0.25, 0.04, 0.20, 0.0),
    (100.0, 90.0, 0.0769, 0.04, 0.20, 0.0),
    (100.0, 70.0, 0.25, 0.04, 0.45, 0.0),
    (100.0, 110.0, 2.0, 0.03, 0.15, 0.0),
    (100.0, 95.0, 0.5, 0.05, 0.25, 0.02),
    (100.0, 60.0, 0.08, 0.04, 0.90, 0.0),
]


def _price(
    pricer: BlackScholesPricer, s: float, k: float, t: float, r: float, v: float, q: float
) -> float:
    return pricer.price_put(spot=s, strike=k, t_years=t, r=r, sigma=v, q=q)


@pytest.mark.parametrize(("s", "k", "t", "r", "v", "q"), _GREEK_CASES)
def test_greeks_match_finite_differences_of_the_price(
    s: float, k: float, t: float, r: float, v: float, q: float
) -> None:
    """Every analytic greek against a central difference of `price_put`.

    This is the load-bearing test: it ties the closed form to the function it
    is supposed to be the derivative of, so a transcription error in any term
    shows up here rather than in a backtest six months later.
    """
    p = BlackScholesPricer()
    g = p.greeks_put(spot=s, strike=k, t_years=t, r=r, sigma=v, q=q)

    hs = s * 1e-5
    fd_delta = (_price(p, s + hs, k, t, r, v, q) - _price(p, s - hs, k, t, r, v, q)) / (2 * hs)
    fd_gamma = (
        _price(p, s + hs, k, t, r, v, q)
        - 2 * _price(p, s, k, t, r, v, q)
        + _price(p, s - hs, k, t, r, v, q)
    ) / (hs * hs)
    hv = 1e-6
    fd_vega = (_price(p, s, k, t, r, v + hv, q) - _price(p, s, k, t, r, v - hv, q)) / (2 * hv)
    ht = 1e-6
    # Theta is decay with the passage of time, i.e. -d(price)/d(t_years).
    fd_theta = -(_price(p, s, k, t + ht, r, v, q) - _price(p, s, k, t - ht, r, v, q)) / (2 * ht)
    hr = 1e-6
    fd_rho = (_price(p, s, k, t, r + hr, v, q) - _price(p, s, k, t, r - hr, v, q)) / (2 * hr)

    assert g.delta == pytest.approx(fd_delta, rel=1e-5, abs=1e-8)
    assert g.gamma == pytest.approx(fd_gamma, rel=1e-3, abs=1e-8)
    # Scaled units: vega per vol POINT, theta per DAY, rho per rate point.
    assert g.vega == pytest.approx(fd_vega * 0.01, rel=1e-5, abs=1e-8)
    assert g.theta == pytest.approx(fd_theta / 365.25, rel=1e-4, abs=1e-8)
    assert g.rho == pytest.approx(fd_rho * 0.01, rel=1e-5, abs=1e-8)


@pytest.mark.parametrize(("s", "k", "t", "r", "v", "q"), _GREEK_CASES)
def test_put_call_delta_parity_holds(
    s: float, k: float, t: float, r: float, v: float, q: float
) -> None:
    """delta_call - delta_put = e^{-qT}, differentiating put-call parity.

    An identity the formula must satisfy for *any* parameters, so it catches
    the class of error a single hand-computed case cannot: a wrong sign or a
    missing discount factor that happens to be invisible at q=0.
    """
    p = BlackScholesPricer()
    g = p.greeks_put(spot=s, strike=k, t_years=t, r=r, sigma=v, q=q)
    # Call price via parity, then its delta by finite difference.
    hs = s * 1e-5

    def call(spot: float) -> float:
        put = _price(p, spot, k, t, r, v, q)
        return put + spot * math.exp(-q * t) - k * math.exp(-r * t)

    delta_call = (call(s + hs) - call(s - hs)) / (2 * hs)
    assert delta_call - g.delta == pytest.approx(math.exp(-q * t), rel=1e-6)


@pytest.mark.parametrize(("s", "k", "t", "r", "v", "q"), _GREEK_CASES)
def test_greek_signs_and_bounds_are_respected(
    s: float, k: float, t: float, r: float, v: float, q: float
) -> None:
    """A long put is short the underlying, long convexity and long vol; and a
    probability is a probability."""
    g = BlackScholesPricer().greeks_put(spot=s, strike=k, t_years=t, r=r, sigma=v, q=q)
    assert -1.0 <= g.delta <= 0.0
    assert g.gamma > 0.0
    assert g.vega > 0.0
    assert g.rho < 0.0
    assert 0.0 <= g.itm_prob <= 1.0


def test_itm_prob_is_the_risk_neutral_exercise_probability() -> None:
    """N(-d2), not N(-d1). The two are close for short-dated ATM options and
    diverge exactly where this platform operates -- deep OOM, high vol -- so
    confusing them would be invisible in a smoke test and wrong in the wing."""
    p = BlackScholesPricer()
    s, k, t, r, v = 100.0, 70.0, 0.25, 0.04, 0.45
    g = p.greeks_put(spot=s, strike=k, t_years=t, r=r, sigma=v)
    vol_t = v * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * v * v) * t) / vol_t
    d2 = d1 - vol_t
    assert g.itm_prob == pytest.approx(0.5 * math.erfc(d2 / math.sqrt(2.0)), abs=1e-12)
    assert g.itm_prob != pytest.approx(abs(g.delta), abs=1e-3)


def test_itm_prob_falls_as_the_strike_moves_further_out() -> None:
    p = BlackScholesPricer()
    probs = [
        p.greeks_put(spot=100.0, strike=k, t_years=0.25, r=0.04, sigma=0.30).itm_prob
        for k in (95.0, 90.0, 80.0, 70.0, 60.0)
    ]
    assert probs == sorted(probs, reverse=True)


def test_vega_is_per_volatility_point_not_per_unit_sigma() -> None:
    """The convention that makes the number legible (`docs/PRIOR_ART.md` §2).
    A 1-point vol move on a 3-month ATM 100-strike put is worth cents, not
    tens of dollars; getting this wrong by 100x is the kind of error that
    survives review because nobody checks units."""
    p = BlackScholesPricer()
    g = p.greeks_put(spot=100.0, strike=100.0, t_years=0.25, r=0.04, sigma=0.20)
    repriced = p.price_put(
        spot=100.0, strike=100.0, t_years=0.25, r=0.04, sigma=0.21
    ) - p.price_put(spot=100.0, strike=100.0, t_years=0.25, r=0.04, sigma=0.20)
    assert g.vega == pytest.approx(repriced, rel=2e-3)
    assert 0.0 < g.vega < 1.0


def test_theta_is_per_calendar_day() -> None:
    """One day of decay, not one year of it."""
    p = BlackScholesPricer()
    kwargs = {"spot": 100.0, "strike": 95.0, "t_years": 0.25, "r": 0.04, "sigma": 0.25}
    g = p.greeks_put(**kwargs)
    one_day = 1.0 / 365.25
    decay = p.price_put(**{**kwargs, "t_years": 0.25 - one_day}) - p.price_put(**kwargs)
    assert g.theta == pytest.approx(decay, rel=5e-3)


def test_greeks_reject_the_same_inputs_the_price_rejects() -> None:
    p = BlackScholesPricer()
    with pytest.raises(ValueError):
        p.greeks_put(spot=0.0, strike=40.0, t_years=0.5, r=0.1, sigma=0.2)
    with pytest.raises(ValueError):
        p.greeks_put(spot=42.0, strike=40.0, t_years=0.5, r=0.1, sigma=0.0)
