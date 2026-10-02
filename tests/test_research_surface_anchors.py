"""Pinned tests for the three-anchor implied reading and its dispersion."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tail_lab.research.option_pricer import BlackScholesPricer
from tail_lab.research.surface.anchors import read_anchors
from tail_lab.research.surface.paretan import ParetanTail

QUOTE = dt.date(2026, 9, 25)
EXPIRY = dt.date(2026, 10, 25)
SPOT = 1000.0
R, Q = 0.04, 0.019
STRIKES = [float(k) for k in range(945, 600, -5)]


def chain(price_of, ivs=lambda k: 0.25, strikes=STRIKES) -> pd.DataFrame:
    rows = []
    for k in strikes:
        p = price_of(k)
        rows.append(
            {
                "strike": k,
                "bid": p * 0.98,
                "ask": p * 1.02,
                "quote_date": pd.Timestamp(QUOTE),
                "expiration": pd.Timestamp(EXPIRY),
                "iv": ivs(k),
            }
        )
    return pd.DataFrame(rows)


def power_law(alpha: float):
    tail = ParetanTail(alpha=alpha, karamata_l=0.05, basis="returns")
    return lambda k: tail.put_price(strike=k, spot=SPOT)


def read(quotes: pd.DataFrame, m: float = 7.0):
    return read_anchors(quotes, underlying="SPY", spot=SPOT, moneyness_pct=m, r=R, q=Q)


def test_a_true_power_law_has_zero_dispersion_at_three_distinct_anchors() -> None:
    result = read(chain(power_law(3.0)))
    assert [x.strike for x in result.readings] == [930.0, 910.0, 890.0]
    assert result.dispersion is not None and result.dispersion < 1e-6
    for reading in result.readings:
        assert reading.fit is not None and reading.fit.alpha == pytest.approx(3.0, abs=1e-4)


def test_flat_vol_black_scholes_shows_dispersion() -> None:
    bs = BlackScholesPricer()

    def price(k: float) -> float:
        return bs.price_put(spot=SPOT, strike=k, t_years=90 / 365, sigma=0.35, r=R, q=Q)

    result = read(chain(price))
    assert result.dispersion is not None and result.dispersion > 0.5


def test_coincident_anchors_collapse_and_report_no_dispersion() -> None:
    coarse = [float(k) for k in range(950, 600, -50)]  # 950, 900, 850 ...
    result = read(chain(power_law(3.0), strikes=coarse), m=5.0)
    assert len({x.strike for x in result.readings}) == len(result.readings)
    result = read_anchors(
        chain(power_law(3.0), strikes=[950.0]),
        underlying="SPY",
        spot=SPOT,
        moneyness_pct=5.0,
        r=R,
        q=Q,
    )
    assert len(result.readings) == 1
    assert result.dispersion is None
    assert "fewer than two distinct" in (result.dispersion_reason or "")


def test_a_zero_bid_anchor_is_a_refused_reading_not_an_exception() -> None:
    quotes = chain(power_law(3.0))
    quotes.loc[quotes["strike"] == 930.0, "bid"] = 0.0
    result = read(quotes)
    refused = next(x for x in result.readings if x.strike == 930.0)
    assert refused.fit is None and refused.refusal is not None
    assert refused.ceiling is None and refused.smile is None
    assert result.dispersion is not None  # the other two still agree


def test_no_iv_means_no_ceiling_and_a_reason_never_zero() -> None:
    result = read(chain(power_law(3.0)).drop(columns="iv"))
    for reading in result.readings:
        assert reading.ceiling is None
        assert reading.smile is not None and reading.smile.slope is None
        assert reading.ceiling_reason == reading.smile.refusal


def test_with_a_smile_the_ceiling_is_built_from_the_fitted_slope() -> None:
    result = read(chain(power_law(3.0), ivs=lambda k: 0.25 - 0.0005 * (k - 930.0)))
    first = result.readings[0]
    assert first.smile is not None and first.smile.slope == pytest.approx(-0.0005, abs=1e-9)
    assert first.ceiling is not None and first.ceiling_reason is None


def test_two_refused_fits_give_no_dispersion() -> None:
    quotes = chain(power_law(3.0), strikes=[float(k) for k in range(945, 900, -5)])
    result = read(quotes)
    assert result.dispersion is None
    assert result.dispersion_reason == "fewer than two anchors accepted"
