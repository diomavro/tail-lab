"""How a roll picks its strike: a fixed distance below spot, or a target delta.

``ByMoneyness(pct)`` is the original rule -- ``K = spot * (1 - pct/100)``. It is
simple and it confounds every comparison across regimes (`docs/PRIOR_ART.md`
§1): a "10% below spot, 4-week" put has delta 0.0006 in a calm market (vol 12%)
and 0.18 in a crisis (vol 45%), so a verdict keyed on regime partly measures
whether the strike was reachable.

``ByDelta(target)`` picks the strike whose put delta is ``-target``, under ONE
convention used everywhere in this platform (`docs/adr/0029`): the
Black-Scholes **European spot delta, dividend-adjusted**,

    delta = -e^{-qT} N(-d1),   d1 = (ln(S/K) + (r - q + sigma^2/2) T) / (sigma sqrt T)

which inverts in closed form:

    d1 = -N^{-1}(|delta| e^{qT}),   K = S exp(-d1 sigma sqrt T + (r - q + sigma^2/2) T)

Which ``sigma`` matters and is never hidden. The served backtests price with
the roll's own 20-day **realised** volatility, so the strike they pick is "0.10
delta *at realised vol*" -- in a calm market with sigma = 8% that is only ~2.5%
below spot, nothing like a desk's 10-delta put, because the market's implied
vol sits above realised and carries skew. The market's own 0.10-delta strike
(our formula on the exchange's implied vol) is shown beside it, never pooled
with it.

The inversion needs ``|delta| e^{qT} < 1``; a target that fails it has no
strike and is refused rather than clipped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

__all__ = [
    "MAX_TARGET_DELTA",
    "MIN_TARGET_DELTA",
    "ByDelta",
    "ByMoneyness",
    "StrikeRule",
    "put_delta",
]

#: Bounds for a target put delta (absolute value). Below 0.01 the strike runs
#: off any listed chain. At 0.50, d1 = 0 and K = S e^((r - q + sigma^2/2) T):
#: at the money (just above spot unless q > r + sigma^2/2) -- the edge of a
#: hedge, so the bound stops there.
MIN_TARGET_DELTA = 0.01
MAX_TARGET_DELTA = 0.50

_N = NormalDist()


@dataclass(frozen=True)
class ByMoneyness:
    """Strike ``pct`` percent below spot."""

    pct: float
    kind: Literal["moneyness"] = "moneyness"

    def __post_init__(self) -> None:
        if not 0.0 < self.pct < 100.0:
            raise ValueError(f"moneyness must be in (0, 100), got {self.pct}")

    def strike(self, *, spot: float, sigma: float, t_years: float, r: float, q: float) -> float:
        del sigma, t_years, r, q  # same signature as ByDelta; a fixed distance needs none
        return spot * (1.0 - self.pct / 100.0)


@dataclass(frozen=True)
class ByDelta:
    """Strike whose dividend-adjusted Black-Scholes put delta is ``-target``."""

    target: float
    kind: Literal["delta"] = "delta"

    def __post_init__(self) -> None:
        if not MIN_TARGET_DELTA <= self.target <= MAX_TARGET_DELTA:
            raise ValueError(
                f"target delta must be in [{MIN_TARGET_DELTA}, {MAX_TARGET_DELTA}], "
                f"got {self.target}"
            )

    def strike(self, *, spot: float, sigma: float, t_years: float, r: float, q: float) -> float:
        scaled = self.target * math.exp(q * t_years)
        if scaled >= 1.0:  # the target is >= 0.01, so scaled is always > 0
            raise ValueError(
                f"no strike has put delta -{self.target} at q={q}, T={t_years}: "
                f"|delta| e^(qT) = {scaled:.4f} is not below 1"
            )
        root_t = math.sqrt(t_years)
        d1 = -_N.inv_cdf(scaled)
        return spot * math.exp(-d1 * sigma * root_t + (r - q + 0.5 * sigma * sigma) * t_years)


StrikeRule = ByMoneyness | ByDelta


def put_delta(
    *, spot: float, strike: float, sigma: float, t_years: float, r: float, q: float
) -> float:
    """The convention above, forward: ``-e^{-qT} N(-d1)`` (negative)."""
    root_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t_years) / (sigma * root_t)
    return -math.exp(-q * t_years) * _N.cdf(-d1)
