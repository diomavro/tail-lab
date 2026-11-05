"""Forwards, log-moneyness and Black implied vol on the forward (ADR 0036).

Why these matter: every smile coordinate is ``k = ln(K / F)``, so a wrong
forward shifts the whole surface sideways and flips which quotes count as out
of the money. The parity forward is the model-free-ish one; these pin that it
is exact on European prices, bounded (not zero) on American ones, and refused
-- not silently used -- when the strikes disagree.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tail_lab.research.option_pricer import BlackScholesPricer
from tail_lab.research.vol_surface import coords

S, R, Q, SIGMA = 100.0, 0.043, 0.013, 0.18
PRICER = BlackScholesPricer()


def _euro(strike: float, t: float) -> tuple[float, float]:
    """European (call, put) at the module's constants, call by parity."""
    put = PRICER.price_put(spot=S, strike=strike, t_years=t, r=R, sigma=SIGMA, q=Q)
    call = put + S * math.exp(-Q * t) - strike * math.exp(-R * t)
    return call, put


def _american(strike: float, t: float, *, is_call: bool, steps: int = 400) -> float:
    """Cox-Ross-Rubinstein American price -- the exercise style of SPY and
    every single-name chain the sweep collects."""
    dt_ = t / steps
    up = math.exp(SIGMA * math.sqrt(dt_))
    p = (math.exp((R - Q) * dt_) - 1 / up) / (up - 1 / up)
    disc = math.exp(-R * dt_)
    sign = 1.0 if is_call else -1.0
    values = [max(sign * (S * up ** (2 * j - steps) - strike), 0.0) for j in range(steps + 1)]
    for n in range(steps - 1, -1, -1):
        values = [
            max(
                disc * (p * values[j + 1] + (1 - p) * values[j]),
                sign * (S * up ** (2 * j - n) - strike),
            )
            for j in range(n + 1)
        ]
    return values[0]


def test_parity_recovers_the_forward_exactly_on_european_prices() -> None:
    t = 0.5
    pairs = [(k, *_euro(k, t)) for k in (95.0, 100.0, 105.0)]
    forward, refusal = coords.parity_forward(pairs, t_years=t, r=R, near=S)
    assert refusal is None
    assert forward == pytest.approx(S * math.exp((R - Q) * t), abs=1e-9)


def test_carry_forward_is_spot_grown_at_r_minus_q() -> None:
    assert coords.carry_forward(S, t_years=0.5, r=R, q=Q) == pytest.approx(S * math.exp(0.015))


def test_parity_on_american_prices_is_biased_low_but_within_tolerance() -> None:
    """A European identity on American options: early exercise lifts the put
    more than the call (r > q), so C - P is too small and F reads LOW. The
    tolerance must still accept it, or every real slice would fall to carry."""
    t = 0.5
    pairs = [
        (k, _american(k, t, is_call=True), _american(k, t, is_call=False))
        for k in (98.0, 100.0, 102.0)
    ]
    forward, refusal = coords.parity_forward(pairs, t_years=t, r=R, near=S)
    true = S * math.exp((R - Q) * t)
    assert refusal is None
    assert forward is not None
    bias = forward / true - 1
    assert -coords.parity_tolerance(t) < bias < 0


def test_disagreeing_strikes_fall_back_to_carry_with_the_reason() -> None:
    t = 0.25
    pairs = [(k, *_euro(k, t)) for k in (95.0, 100.0, 105.0)]
    pairs[1] = (100.0, pairs[1][1] + 2.0, pairs[1][2])  # one stale call
    fwd = coords.choose_forward(pairs, spot=S, t_years=t, r=R, q=Q, q_known=True)
    assert fwd.source == "carry"
    assert fwd.parity_refusal is not None and "disagree" in fwd.parity_refusal
    assert fwd.value == pytest.approx(coords.carry_forward(S, t_years=t, r=R, q=Q))


def test_an_unknown_dividend_is_labelled_not_hidden() -> None:
    fwd = coords.choose_forward([], spot=S, t_years=0.25, r=R, q=0.0, q_known=False)
    assert fwd.source == "carry_q0"
    assert fwd.parity_refusal == "only 0 strikes quoted on both sides"


@pytest.mark.parametrize("strike", [80.0, 100.0, 120.0])
def test_black_iv_on_the_forward_round_trips_puts_and_calls(strike: float) -> None:
    t = 0.5
    call, put = _euro(strike, t)
    forward = S * math.exp((R - Q) * t)
    for price, is_call in ((call, True), (put, False)):
        iv = coords.implied_vol(
            price, strike=strike, forward=forward, t_years=t, r=R, is_call=is_call
        )
        assert iv == pytest.approx(SIGMA, abs=1e-6)


def test_an_unreachable_price_is_none_not_a_bound() -> None:
    assert (
        coords.implied_vol(-1.0, strike=100.0, forward=100.0, t_years=0.5, r=R, is_call=False)
        is None
    )


@given(
    forward=st.floats(1.0, 5_000.0),
    lo=st.floats(0.3, 1.0),
    hi=st.floats(1.0, 3.0),
)
def test_log_moneyness_is_zero_at_the_forward_and_increasing(
    forward: float, lo: float, hi: float
) -> None:
    assert coords.log_moneyness(forward, forward) == pytest.approx(0.0, abs=1e-12)
    assert coords.log_moneyness(lo * forward, forward) <= coords.log_moneyness(
        hi * forward, forward
    )
