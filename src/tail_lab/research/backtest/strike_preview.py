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
nearest listed expiry on or after the tenor. The Screen asks the same question
for its fixed-distance strike (:func:`today_strikes` with a ``ByMoneyness``
rule): the listed strike nearest that distance below the chain's spot.

The two sides keep their own clocks, on purpose: the model side is the
backtest's (tenor in trading days, T = days/252, exactly what a roll uses); the
market side is the listed contract's (expiry counted in calendar days from the
chain's session, T = days/365). A 4-week tenor is 20/252 = 0.079 on one side
and at least 28/365 = 0.077 on the other. A zero-filled IV is already null in
bronze (``contracts/option_chain.py``) and is skipped.

Each side also carries what holding it costs against what it pays
(``docs/PRIOR_ART.md`` §11): ``convexity_ratio`` is the put's intrinsic value
at expiry after a :data:`TAIL_DROP` fall, divided by a year of rolling it
(``premium / t_years`` -- rolls per year on the side's own clock, never a fixed
twelve). The model side's premium is the Black-Scholes price at realised vol;
the market side's is the listed ask, what a buyer pays.

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
from tail_lab.research.backtest.strike_rule import ByDelta, ByMoneyness, StrikeRule, put_delta
from tail_lab.research.option_pricer import BlackScholesPricer

__all__ = [
    "MAX_STRIKE_MISS_PCT",
    "TAIL_DROP",
    "MarketStatus",
    "MarketStrike",
    "ModelStrike",
    "StrikePreview",
    "convexity_ratio",
    "preview_strikes",
    "today_strikes",
]

#: Why a market side is or is not quoted (see :class:`StrikePreview`).
MarketStatus = Literal["quoted", "not_collected", "no_expiry", "no_iv", "no_strike"]

#: How far, in percent of spot, the listed strike a distance rule takes may sit
#: from that distance. Beyond it the chain does not list the strike asked
#: about, and scoring a nearer one would pass a different put off as it.
MAX_STRIKE_MISS_PCT = 2.5

#: The fall ``convexity_ratio`` is scored on: 20%, ``options_portfolio_
#: backtester``'s ``tail_drop`` (``docs/PRIOR_ART.md`` §11).
TAIL_DROP = 0.20


def convexity_ratio(*, spot: float, strike: float, premium: float, t_years: float) -> float | None:
    """Intrinsic value after a :data:`TAIL_DROP` fall over a year of premium.

    3.0 means one such fall, held to expiry, repays three years of rolling this
    put; 0.0 means the strike sits below the fallen spot and the fall pays
    nothing. ``None`` when there is no premium to divide by."""
    if premium <= 0.0:
        return None
    payoff = max(strike - (1.0 - TAIL_DROP) * spot, 0.0)
    return payoff / (premium / t_years)


class ModelStrike(BaseModel):
    """The strike the served backtest would pick today: delta at realised vol."""

    vol_date: dt.date
    spot: float
    sigma: float
    t_years: float
    strike: float
    moneyness_pct: float
    #: Black-Scholes price at ``sigma``, per share.
    premium: float
    convexity_ratio: float | None


class MarketStrike(BaseModel):
    """The listed put nearest the rule: by delta on its own implied vol, or by
    distance below the chain's spot."""

    session: dt.date
    expiration: dt.date
    spot: float
    t_years: float
    strike: float
    #: ``None`` only on a distance pick the exchange published no IV for; a
    #: delta pick always has both.
    iv: float | None
    delta: float | None
    moneyness_pct: float
    #: The listed ask, per share: what a buyer pays.
    premium: float
    convexity_ratio: float | None


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
    market_status: MarketStatus
    #: The chain's session is older than the realised-vol date: the two sides
    #: are not the same day, and the page says so.
    market_is_older: bool


def _model_side(
    prices: pd.Series, realized_vol: pd.Series, rule: StrikeRule, t_years: float, r: float, q: float
) -> ModelStrike | None:
    vol = realized_vol.dropna()
    if vol.empty:
        return None
    sigma = float(min(max(float(vol.iloc[-1]), REALIZED_VOL_FLOOR), REALIZED_VOL_CAP))
    day = vol.index[-1]
    spot = float(prices.loc[day])
    strike = rule.strike(spot=spot, sigma=sigma, t_years=t_years, r=r, q=q)
    premium = BlackScholesPricer().price_put(
        spot=spot, strike=strike, t_years=t_years, r=r, sigma=sigma, q=q
    )
    return ModelStrike(
        vol_date=pd.Timestamp(day).date(),
        spot=spot,
        sigma=sigma,
        t_years=t_years,
        strike=strike,
        moneyness_pct=(1.0 - strike / spot) * 100.0,
        premium=premium,
        convexity_ratio=convexity_ratio(spot=spot, strike=strike, premium=premium, t_years=t_years),
    )


