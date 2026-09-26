"""The largest tail index a put anchor can carry without arbitrage
(``AGENT_TODO.md`` P3; ``docs/adr/0026``).

A Paretan ladder is spliced onto the market at the anchor: the market's own
smile prices strikes above it, the power law prices strikes below it. Put
prices must be convex in the strike (Breeden-Litzenberger: the second
derivative is the risk-neutral density), so at the splice the Paretan slope
from below may not exceed the market's slope from above::

    lambda l^a [(S0 / (S0 - K))^a - 1]  <=  dBSP(K, sigma(K)) / dK
                                         =  e^(-rT) N(-d2) + vega * sigma'(K)

Both sides are a cumulative probability at the anchor: the right is the
market's, the left the power law's. The paper (section "Arbitrage Boundaries")
states this for calls and calls the result a LOWER bound on alpha. That holds
only for a FIXED ``l``. Here ``l`` is calibrated from the anchor price at every
trial alpha (``anchor_l_put``), as it must be, since an ``l`` estimated from
other data describes some other tail. With the price held fixed, a fatter tail (lower alpha)
puts more of the put's value deep and LESS probability just below the anchor,
so the left side rises with alpha and the inequality caps it from ABOVE. A
direct butterfly across the splice confirms the direction
(``tests/test_research_surface_alpha_bound.py``).

**What the ceiling is not.** It is not a bound on P2's fitted alpha. A market
that truly is a (truncated) power law below the anchor sits exactly ON its
ceiling; a thin-tailed Black-Scholes smile sits BELOW the alpha P2 fits to it
(spot 1000, anchor 900, 30 days, 25 % vol, slope -0.001: P2 fits 3.34, ceiling
2.75), because P2 reads the decay of the deeper prices and the ceiling reads
one slope at the anchor. A P2 alpha above the ceiling means P2's own ladder,
spliced at the anchor, is non-convex against the smile -- a sign the chain is
not a power law, alongside P2's anchor dispersion. Two further limits: the
"exactly on" holds for the paper's truncated tail, not a censored one (mass at
zero puts the true alpha ABOVE the ceiling); and the arbitrage beyond the
ceiling is a kink at the anchor, which on a short expiry can be too small to
trade at listed strike spacing -- it is the model's own consistency limit.

Two departures from the P3 spec, both forced by the maths:

* There is no free ``strike``. The inequality lives at the splice, which is the
  anchor; evaluated anywhere else it mixes an ``l`` from one strike with a
  slope from another and certifies nothing.
* ``sigma`` is not an input: it is the anchor price's own implied vol, so the
  two cannot disagree. What the caller must supply is the smile's slope
  ``sigma'(K)`` at the anchor, per unit of strike, measured from neighbouring
  quotes. It is the one number here the anchor price cannot give.

**Refusal is a normal return value**, as in ``implied_alpha``: ``alpha`` is
``None`` with a ``refusal`` reason whenever the search bracket holds no sign
change. There is never a bisection for a root that does not exist.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from tail_lab.research.option_pricer import VEGA_PER_VOL_POINT, BlackScholesPricer
from tail_lab.research.skew import implied_vol_put
from tail_lab.research.surface.implied_alpha import ALPHA_TOLERANCE, _valid_upper_bound
from tail_lab.research.surface.ladder import Anchor
from tail_lab.research.surface.paretan import anchor_l_put


@dataclass(frozen=True)
class AlphaCeiling:
    """One ceiling, or one refusal. ``alpha`` is ``None`` exactly when
    ``refusal`` is set. ``market_slope`` is the right-hand side above -- the
    market's probability of finishing below the anchor -- and is ``None`` only
    when the anchor has no implied vol."""

    anchor: Anchor
    alpha: float | None
    market_slope: float | None
    refusal: str | None


def market_put_slope(
    anchor: Anchor, *, sigma: float, smile_slope: float, r: float, q: float
) -> float:
    """``dBSP(K, sigma(K)) / dK`` at the anchor: the strike partial plus the
    smile term.

    The vega here is ``dP/dsigma`` per UNIT of sigma. ``PutGreeks.vega`` is per
    vol POINT (it carries ``VEGA_PER_VOL_POINT = 0.01``), so it is divided back
    out explicitly: wiring the desk greek in directly shrinks the smile term
    100-fold, and on a skewed chain that alone moves the ceiling by more than
    a whole unit of alpha.
    """
    greeks = BlackScholesPricer().greeks_put(
        spot=anchor.spot, strike=anchor.strike, t_years=anchor.t_years, sigma=sigma, r=r, q=q
    )
    vega_per_unit_sigma = greeks.vega / VEGA_PER_VOL_POINT
    return math.exp(-r * anchor.t_years) * greeks.itm_prob + vega_per_unit_sigma * smile_slope


def paretan_put_slope(anchor: Anchor, alpha: float) -> float:
    """``dP/dK`` of the Paretan put at the anchor, with ``l`` calibrated from
    the anchor price at THIS alpha. Raises ``ValueError`` (from
    ``anchor_l_put``) where the anchor is outside its own calibrated tail."""
    tail = anchor_l_put(price=anchor.price, strike=anchor.strike, spot=anchor.spot, alpha=alpha)
    growth = math.pow(anchor.spot / (anchor.spot - anchor.strike), alpha) - 1.0
    return tail.lambda_correction() * math.pow(tail.karamata_l, alpha) * growth


def alpha_upper_bound(
    anchor: Anchor,
    *,
    smile_slope: float,
    r: float,
    q: float,
    alpha_lo: float = 1.05,
    alpha_hi: float = 10.0,
) -> AlphaCeiling:
    """The largest alpha at which splicing a Paretan tail onto the market at
    ``anchor`` leaves put prices convex.

    ``smile_slope`` is ``d sigma / d K`` at the anchor per unit of strike
    (negative on a normal equity skew). The bracket defaults match
    ``fit_implied_alpha``'s, and it is cut the same way, to the alphas at
    which the anchor lies inside its own calibrated tail. An ``alpha_hi`` far
    beyond the default (around 1000) can overflow ``math.pow`` when spot is
    small and raise ``OverflowError``: the bracket cut stops where
    ``anchor_l_put`` overflows, and ``(S/(S-K))**alpha`` is larger than what it
    checks by a factor ``1/(S-K)``, so it can overflow just inside the cut
    (seen at spot 1, never at spot 10 and above). Within the default it cannot
    (``S/(S-K)`` would need to exceed ~6e30, and a float ratio tops out near
    1e16).
    """
    _require_caller_contract(
        smile_slope=smile_slope, alpha_lo=alpha_lo, alpha_hi=alpha_hi, r=r, q=q
    )
    sigma = implied_vol_put(
        anchor.price, spot=anchor.spot, strike=anchor.strike, t_years=anchor.t_years, r=r, q=q
    )
    if sigma is None:
        return AlphaCeiling(anchor, None, None, "the anchor price has no Black-Scholes implied vol")
    market = market_put_slope(anchor, sigma=sigma, smile_slope=smile_slope, r=r, q=q)

    def refuse(reason: str) -> AlphaCeiling:
        return AlphaCeiling(anchor, None, market, reason)

    # The anchor-level refusal first: it does not depend on smile_slope, so a
    # noisy slope estimate cannot flip which reason the caller sees.
    hi = _valid_upper_bound(anchor, alpha_lo=alpha_lo, alpha_hi=alpha_hi)
    if hi is None:
        return refuse(
            "the anchor lies outside its own calibrated tail (inside the Karamata "
            f"point) even at alpha_lo={alpha_lo}"
        )

    # dP/dK of a put is a discounted probability: a put spread pays between 0
    # and dK whatever the dividend yield, so 0 <= dP/dK <= e^(-rT). Outside
    # that the SMILE already admits put-spread arbitrage, and blaming the tail
    # for it would be a false refusal.
    if not 0.0 <= market <= math.exp(-r * anchor.t_years):
        return refuse(
            f"the smile itself is arbitrageable at the anchor: its slope {market:.4g} is "
            "not a discounted probability, so no tail can be tested against it"
        )

    def slack(alpha: float) -> float:
        return market - paretan_put_slope(anchor, alpha)

    if slack(alpha_lo) < 0.0:
        return refuse(
            f"even alpha={alpha_lo} makes the splice non-convex: the market puts less "
            f"probability below the anchor ({market:.4g}) than any tail in the bracket can"
        )
    if slack(hi) >= 0.0:
        return refuse(
            f"every alpha in [{alpha_lo}, {hi:.4f}] leaves the splice convex; no ceiling inside it"
        )
    return AlphaCeiling(anchor, _last_convex(slack, alpha_lo, hi), market, None)


def _require_caller_contract(
    *, smile_slope: float, alpha_lo: float, alpha_hi: float, r: float, q: float
) -> None:
    for name, value in (("smile_slope", smile_slope), ("r", r), ("q", q)):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite, got {value}")
    # Finite, as in fit_implied_alpha: an infinite alpha_hi bisects forever.
    if not (math.isfinite(alpha_lo) and math.isfinite(alpha_hi) and 1.0 < alpha_lo < alpha_hi):
        raise ValueError(f"need finite 1 < alpha_lo < alpha_hi, got [{alpha_lo}, {alpha_hi}]")


def _last_convex(slack: Callable[[float], float], good: float, bad: float) -> float:
    """Bisect to the sign change of ``slack``, given ``slack(good) >= 0`` and
    ``slack(bad) < 0`` -- the caller has already proved one exists."""
    while bad - good > ALPHA_TOLERANCE:
        mid = 0.5 * (good + bad)
        if slack(mid) >= 0.0:
            good = mid
        else:
            bad = mid
    return good
