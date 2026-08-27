"""Pluggable option-pricing interface — the seed of the model-priced backtest.

The README's v1 backtest prices historical OOM puts with a model (Black-
Scholes today; a vol-surface proxy and, later, real historical option
quotes are upgrades behind this same interface — README, ``docs/adr/0004``).

Assumptions register
--------------------
Every one of these is false in some regime, and each is listed with what
breaks when it is (the form is from ``docs/PRIOR_ART.md`` §10). This module
is where the platform's known pricing error lives, so it is where the
register belongs. "Accuracy is surfaced, not filed" (README) applies to the
assumptions as much as to the numbers they produce.

1. **Volatility is a single constant per option.** There is no smile and no
   term structure; callers pass one ``sigma``, typically a trailing realized
   vol. *Breaks*: OOM puts are priced too cheap, because the market charges a
   skew premium this model cannot see. **Measured, not guessed**: replicating
   Cboe's PPUT rule with this pricer puts the gap at **+1.34%/yr over 438
   monthly rolls since 1990, and the error flips sign in a crisis**
   (``docs/MODEL_RESIDUAL.md``). This is the single largest known error in
   the platform.
2. **The greeks are therefore sticky-strike.** ``greeks_put`` differentiates a
   surface that does not move when spot does. *Breaks*: under the usual
   negative equity skew the true delta differs by ``vega * dsigma/dS``, so a
   strike chosen to hit a delta target using *these* greeks would miss it.
   **Mitigation in force**: live strike selection uses the exchange's own
   delta from the forward-collected chain (``docs/adr/0020``), not this.
3. **European exercise.** No early exercise. *Breaks*: American puts on
   dividend-paying underlyings are worth more than this returns; the gap
   grows with moneyness, dividend yield and time. Bounded for the short-dated
   OOM puts this platform buys, where early exercise is worth ~nothing.
4. **The dividend yield defaults to zero.** *Breaks*: a yielding underlying
   has a lower forward, so every put on it is worth *more* than ``q=0``
   returns. **This is not a rounding error for the income names in the
   universe.** ``make greeks-check`` scores our delta against the exchange's
   own across 7,618 liquid contracts, and the error orders itself by
   distribution yield exactly as theory demands:

   =========  ==============  ====================
   symbol     approx. yield   median |delta error|
   =========  ==============  ====================
   TSLA       0%              0.0006
   SPY        ~1.2%           0.0043
   TLT        ~4%             0.0601
   HYG        ~6%             0.1967
   =========  ==============  ====================

   A 0.20 delta error on HYG is a different option from the one we think we
   are pricing, and HYG and TLT are in the screening universe precisely as the
   credit and rates hedges. ``index_replication`` passes ``q`` explicitly; the
   roll backtest does **not**, and that is queued in ``AGENT_TODO.md``. Until
   it lands, treat put prices and greeks on income names as biased cheap.
5. **Continuous, frictionless trading at a known constant rate.** No bid-ask,
   no borrow, no rate curve. *Breaks*: real fills cost more. Handled outside
   this module by ``research/backtest/brokerage.py``, which is itself one
   hardcoded half-spread rather than a model (queued in ``AGENT_TODO.md``).
6. **Log-normal returns.** The distribution has no fat tails. *Breaks*: this
   is the assumption the platform's whole thesis says the market misprices,
   so it is load-bearing in the uncomfortable direction — the model is wrong
   in exactly the way the strategy is trying to profit from. Treat
   model-priced results as a *relative ranking* of sensitivity metrics, never
   as P&L truth (README, ``docs/adr/0004``).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

#: Vega is reported per **one volatility point** (a 0.01 move in sigma), and
#: theta per **one calendar day**, following the convention every desk tool
#: uses (`docs/PRIOR_ART.md` §2). The raw partial derivatives are per unit
#: sigma and per year; these are the scalings applied on the way out. A greek
#: whose units are not the ones the reader assumes is worse than no greek.
VEGA_PER_VOL_POINT = 0.01
DAYS_PER_YEAR = 365.25


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class PutGreeks:
    """Sensitivities of a European put, in desk units.

    ``delta`` and ``gamma`` are per unit of spot, ``vega`` per volatility
    *point*, ``theta`` per calendar *day*, ``rho`` per rate point. ``itm_prob``
    is the risk-neutral probability the put finishes in the money -- the
    market's own answer to "how often does this payoff trigger", and the reason
    it is carried here rather than derived at each call site.

    **These are sticky-strike greeks** (`docs/PRIOR_ART.md` §6). They assume
    the smile does not move when spot does, which is false for equities: under
    the usual negative skew the true delta differs by ``vega * dsigma/dS``.
    This pricer has no smile to differentiate, so it cannot do better -- which
    is exactly why strike selection on live chains must use the exchange's own
    delta (`docs/adr/0020`) rather than these.
    """

    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    itm_prob: float


def _norm_cdf(x: float) -> float:
    """Standard normal CDF.

    ``0.5 * erfc(-x / sqrt(2))`` is the identity, and ``erfc`` rather than
    ``1 + erf`` because it stays accurate in the far tails, which is exactly
    where deep out-of-the-money puts live.

    This replaced ``scipy.stats.norm.cdf``, which is correct but routes every
    *scalar* call through the generic distribution machinery (argument
    reduction, broadcasting, support masks). Profiling the strike x tenor
    sweep put **72% of its total runtime** in that one function; the universe
    ranking runs ~1,600 of those sweeps. The pricer's property tests --
    put-call parity against an independently-implemented call, across the whole
    valid input space -- are what make this substitution safe to make.
    """
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


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

    @abstractmethod
    def greeks_put(
        self,
        *,
        spot: float,
        strike: float,
        t_years: float,
        r: float,
        sigma: float,
        q: float = 0.0,
    ) -> PutGreeks:
        """Sensitivities of the same European put ``price_put`` prices.

        Same arguments, same conventions. Separate from ``price_put`` because
        most callers want only the price and computing five partials for them
        would be waste on a sweep that runs ~1,600 times per ranking.
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
        return float(discounted_strike * _norm_cdf(-d2) - discounted_spot * _norm_cdf(-d1))

    def greeks_put(
        self,
        *,
        spot: float,
        strike: float,
        t_years: float,
        r: float,
        sigma: float,
        q: float = 0.0,
    ) -> PutGreeks:
        """Closed-form Merton greeks, scaled to desk units on the way out.

        Analytic rather than finite-differenced: the closed form is exact and
        costs one ``_norm_pdf`` more than the price, whereas a central
        difference would cost four extra prices and introduce a step-size
        choice nobody would revisit.
        """
        if spot <= 0 or strike <= 0 or t_years <= 0 or sigma <= 0:
            raise ValueError("spot, strike, t_years, and sigma must all be positive")

        vol_t = sigma * t_years**0.5
        d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma**2) * t_years) / vol_t
        d2 = d1 - vol_t

        disc_r = math.exp(-r * t_years)
        disc_q = math.exp(-q * t_years)
        pdf_d1 = _norm_pdf(d1)

        delta = -disc_q * _norm_cdf(-d1)
        gamma = disc_q * pdf_d1 / (spot * vol_t)
        vega_raw = spot * disc_q * pdf_d1 * t_years**0.5
        theta_raw = (
            -spot * disc_q * pdf_d1 * sigma / (2.0 * t_years**0.5)
            - q * spot * disc_q * _norm_cdf(-d1)
            + r * strike * disc_r * _norm_cdf(-d2)
        )
        rho_raw = -strike * t_years * disc_r * _norm_cdf(-d2)

        return PutGreeks(
            delta=float(delta),
            gamma=float(gamma),
            vega=float(vega_raw * VEGA_PER_VOL_POINT),
            theta=float(theta_raw / DAYS_PER_YEAR),
            rho=float(rho_raw * VEGA_PER_VOL_POINT),
            itm_prob=float(_norm_cdf(-d2)),
        )
