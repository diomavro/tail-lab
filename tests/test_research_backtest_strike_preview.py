"""The strike preview: both sides must land on the target under the ONE delta
convention, and every way the market side can be missing must say why.

Why it matters: the page puts these two strikes side by side so a reader sees
that "0.10 delta at realised vol" is not the market's 10-delta put. A market
strike computed on the vendor's delta, a stale chain shown as today's, or a
silent blank would each undo that.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.backtest.put_roll import trailing_realized_vol
from tail_lab.research.backtest.strike_preview import StrikePreview, preview_strikes
from tail_lab.research.backtest.strike_rule import put_delta

SESSION = dt.date(2026, 10, 9)


def _prices(end: dt.date = SESSION, n: int = 60) -> tuple[pd.Series, pd.Series]:
    idx = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(3)
    prices = pd.Series(500 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx)
    return prices, trailing_realized_vol(prices)


def _chain(iv: float | None = 0.22, days: int = 30, session: dt.date = SESSION) -> pd.DataFrame:
    rows = [
        {
            "underlying": "SPY",
            "quote_date": pd.Timestamp(session),
            "expiration": pd.Timestamp(session + dt.timedelta(days=days)),
            "strike": float(k),
            "spot": 500.0,
            "iv": iv,
            # A vendor delta that is deliberately wrong: it must never be read.
            "delta": -0.5,
        }
        for k in range(400, 500, 1)
    ]
    return pd.DataFrame(rows)


def _preview(
    chain: pd.DataFrame,
    *,
    series: tuple[pd.Series, pd.Series] | None = None,
    asset: str = "spy",
    tenor_weeks: float = 4.0,
) -> StrikePreview:
    prices, rv = series if series is not None else _prices()
    return preview_strikes(
        chain,
        prices,
        rv,
        asset=asset,
        as_of=SESSION,
        target_delta=0.10,
        tenor_weeks=tenor_weeks,
        r=0.04,
        q=0.012,
        q_source="measured",
    )


def test_the_market_strike_is_our_delta_on_the_listed_iv_not_the_vendors() -> None:
    p = _preview(_chain())
    m = p.market
    assert p.market_status == "quoted"
    t = 30 / 365
    assert m.delta == pytest.approx(
        put_delta(spot=500.0, strike=m.strike, sigma=0.22, t_years=t, r=0.04, q=0.012)
    )
    # Nearest listed strike to -0.10: its neighbours are no closer.
    for k in (m.strike - 1, m.strike + 1):
        other = put_delta(spot=500.0, strike=k, sigma=0.22, t_years=t, r=0.04, q=0.012)
        assert abs(other + 0.10) >= abs(m.delta + 0.10)
    assert m.moneyness_pct == pytest.approx((1 - m.strike / 500) * 100)


def test_the_model_strike_uses_the_latest_realised_vol() -> None:
    prices, rv = _prices()
    p = _preview(_chain(), series=(prices, rv))
    model = p.model
    assert model.vol_date == SESSION and model.sigma == pytest.approx(rv.iloc[-1])
    assert model.t_years == pytest.approx(20 / 252)
    d = put_delta(
        spot=model.spot,
        strike=model.strike,
        sigma=model.sigma,
        t_years=model.t_years,
        r=0.04,
        q=0.012,
    )
    assert d == pytest.approx(-0.10, abs=1e-9)


def test_an_asset_outside_the_chain_is_not_collected() -> None:
    p = _preview(_chain(), asset="hyg")
    assert (p.market, p.market_status) == (None, "not_collected")
    assert _preview(pd.DataFrame()).market_status == "not_collected"


def test_no_expiry_long_enough_is_named() -> None:
    p = _preview(_chain(days=10), tenor_weeks=4.0)
    assert (p.market, p.market_status) == (None, "no_expiry")


def test_zero_filled_iv_is_never_priced() -> None:
    p = _preview(_chain(iv=None))
    assert (p.market, p.market_status) == (None, "no_iv")


def test_an_older_chain_is_flagged_not_passed_off_as_today() -> None:
    p = _preview(_chain(session=SESSION - dt.timedelta(days=3)))
    assert p.market_is_older is True
    assert _preview(_chain()).market_is_older is False


def test_no_realised_vol_yet_means_no_model_strike() -> None:
    prices, rv = _prices(n=10)
    assert _preview(_chain(), series=(prices, rv)).model is None


def test_the_expiry_is_the_nearest_listed_on_or_after_the_tenor() -> None:
    # 30 and 60 days listed, tenor 4 weeks (28 calendar days): the 30-day one.
    # Rounding past it to a later expiry would preview a different, dearer put.
    chain = pd.concat([_chain(days=60), _chain(days=30)])
    assert _preview(chain).market.expiration == SESSION + dt.timedelta(days=30)  # type: ignore[union-attr]


def test_the_tenor_is_counted_in_calendar_days_from_the_chain_session() -> None:
    # 4 weeks = 28 calendar days. An expiry 25 days out is too short; counting
    # the tenor in trading days (20) would wrongly accept it.
    assert _preview(_chain(days=25)).market_status == "no_expiry"
    assert _preview(_chain(days=27)).market_status == "no_expiry"
    assert _preview(_chain(days=28)).market_status == "quoted"


def test_the_latest_session_in_the_chain_is_the_one_previewed() -> None:
    old = _chain(iv=0.60, session=SESSION - dt.timedelta(days=1))
    p = _preview(pd.concat([old, _chain(iv=0.22)]))
    assert p.market is not None and p.market.session == SESSION and p.market.iv == 0.22


def test_a_chain_newer_than_the_prices_is_not_flagged_older() -> None:
    p = _preview(_chain(session=SESSION + dt.timedelta(days=1)))
    assert p.market_is_older is False


def test_the_model_vol_is_clamped_exactly_as_the_backtest_clamps_it() -> None:
    # A dead-calm series has realised vol near zero; the backtest prices at the
    # 6% floor, so the preview of "its" strike must too.
    from tail_lab.research.backtest.put_roll import REALIZED_VOL_FLOOR

    idx = pd.bdate_range(end=SESSION, periods=60)
    prices = pd.Series(500.0 * (1 + 1e-6 * np.arange(60)), index=idx)
    p = _preview(_chain(), series=(prices, trailing_realized_vol(prices)))
    assert p.model is not None and p.model.sigma == REALIZED_VOL_FLOOR


def test_a_fractional_tenor_rounds_its_calendar_days_up() -> None:
    # 2.5 weeks = 17.5 calendar days: the first expiry ON OR AFTER that is day
    # 18; rounding down would admit a 17-day option, shorter than asked.
    assert _preview(_chain(days=17), tenor_weeks=2.5).market_status == "no_expiry"
    assert _preview(_chain(days=18), tenor_weeks=2.5).market_status == "quoted"


def test_a_fractional_tenor_is_rounded_up_not_to_nearest() -> None:
    # 2.2 weeks = 15.4 days: rounding to nearest would admit a 15-day option.
    assert _preview(_chain(days=15), tenor_weeks=2.2).market_status == "no_expiry"
    assert _preview(_chain(days=16), tenor_weeks=2.2).market_status == "quoted"
