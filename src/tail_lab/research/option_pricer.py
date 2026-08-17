"""Pluggable option-pricing interface — the seed of the model-priced backtest.

The README's v1 backtest prices historical OOM puts with a model (Black-
Scholes today; a vol-surface proxy and, later, real historical option
quotes are upgrades behind this same interface — README, `docs/adr/0004`).
No backtest exists yet in this walking skeleton; this module only defines
the interface and one correct, tested implementation so the pattern is set.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod

from scipy.stats import norm


class OptionPricer(ABC):
    """Prices a European put given spot, strike, time-to-expiry, rate, vol."""

    @abstractmethod
    def price_put(
        self, *, spot: float, strike: float, t_years: float, r: float, sigma: float
    ) -> float:
        """Price a European put.

        Args:
            spot: current underlying price (S).
            strike: strike price (K).
            t_years: time to expiry in years (T).
            r: continuously-compounded risk-free rate (annualized).
            sigma: annualized volatility of the underlying.
        """


class BlackScholesPricer(OptionPricer):
    """Closed-form Black-Scholes European put price."""

    def price_put(
        self, *, spot: float, strike: float, t_years: float, r: float, sigma: float
    ) -> float:
        if spot <= 0 or strike <= 0 or t_years <= 0 or sigma <= 0:
            raise ValueError("spot, strike, t_years, and sigma must all be positive")

        d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * t_years) / (sigma * t_years**0.5)
        d2 = d1 - sigma * t_years**0.5

        discounted_strike = strike * math.exp(-r * t_years)
        return float(discounted_strike * norm.cdf(-d2) - spot * norm.cdf(-d1))
