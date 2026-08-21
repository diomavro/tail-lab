"""Decompose the model-vs-market residual into the volatility skew.

``docs/MODEL_RESIDUAL.md`` measures *that* a model-priced put roll beats the
real published index (+1.34%/yr at 5% OTM, +2.71%/yr at 10%) and argues the
cause is skew: the pricer is fed the VIX, a ~30-day ATM implied vol, but the
option it prices is out of the money, where implied vol is higher. That is an
argument, not a measurement — the residual also contains settlement
convention, a flat rate, and the dividend assumption.

This module turns it into a measurement. With real historical quotes in bronze
(``ingestion/option_quotes.py``) the same put can be priced twice on the same
day: once by the model at VIX, once by the market. The difference in premium,
annualized over the roll cadence, is **the share of the residual that skew
alone explains**. Whatever is left over belongs to the conventions.

The inversion runs by bisection rather than Newton. A put price is monotone in
volatility, so bisection cannot diverge; Newton can, on the deep-OTM strikes
where vega is nearly zero and where this measurement matters most.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.option_quotes import DATASET as OPTION_QUOTES_DATASET
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.index_replication import DAYS_PER_YEAR, DEFAULT_DIVIDEND_YIELD
from tail_lab.research.backtest.put_roll import DEFAULT_RATE
from tail_lab.research.option_pricer import BlackScholesPricer, OptionPricer

VIX_DATASET = "vix"

#: Bisection bounds on implied volatility. The floor is above zero because a
#: put quoted at its intrinsic value has no finite IV; the ceiling is far above
#: anything seen (March 2020 peaked near 100% on ATM SPY).
IV_MIN = 0.01
IV_MAX = 5.0
#: Bisection iterations. 60 halvings of [0.01, 5.0] resolve far below the
#: precision of the quotes being inverted.
IV_ITERATIONS = 60


class SkewObservation(BaseModel):
    """One roll date: the same put priced by the model and by the market."""

    quote_date: dt.date
    expiration: dt.date
    spot: float
    strike: float
    moneyness: float  # strike / spot
    t_years: float
    market_premium: float  # mid of the real quote
    model_premium: float  # Black-Scholes at the VIX
    market_iv: float | None  # mid inverted through the pricer
    model_iv: float  # the VIX that day
    #: Market minus model premium, as a fraction of spot. Positive means the
    #: model underpaid for protection on this roll.
    underpayment: float


class SkewSummary(BaseModel):
    """What skew explains, over one target moneyness."""

    moneyness_pct: float
    rolls_per_year: int
    n_observations: int
    start: dt.date
    end: dt.date
    mean_market_iv: float | None
    mean_model_iv: float
    #: Mean (market IV - VIX) in vol points. The skew premium, measured.
    mean_iv_gap: float | None
    #: **Median** market/model premium ratio — how many times the model
    #: underpaid on a typical roll. Median, not mean: past ~15% OTM the model
    #: premium approaches zero while the market still charges real money, so
    #: the mean ratio runs to 1e13 and describes one observation rather than
    #: the sample. The median stays a number a human can act on.
    median_premium_ratio: float
    #: Mean underpayment per roll as a fraction of spot, annualized by the roll
    #: cadence. **This is the share of the residual skew accounts for.**
    annualized_underpayment: float
    observations: list[SkewObservation]


def implied_vol_put(
    price: float,
    *,
    spot: float,
    strike: float,
    t_years: float,
    r: float,
    q: float,
    pricer: OptionPricer | None = None,
) -> float | None:
    """Invert a put price to its Black-Scholes implied volatility.

    Returns ``None`` when the price lies outside the model's reachable range —
    below the discounted intrinsic (an arbitrage or a stale quote) or above
    what even ``IV_MAX`` produces. That is a real and informative outcome, so
    it is reported rather than clamped to a bound, which would silently plant a
    fabricated 500% vol in the middle of an average.
    """
    pricer = pricer or BlackScholesPricer()
    low, high = IV_MIN, IV_MAX
    if not (
        pricer.price_put(spot=spot, strike=strike, t_years=t_years, r=r, sigma=low, q=q)
        <= price
        <= pricer.price_put(spot=spot, strike=strike, t_years=t_years, r=r, sigma=high, q=q)
    ):
        return None
    for _ in range(IV_ITERATIONS):
        mid = 0.5 * (low + high)
        if pricer.price_put(spot=spot, strike=strike, t_years=t_years, r=r, sigma=mid, q=q) < price:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def _nearest_strike(chain: pd.DataFrame, target: float) -> pd.Series:
    """The quoted strike closest to ``target`` — what a real roll would buy."""
    idx = (chain["strike"] - target).abs().idxmin()
    row = chain.loc[[idx]].iloc[0]
    return pd.Series(row)


def measure_skew(
    quotes: pd.DataFrame,
    vix: pd.Series,
    *,
    moneyness_pct: float,
    rolls_per_year: int = 12,
    rate: float = DEFAULT_RATE,
    dividend_yield: float = DEFAULT_DIVIDEND_YIELD,
    pricer: OptionPricer | None = None,
) -> SkewSummary:
    """Price the same put by model and by market at every roll date. Pure.

    ``quotes`` is the bronze put-smile frame; ``vix`` is a date-indexed series
    of VIX closes **in points** (17.5, not 0.175). Roll dates with no usable
    quote at the target strike are skipped — but a quote that simply cannot be
    inverted still contributes its *premium* comparison, because the premium is
    what the backtest actually pays and the IV is only the explanation.
    """
    pricer = pricer or BlackScholesPricer()
    target_ratio = 1.0 - moneyness_pct / 100.0
    observations: list[SkewObservation] = []

    for quote_date, chain in quotes.groupby("quote_date", sort=True):
        stamp = pd.Timestamp(str(quote_date))
        eligible = vix.loc[vix.index <= stamp]
        if eligible.empty:
            continue
        sigma = float(eligible.iloc[-1]) / 100.0
        if not (np.isfinite(sigma) and sigma > 0):
            continue

        spot = float(chain["spot"].iloc[0])
        row = _nearest_strike(chain, spot * target_ratio)
        strike = float(row["strike"])
        expiration = pd.Timestamp(str(row["expiration"]))
        t_years = (expiration - stamp).days / DAYS_PER_YEAR
        if t_years <= 0:
            continue

        market = (float(row["bid"]) + float(row["ask"])) / 2.0
        model = pricer.price_put(
            spot=spot, strike=strike, t_years=t_years, r=rate, sigma=sigma, q=dividend_yield
        )
        observations.append(
            SkewObservation(
                quote_date=stamp.date(),
                expiration=expiration.date(),
                spot=spot,
                strike=strike,
                moneyness=strike / spot,
                t_years=t_years,
                market_premium=market,
                model_premium=model,
                market_iv=implied_vol_put(
                    market,
                    spot=spot,
                    strike=strike,
                    t_years=t_years,
                    r=rate,
                    q=dividend_yield,
                    pricer=pricer,
                ),
                model_iv=sigma,
                underpayment=(market - model) / spot,
            )
        )

    if not observations:
        raise LookupError("no roll date had both a usable quote and a VIX close")

    inverted = [o.market_iv for o in observations if o.market_iv is not None]
    underpayments = np.array([o.underpayment for o in observations], dtype=float)
    ratios = np.array(
        [o.market_premium / o.model_premium for o in observations if o.model_premium > 0],
        dtype=float,
    )
    return SkewSummary(
        moneyness_pct=moneyness_pct,
        rolls_per_year=rolls_per_year,
        n_observations=len(observations),
        start=observations[0].quote_date,
        end=observations[-1].quote_date,
        mean_market_iv=float(np.mean(inverted)) if inverted else None,
        mean_model_iv=float(np.mean([o.model_iv for o in observations])),
        mean_iv_gap=(
            float(
                np.mean([o.market_iv - o.model_iv for o in observations if o.market_iv is not None])
            )
            if inverted
            else None
        ),
        median_premium_ratio=float(np.median(ratios)) if len(ratios) else float("nan"),
        annualized_underpayment=float(underpayments.mean()) * rolls_per_year,
        observations=observations,
    )


def compute_skew_measurement(
    store: LakeStore,
    *,
    as_of: dt.date,
    moneyness_pct: float = 5.0,
    rolls_per_year: int = 12,
    underlying: str = "SPY",
) -> SkewSummary:
    """Measure the skew premium from the lake, point-in-time as of ``as_of``.

    Raises ``LookupError`` when either input is missing. Unlike the accuracy
    report this one *is* allowed to fail: it is a research measurement run by
    hand, not a surface that must never go silent, and its input is the
    licence-limited quote snapshot that may legitimately not exist.
    """
    quotes = store.read_bronze_as_of(OPTION_QUOTES_DATASET, as_of)
    quotes = quotes[quotes["underlying"] == underlying.upper()]
    if quotes.empty:
        raise LookupError(f"no {underlying.upper()} quotes known as of {as_of.isoformat()}")

    vix_bronze = store.read_bronze_as_of(VIX_DATASET, as_of)
    ordered = vix_bronze.sort_values("date").drop_duplicates(subset="date", keep="last")
    vix = pd.Series(ordered["close"].to_numpy(dtype=float), index=pd.DatetimeIndex(ordered["date"]))
    return measure_skew(quotes, vix, moneyness_pct=moneyness_pct, rolls_per_year=rolls_per_year)
