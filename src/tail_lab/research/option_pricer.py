"""Pluggable option-pricing interface — the seed of the model-priced backtest.

The README's v1 backtest prices historical OOM puts with a model (Black-
Scholes today; a vol-surface proxy and, later, real historical option
quotes are upgrades behind this same interface — README, ``docs/adr/0004``).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod

from scipy.stats import norm


class OptionPricer(ABC):
    """Prices a European put given spot, strike, time-to-expiry, rate, vol."""

    @abstractmethod
    def price_put(
        self,
        *,
        spot: float,
        strike: float,
        t_years: float,
        r: float,
        sigma: float,
        q: float = 0.0,
    ) -> float:
        """Price a European put.

        Args:
            spot: current underlying price (S).
            strike: strike price (K).
            t_years: time to expiry in years (T).
            r: continuously-compounded risk-free rate (annualized).
            sigma: annualized volatility of the underlying.
            q: continuously-compounded dividend yield (annualized). Defaults
                to ``0.0``, which is right for a single non-payer and close
                enough for a short-dated equity put; it is **not** right for a
                broad index, where a ~2% yield lowers the forward and so
                *raises* every put. Index replications must pass it
                (:mod:`tail_lab.research.backtest.index_replication`).
        """


class BlackScholesPricer(OptionPricer):
    """Closed-form Black-Scholes(-Merton) European put price.

    With a continuous dividend yield ``q`` the spot leg is discounted by
    ``exp(-q T)`` and the drift becomes ``r - q`` — the Merton (1973)
    extension. At ``q = 0`` this reduces exactly to plain Black-Scholes, so
    existing callers are unaffected.
    """

    def price_put(
        self,
        *,
        spot: float,
        strike: float,
        t_years: float,
        r: float,
        sigma: float,
        q: float = 0.0,
    ) -> float:
        if spot <= 0 or strike <= 0 or t_years <= 0 or sigma <= 0:
            raise ValueError("spot, strike, t_years, and sigma must all be positive")

        vol_t = sigma * t_years**0.5
        d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma**2) * t_years) / vol_t
        d2 = d1 - vol_t

        discounted_strike = strike * math.exp(-r * t_years)
        discounted_spot = spot * math.exp(-q * t_years)
        return float(discounted_strike * norm.cdf(-d2) - discounted_spot * norm.cdf(-d1))
