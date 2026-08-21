"""Tests for the skew decomposition.

The pins here are round-trips and monotonicity: a price inverted to a vol and
priced again must come back, and a steeper skew must produce a bigger measured
underpayment. Both hold regardless of what the real market did, so they keep
holding when the data is re-ingested.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.option_pricer import BlackScholesPricer
from tail_lab.research.skew import (
    IV_MAX,
    compute_skew_measurement,
    implied_vol_put,
    measure_skew,
)

RATE = 0.04
DIV = 0.019
AS_OF = dt.date(2024, 12, 31)


# ------------------------------------------------------------- inversion


@pytest.mark.parametrize("sigma", [0.08, 0.20, 0.45, 1.20])
def test_a_price_inverts_back_to_the_vol_that_made_it(sigma: float) -> None:
    """The round-trip pin: price at a known vol, invert, recover the vol."""
    pricer = BlackScholesPricer()
    price = pricer.price_put(spot=400.0, strike=380.0, t_years=0.08, r=RATE, sigma=sigma, q=DIV)

    recovered = implied_vol_put(price, spot=400.0, strike=380.0, t_years=0.08, r=RATE, q=DIV)

    assert recovered is not None
    assert recovered == pytest.approx(sigma, abs=1e-6)


def test_inversion_works_where_vega_is_nearly_zero() -> None:
    """Deep-OTM strikes are where this measurement matters most and where a
    Newton solver would diverge. Bisection must still land."""
    pricer = BlackScholesPricer()
    price = pricer.price_put(spot=400.0, strike=280.0, t_years=0.08, r=RATE, sigma=0.55, q=DIV)

    recovered = implied_vol_put(price, spot=400.0, strike=280.0, t_years=0.08, r=RATE, q=DIV)

    assert recovered is not None and recovered == pytest.approx(0.55, abs=1e-6)


def test_a_price_below_the_models_floor_is_reported_not_clamped() -> None:
    """Clamping would plant a fabricated vol in the middle of an average."""
    assert implied_vol_put(0.0, spot=400.0, strike=380.0, t_years=0.08, r=RATE, q=DIV) is None


def test_a_price_above_the_models_ceiling_is_reported_not_clamped() -> None:
    ceiling = BlackScholesPricer().price_put(
        spot=400.0, strike=380.0, t_years=0.08, r=RATE, sigma=IV_MAX, q=DIV
    )
    assert (
        implied_vol_put(ceiling * 1.5, spot=400.0, strike=380.0, t_years=0.08, r=RATE, q=DIV)
        is None
    )


# --------------------------------------------------------- the decomposition


def _quotes(*, skew_slope: float, n_dates: int = 24, sigma_atm: float = 0.20) -> pd.DataFrame:
    """A synthetic smile: implied vol rises linearly as the strike falls, at
    ``skew_slope`` vol points per 1% of moneyness."""
    pricer = BlackScholesPricer()
    dates = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=n_dates * 21, freq="C")[::21]
    rows = []
    for stamp in dates:
        spot = 400.0
        expiration = stamp + pd.Timedelta(days=30)
        t = 30 / 365.25
        for otm_pct in range(0, 26):
            strike = spot * (1 - otm_pct / 100)
            sigma = sigma_atm + skew_slope * otm_pct
            price = pricer.price_put(
                spot=spot, strike=strike, t_years=t, r=RATE, sigma=sigma, q=DIV
            )
            rows.append(
                {
                    "underlying": "SPY",
                    "quote_date": stamp,
                    "expiration": expiration,
                    "strike": strike,
                    "bid": price * 0.99,
                    "ask": price * 1.01,
                    "volume": 10,
                    "open_interest": 100,
                    "spot": spot,
                }
            )
    return pd.DataFrame(rows)


def _vix(quotes: pd.DataFrame, level: float = 20.0) -> pd.Series:
    idx = pd.DatetimeIndex(sorted(quotes["quote_date"].unique()))
    return pd.Series(np.full(len(idx), level), index=idx)


def test_a_flat_surface_leaves_nothing_for_skew_to_explain() -> None:
    """The null case. If the market prices every strike at the VIX, a model fed
    the VIX underpays by nothing — and a measurement that still reported a gap
    would be measuring its own arithmetic."""
    quotes = _quotes(skew_slope=0.0)

    summary = measure_skew(quotes, _vix(quotes), moneyness_pct=5.0)

    assert summary.mean_iv_gap == pytest.approx(0.0, abs=1e-6)
    assert summary.annualized_underpayment == pytest.approx(0.0, abs=1e-6)
    assert summary.median_premium_ratio == pytest.approx(1.0, abs=1e-3)


def test_a_steeper_skew_is_measured_as_a_bigger_underpayment() -> None:
    quotes_shallow = _quotes(skew_slope=0.002)
    quotes_steep = _quotes(skew_slope=0.006)

    shallow = measure_skew(quotes_shallow, _vix(quotes_shallow), moneyness_pct=10.0)
    steep = measure_skew(quotes_steep, _vix(quotes_steep), moneyness_pct=10.0)

    assert steep.mean_iv_gap is not None and shallow.mean_iv_gap is not None
    assert steep.mean_iv_gap > shallow.mean_iv_gap
    assert steep.annualized_underpayment > shallow.annualized_underpayment


def test_the_measured_iv_gap_recovers_the_skew_that_was_built_in() -> None:
    """The analytic pin: a 0.004/point slope at 10% OTM is 4 vol points above
    ATM, by construction. The measurement has to say exactly that."""
    quotes = _quotes(skew_slope=0.004)

    summary = measure_skew(quotes, _vix(quotes), moneyness_pct=10.0)

    assert summary.mean_iv_gap == pytest.approx(0.04, abs=1e-4)
    assert summary.mean_model_iv == pytest.approx(0.20, abs=1e-9)
    assert summary.mean_market_iv == pytest.approx(0.24, abs=1e-4)


def test_the_roll_buys_the_strike_nearest_the_target() -> None:
    quotes = _quotes(skew_slope=0.003)
    summary = measure_skew(quotes, _vix(quotes), moneyness_pct=5.0)

    assert summary.n_observations == 24
    for obs in summary.observations:
        assert obs.moneyness == pytest.approx(0.95, abs=0.005)


def test_the_cadence_scales_the_annualized_figure() -> None:
    """The same per-roll underpayment costs three times as much when it is paid
    monthly rather than quarterly."""
    quotes = _quotes(skew_slope=0.004)
    monthly = measure_skew(quotes, _vix(quotes), moneyness_pct=10.0, rolls_per_year=12)
    quarterly = measure_skew(quotes, _vix(quotes), moneyness_pct=10.0, rolls_per_year=4)

    assert monthly.annualized_underpayment == pytest.approx(3.0 * quarterly.annualized_underpayment)


def test_a_roll_date_with_no_vix_yet_is_skipped_not_guessed() -> None:
    quotes = _quotes(skew_slope=0.003)
    late_vix = _vix(quotes).iloc[5:]

    summary = measure_skew(quotes, late_vix, moneyness_pct=5.0)

    assert summary.n_observations == 19  # the first five dates predate the series


def test_no_usable_roll_date_raises_rather_than_returning_an_empty_summary() -> None:
    quotes = _quotes(skew_slope=0.003)
    empty_vix = pd.Series(dtype=float, index=pd.DatetimeIndex([]))

    with pytest.raises(LookupError, match="no roll date"):
        measure_skew(quotes, empty_vix, moneyness_pct=5.0)


# -------------------------------------------------------------- from the lake


def test_compute_reads_the_lake_point_in_time(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    quotes = _quotes(skew_slope=0.004)
    store.write_bronze("option_quotes", AS_OF, quotes)
    vix = _vix(quotes)
    store.write_bronze("vix", AS_OF, pd.DataFrame({"date": vix.index, "close": vix.to_numpy()}))

    summary = compute_skew_measurement(store, as_of=AS_OF, moneyness_pct=10.0)
    assert summary.mean_iv_gap == pytest.approx(0.04, abs=1e-4)

    with pytest.raises(LookupError):
        compute_skew_measurement(store, as_of=AS_OF - dt.timedelta(days=1))


def test_a_lake_without_the_licence_limited_quotes_raises_clearly(tmp_path: Path) -> None:
    """This measurement is allowed to fail — unlike the accuracy panel — and
    the quote snapshot legitimately may not exist."""
    store = DeltaLakeStore(tmp_path)
    quotes = _quotes(skew_slope=0.003)
    store.write_bronze("option_quotes", AS_OF, quotes.assign(underlying="QQQ"))
    vix = _vix(quotes)
    store.write_bronze("vix", AS_OF, pd.DataFrame({"date": vix.index, "close": vix.to_numpy()}))

    with pytest.raises(LookupError, match="no SPY quotes"):
        compute_skew_measurement(store, as_of=AS_OF)
