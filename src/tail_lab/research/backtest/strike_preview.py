"""Today's strike for a delta target, two ways, side by side (`docs/adr/0029`).

The served backtests pick a "0.10 delta" strike with the roll's own 20-day
**realised** volatility -- the only vol they have for every past day. The
market's 0.10-delta strike uses the exchange's **implied** vol, which sits above
realised and carries skew, so it lies further below spot. Neither is wrong; they
answer different questions, and a reader who sees only one of them will read
"0.10 delta" as the desk's 10-delta put. This module computes both for the
latest session so the page can show the gap rather than hide it.

The market side is the platform's ONE delta convention (dividend-adjusted
Black-Scholes spot delta, :mod:`.strike_rule`) evaluated on each listed put's
own implied vol -- never the vendor's ``delta`` column, whose convention is not
ours. It returns the listed strike whose delta is nearest the target, at the
nearest listed expiry on or after the tenor.

The two sides keep their own clocks, on purpose: the model side is the
backtest's (tenor in trading days, T = days/252, exactly what a roll uses); the
market side is the listed contract's (expiry counted in calendar days from the
chain's session, T = days/365). A 4-week tenor is 20/252 = 0.079 on one side
and at least 28/365 = 0.077 on the other. A zero-filled IV is already null in
bronze (``contracts/option_chain.py``) and is skipped.

Pure: the chain and the price path arrive as frames.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.research.backtest.put_roll import (
    REALIZED_VOL_CAP,
    REALIZED_VOL_FLOOR,
    TRADING_DAYS_PER_WEEK,
)
from tail_lab.research.backtest.strike_rule import ByDelta, put_delta

__all__ = ["MarketStrike", "ModelStrike", "StrikePreview", "preview_strikes"]


class ModelStrike(BaseModel):
    """The strike the served backtest would pick today: delta at realised vol."""

    vol_date: dt.date
    spot: float
    sigma: float
    t_years: float
    strike: float
    moneyness_pct: float


class MarketStrike(BaseModel):
    """The listed put whose delta, on its own implied vol, is nearest the target."""

    session: dt.date
    expiration: dt.date
    spot: float
    t_years: float
    strike: float
    iv: float
    delta: float
    moneyness_pct: float


class StrikePreview(BaseModel):
    asset: str
    target_delta: float
    tenor_weeks: float
    #: The date ``q`` was read as of (the request's as-of), so the page can
    #: say whose yield it is: not necessarily the latest roll's.
    as_of: dt.date
    r: float
    q: float
    q_source: str
    model: ModelStrike | None
    market: MarketStrike | None
    #: Why ``market`` is absent: the asset is not in the collected chain, no
    #: listed expiry reaches the tenor, or no put there has a usable IV.
    market_status: Literal["quoted", "not_collected", "no_expiry", "no_iv"]
    #: The chain's session is older than the realised-vol date: the two sides
    #: are not the same day, and the page says so.
    market_is_older: bool


def _model_side(
    prices: pd.Series, realized_vol: pd.Series, rule: ByDelta, t_years: float, r: float, q: float
) -> ModelStrike | None:
    vol = realized_vol.dropna()
    if vol.empty:
        return None
    sigma = float(min(max(float(vol.iloc[-1]), REALIZED_VOL_FLOOR), REALIZED_VOL_CAP))
    day = vol.index[-1]
    spot = float(prices.loc[day])
    strike = rule.strike(spot=spot, sigma=sigma, t_years=t_years, r=r, q=q)
    return ModelStrike(
        vol_date=pd.Timestamp(day).date(),
        spot=spot,
        sigma=sigma,
        t_years=t_years,
        strike=strike,
        moneyness_pct=(1.0 - strike / spot) * 100.0,
    )


def _market_side(
    puts: pd.DataFrame, target: float, after: dt.date, r: float, q: float
) -> tuple[MarketStrike | None, Literal["quoted", "no_expiry", "no_iv"]]:
    eligible = puts.loc[puts["expiration"] >= pd.Timestamp(after), "expiration"]
    if eligible.empty:
        return None, "no_expiry"
    expiry = pd.Timestamp(eligible.min())
    at = puts[(puts["expiration"] == expiry) & (puts["iv"] > 0)]
    if at.empty:
        return None, "no_iv"
    session = pd.Timestamp(at["quote_date"].iloc[0])
    t_years = (expiry - session).days / 365.0
    spot = float(at["spot"].iloc[0])
    deltas = np.array(
        [
            put_delta(spot=spot, strike=float(k), sigma=float(v), t_years=t_years, r=r, q=q)
            for k, v in zip(at["strike"], at["iv"], strict=True)
        ]
    )
    pos = int(np.abs(deltas + target).argmin())
    row = at.iloc[pos]
    return (
        MarketStrike(
            session=session.date(),
            expiration=expiry.date(),
            spot=spot,
            t_years=t_years,
            strike=float(row["strike"]),
            iv=float(row["iv"]),
            delta=float(deltas[pos]),
            moneyness_pct=(1.0 - float(row["strike"]) / spot) * 100.0,
        ),
        "quoted",
    )


def preview_strikes(
    chain: pd.DataFrame,
    prices: pd.Series,
    realized_vol: pd.Series,
    *,
    asset: str,
    as_of: dt.date,
    target_delta: float,
    tenor_weeks: float,
    r: float,
    q: float,
    q_source: str,
) -> StrikePreview:
    """Model strike (realised vol) and market strike (listed IV) for one target."""
    rule = ByDelta(target_delta)
    tenor_days = max(round(tenor_weeks * TRADING_DAYS_PER_WEEK), 1)
    model = _model_side(prices, realized_vol, rule, tenor_days / 252.0, r, q)
    puts = chain[chain["underlying"].str.lower() == asset.lower()] if not chain.empty else chain
    market: MarketStrike | None = None
    status: Literal["quoted", "not_collected", "no_expiry", "no_iv"] = "not_collected"
    if not puts.empty:
        session = pd.Timestamp(puts["quote_date"].max())
        today = puts[puts["quote_date"] == session]
        # The tenor in calendar days from the chain's own session (7 per week).
        after = (session + pd.Timedelta(days=math.ceil(tenor_weeks * 7))).date()
        market, status = _market_side(today, target_delta, after, r, q)
    return StrikePreview(
        asset=asset,
        target_delta=target_delta,
        tenor_weeks=tenor_weeks,
        as_of=as_of,
        r=r,
        q=q,
        q_source=q_source,
        model=model,
        market=market,
        market_status=status,
        market_is_older=bool(
            market is not None and model is not None and market.session < model.vol_date
        ),
    )