def _nearest_delta(
    at: pd.DataFrame, target: float, t_years: float, r: float, q: float
) -> pd.Series | None:
    """The put whose delta on its own IV is nearest ``-target``; zero-filled
    (null) IVs cannot be priced and are skipped."""
    priced = at[at["iv"] > 0]
    if priced.empty:
        return None
    spot = float(priced["spot"].iloc[0])
    deltas = [
        put_delta(spot=spot, strike=float(k), sigma=float(v), t_years=t_years, r=r, q=q)
        for k, v in zip(priced["strike"], priced["iv"], strict=True)
    ]
    return priced.iloc[int(np.abs(np.array(deltas) + target).argmin())]


def _nearest_distance(at: pd.DataFrame, rule: ByMoneyness) -> pd.Series | None:
    """The listed strike nearest ``rule``'s distance below the chain's spot,
    whatever its IV (the ask is what is scored), or ``None`` when the nearest
    misses by more than :data:`MAX_STRIKE_MISS_PCT`."""
    spot = float(at["spot"].iloc[0])
    target = spot * (1.0 - rule.pct / 100.0)
    miss = np.abs(at["strike"].to_numpy(dtype=float) - target)
    pos = int(miss.argmin())
    return None if miss[pos] > spot * MAX_STRIKE_MISS_PCT / 100.0 else at.iloc[pos]


def _market_side(
    puts: pd.DataFrame, rule: StrikeRule, after: dt.date, r: float, q: float
) -> tuple[MarketStrike | None, MarketStatus]:
    eligible = puts.loc[puts["expiration"] >= pd.Timestamp(after), "expiration"]
    if eligible.empty:
        return None, "no_expiry"
    expiry = pd.Timestamp(eligible.min())
    at = puts[puts["expiration"] == expiry]
    session = pd.Timestamp(at["quote_date"].iloc[0])
    t_years = (expiry - session).days / 365.0
    if isinstance(rule, ByMoneyness):
        row = _nearest_distance(at, rule)
        if row is None:
            return None, "no_strike"
    else:
        row = _nearest_delta(at, rule.target, t_years, r, q)
        if row is None:
            return None, "no_iv"
    spot, strike, ask = float(row["spot"]), float(row["strike"]), float(row["ask"])
    iv = float(row["iv"]) if pd.notna(row["iv"]) and row["iv"] > 0 else None
    return (
        MarketStrike(
            session=session.date(),
            expiration=expiry.date(),
            spot=spot,
            t_years=t_years,
            strike=strike,
            iv=iv,
            delta=None
            if iv is None
            else put_delta(spot=spot, strike=strike, sigma=iv, t_years=t_years, r=r, q=q),
            moneyness_pct=(1.0 - strike / spot) * 100.0,
            premium=ask,
            convexity_ratio=convexity_ratio(spot=spot, strike=strike, premium=ask, t_years=t_years),
        ),
        "quoted",
    )


def today_strikes(
    chain: pd.DataFrame,
    prices: pd.Series,
    realized_vol: pd.Series,
    *,
    asset: str,
    rule: StrikeRule,
    tenor_weeks: float,
    r: float,
    q: float,
) -> tuple[ModelStrike | None, MarketStrike | None, MarketStatus]:
    """Today's strike under ``rule``, both sides, and why the market side is absent.

    A :class:`ByMoneyness` rule takes the listed strike nearest its distance
    below the chain's spot, IV or not, and none if that misses by more than
    :data:`MAX_STRIKE_MISS_PCT`; a :class:`ByDelta` rule the one nearest its
    delta. Only the chain's latest session is read."""
    tenor_days = max(round(tenor_weeks * TRADING_DAYS_PER_WEEK), 1)
    model = _model_side(prices, realized_vol, rule, tenor_days / 252.0, r, q)
    puts = chain[chain["underlying"].str.lower() == asset.lower()] if not chain.empty else chain
    if puts.empty:
        return model, None, "not_collected"
    session = pd.Timestamp(puts["quote_date"].max())
    latest = puts[puts["quote_date"] == session]
    # The tenor in calendar days from the chain's own session (7 per week).
    after = (session + pd.Timedelta(days=math.ceil(tenor_weeks * 7))).date()
    market, status = _market_side(latest, rule, after, r, q)
    return model, market, status


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
    model, market, status = today_strikes(
        chain,
        prices,
        realized_vol,
        asset=asset,
        rule=ByDelta(target_delta),
        tenor_weeks=tenor_weeks,
        r=r,
        q=q,
    )
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
