from __future__ import annotations

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
