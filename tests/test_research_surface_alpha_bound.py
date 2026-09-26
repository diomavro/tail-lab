"""Pinned tests for the no-arbitrage ceiling on alpha (``AGENT_TODO.md`` P3).

The market side is a Black-Scholes smile that is linear in strike around the
anchor, ``sigma(K) = sigma_1 + s (K - K_1)``, so every slope has a closed form
and a finite difference to check it against. Spot is 1000, as in the P2 tests.
"""

from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import pytest

from tail_lab.research.option_pricer import VEGA_PER_VOL_POINT, BlackScholesPricer
from tail_lab.research.skew import implied_vol_put
from tail_lab.research.surface.alpha_bound import (
    AlphaCeiling,
    alpha_upper_bound,
    market_put_slope,
    paretan_put_slope,
)
from tail_lab.research.surface.implied_alpha import _valid_upper_bound, fit_implied_alpha
from tail_lab.research.surface.ladder import Anchor
from tail_lab.research.surface.paretan import ParetanTail, put_ratio

_QUOTE = dt.date(2026, 9, 25)
_EXPIRY = dt.date(2026, 10, 25)  # 30 days
_SPOT = 1000.0
_BS = BlackScholesPricer()


def _t() -> float:
    return _anchor(900.0, 1.0).t_years


def _anchor(strike: float, price: float, *, expiry: dt.date = _EXPIRY) -> Anchor:
    return Anchor("SPY", _QUOTE, expiry, _SPOT, strike, price, price * 0.98, price * 1.02)


def _smile_anchor(strike: float, sigma: float, *, r: float = 0.0, q: float = 0.0) -> Anchor:
    price = _BS.price_put(spot=_SPOT, strike=strike, t_years=_t(), sigma=sigma, r=r, q=q)
    return _anchor(strike, price)


def _smile_price(
    anchor: Anchor, *, sigma: float, slope: float, strike: float, r: float, q: float
) -> float:
    vol = sigma + slope * (strike - anchor.strike)
    return _BS.price_put(spot=_SPOT, strike=strike, t_years=anchor.t_years, sigma=vol, r=r, q=q)


# ---- the claim itself: the splice is convex up to the ceiling and not beyond -----


@pytest.mark.parametrize(
    ("strike", "sigma", "slope", "r", "q"),
    [
        (900.0, 0.25, -0.001, 0.0, 0.0),
        (850.0, 0.35, -0.0008, 0.0, 0.0),
        (900.0, 0.20, -0.0005, 0.045, 0.013),
    ],
)
def test_the_splice_is_convex_below_the_ceiling_and_arbitrage_above_it(
    strike: float, sigma: float, slope: float, r: float, q: float
) -> None:
    """The whole point of the bound: price a butterfly centred on the anchor,
    with the Paretan tail on its lower wing and the market's smile on its
    upper. Just below the ceiling it costs something; just above it is paid
    to hold a position that cannot lose -- arbitrage. This is also what fixes
    the DIRECTION: the paper's "lower bound" assumes a fixed ``l``, and with
    ``l`` calibrated from the anchor the inequality caps alpha from above."""
    anchor = _smile_anchor(strike, sigma, r=r, q=q)
    ceiling = alpha_upper_bound(anchor, smile_slope=slope, r=r, q=q)
    assert ceiling.alpha is not None, ceiling.refusal
    h = strike * 1e-4

    def butterfly(alpha: float) -> float:
        lower = anchor.price * put_ratio(k_from=strike, k_to=strike - h, spot=_SPOT, alpha=alpha)
        upper = _smile_price(anchor, sigma=sigma, slope=slope, strike=strike + h, r=r, q=q)
        return lower + upper - 2.0 * anchor.price

    assert butterfly(ceiling.alpha - 0.05) > 0.0
    assert butterfly(ceiling.alpha + 0.05) < 0.0


def test_a_market_that_is_a_power_law_sits_exactly_on_its_own_ceiling() -> None:
    """If the market's density below the anchor really is Paretan with index
    3, the market's slope at the anchor IS the power law's, the inequality
    holds with equality, and the ceiling is 3 -- recovered from one price and
    one slope, independently of P2's fit over many strikes."""
    tail = ParetanTail(alpha=3.0, karamata_l=0.05, basis="returns")
    strike = 900.0
    anchor = _anchor(strike, tail.put_price(strike=strike, spot=_SPOT))
    h = 1e-3
    target = (
        tail.put_price(strike=strike + h, spot=_SPOT)
        - tail.put_price(strike=strike - h, spot=_SPOT)
    ) / (2.0 * h)
    # Choose the smile slope that makes the market's slope equal the tail's.
    sigma = _implied(anchor)
    greeks = _BS.greeks_put(
        spot=_SPOT, strike=strike, t_years=anchor.t_years, sigma=sigma, r=0.0, q=0.0
    )
    smile_slope = (target - greeks.itm_prob) / (greeks.vega / VEGA_PER_VOL_POINT)

    ceiling = alpha_upper_bound(anchor, smile_slope=smile_slope, r=0.0, q=0.0)

    assert ceiling.alpha == pytest.approx(3.0, abs=1e-5)


def _implied(anchor: Anchor) -> float:
    sigma = implied_vol_put(
        anchor.price, spot=_SPOT, strike=anchor.strike, t_years=anchor.t_years, r=0.0, q=0.0
    )
    assert sigma is not None
    return sigma


def test_the_ceiling_is_not_a_bound_on_the_p2_fit() -> None:
    """On a thin-tailed smile P2 fits the decay of the deeper prices and lands
    ABOVE the ceiling, which reads one slope at the anchor. Anyone treating the
    ceiling as a rail on P2 would reject every such fit; the gap is instead the
    sign that P2's ladder, spliced at the anchor, is not convex against the
    smile -- i.e. that the chain is not a power law."""
    sigma, slope = 0.25, -0.001
    anchor = _smile_anchor(900.0, sigma)
    rows = []
    for strike in (900.0 - 10.0 * i for i in range(1, 31)):
        mid = _smile_price(anchor, sigma=sigma, slope=slope, strike=strike, r=0.0, q=0.0)
        if mid > 0.05:
            rows.append(
                {
                    "strike": strike,
                    "bid": mid * 0.99,
                    "ask": mid * 1.01,
                    "quote_date": _QUOTE,
                    "expiration": _EXPIRY,
                    "open_interest": 1000,
                }
            )
    fit = fit_implied_alpha(anchor, pd.DataFrame(rows))
    ceiling = alpha_upper_bound(anchor, smile_slope=slope, r=0.0, q=0.0)

    assert fit.alpha is not None, fit.refusal
    assert ceiling.alpha is not None
    assert fit.alpha == pytest.approx(3.343, abs=1e-3)
    assert fit.alpha > ceiling.alpha + 0.5


# ---- the two slopes, each against a finite difference ----------------------------


@pytest.mark.parametrize(("r", "q"), [(0.0, 0.0), (0.045, 0.013)])
def test_the_market_slope_is_the_total_derivative_along_the_smile(r: float, q: float) -> None:
    sigma, slope = 0.25, -0.001
    anchor = _smile_anchor(900.0, sigma, r=r, q=q)
    h = 1e-3
    finite = (
        _smile_price(anchor, sigma=sigma, slope=slope, strike=900.0 + h, r=r, q=q)
        - _smile_price(anchor, sigma=sigma, slope=slope, strike=900.0 - h, r=r, q=q)
    ) / (2.0 * h)

    assert market_put_slope(anchor, sigma=sigma, smile_slope=slope, r=r, q=q) == pytest.approx(
        finite, rel=1e-6
    )


def test_vega_is_per_unit_sigma_not_per_vol_point() -> None:
    """``PutGreeks.vega`` is per vol POINT. Wired in as-is, the smile term is
    100x too small -- here that is the difference between a slope the skew
    cuts almost in half (0.0757 -> 0.0389) and one it barely touches."""
    sigma, slope = 0.25, -0.001
    anchor = _smile_anchor(900.0, sigma)
    greeks = _BS.greeks_put(
        spot=_SPOT, strike=900.0, t_years=anchor.t_years, sigma=sigma, r=0.0, q=0.0
    )
    flat = market_put_slope(anchor, sigma=sigma, smile_slope=0.0, r=0.0, q=0.0)
    skewed = market_put_slope(anchor, sigma=sigma, smile_slope=slope, r=0.0, q=0.0)

    assert skewed - flat == pytest.approx(greeks.vega * 100.0 * slope, rel=1e-12)
    assert flat == pytest.approx(0.0757, abs=1e-4)
    assert skewed == pytest.approx(0.0389, abs=1e-4)


def test_the_paretan_slope_is_the_ladders_own_derivative_at_the_anchor() -> None:
    anchor = _smile_anchor(900.0, 0.25)
    h = 1e-3
    for alpha in (1.5, 2.5, 4.0):
        finite = (
            anchor.price
            * (
                put_ratio(k_from=900.0, k_to=900.0 + h, spot=_SPOT, alpha=alpha)
                - put_ratio(k_from=900.0, k_to=900.0 - h, spot=_SPOT, alpha=alpha)
            )
            / (2.0 * h)
        )
        assert paretan_put_slope(anchor, alpha) == pytest.approx(finite, rel=1e-5)


def test_the_ceiling_matches_an_independent_implementation() -> None:
    """Pinned against a separate erfc-based computation (spot 100, anchor 90,
    smile slope -0.01 per $): 2.7472. At spot 1000 the same market has slope
    -0.001 per $ and, the model being scale-free, the same ceiling."""
    ceiling = alpha_upper_bound(_smile_anchor(900.0, 0.25), smile_slope=-0.001, r=0.0, q=0.0)

    assert ceiling.alpha == pytest.approx(2.7472, abs=1e-4)
    assert ceiling.refusal is None
    assert ceiling.market_slope is not None
    assert (
        ceiling.market_slope - paretan_put_slope(_smile_anchor(900.0, 0.25), ceiling.alpha) >= 0.0
    )


def test_a_steeper_skew_lowers_the_ceiling() -> None:
    """A more negative smile slope means the market puts LESS probability just
    below the anchor for the same anchor price -- its value sits deeper -- which
    only a fatter tail (a lower alpha) can match. So the ceiling must fall
    strictly as the skew steepens."""
    anchor = _smile_anchor(900.0, 0.25)
    ceilings = [
        alpha_upper_bound(anchor, smile_slope=s, r=0.0, q=0.0).alpha
        for s in (0.0, -0.0003, -0.0006, -0.0009, -0.0012)
    ]

    assert all(c is not None for c in ceilings)
    assert ceilings == sorted(ceilings, reverse=True)
    assert len(set(ceilings)) == len(ceilings)


# ---- refusals ----------------------------------------------------------------------


def test_no_ceiling_inside_the_bracket_is_a_refusal_not_the_bracket_edge() -> None:
    """A steeply rising smile puts so much probability below the anchor that
    every alpha up to 10 stays convex (at +0.002 the ceiling is still 8.01; by
    +0.004 it has left the bracket). Returning 10 would report the search
    range as if it were a measurement."""
    ceiling = alpha_upper_bound(_smile_anchor(900.0, 0.25), smile_slope=0.004, r=0.0, q=0.0)

    assert ceiling.alpha is None
    assert ceiling.refusal is not None and "no ceiling" in ceiling.refusal
    assert ceiling.market_slope is not None


def test_a_skew_steep_enough_to_beat_every_tail_is_a_refusal() -> None:
    """A legitimate smile (market slope 0.0058, a real probability) that still
    puts less mass below the anchor than even the fattest tail in the bracket
    (0.0141 at alpha 1.05)."""
    ceiling = alpha_upper_bound(_smile_anchor(900.0, 0.25), smile_slope=-0.0019, r=0.0, q=0.0)

    assert ceiling.alpha is None
    assert ceiling.market_slope == pytest.approx(0.00583, abs=1e-5)
    assert ceiling.refusal is not None and "non-convex" in ceiling.refusal


@pytest.mark.parametrize("slope", [-0.1, 0.1])
def test_an_arbitrageable_smile_is_refused_as_the_smiles_fault(slope: float) -> None:
    """At these slopes the market's own "probability below the anchor" is
    -3.6 or +3.75. Reporting that as a tail that failed (or passed) would
    put the blame on the wrong curve."""
    ceiling = alpha_upper_bound(_smile_anchor(900.0, 0.25), smile_slope=slope, r=0.0, q=0.0)

    assert ceiling.alpha is None
    assert ceiling.refusal is not None and "smile itself is arbitrageable" in ceiling.refusal


def test_the_search_is_cut_to_where_the_anchor_is_inside_its_tail() -> None:
    """A 5 %-OTM anchor is inside its own calibrated tail only up to alpha
    6.15. Searching to the default 10 would call anchor_l_put where it raises;
    the ceiling must come from the cut bracket instead."""
    anchor = _smile_anchor(950.0, 0.25)
    ceiling = alpha_upper_bound(anchor, smile_slope=-0.001, r=0.0, q=0.0)

    assert _valid_upper_bound(anchor, alpha_lo=1.05, alpha_hi=10.0) == pytest.approx(
        6.1502, abs=1e-4
    )
    assert ceiling.alpha == pytest.approx(1.6437, abs=1e-4)


def test_an_anchor_with_no_implied_vol_is_a_refusal() -> None:
    """A put priced above its discounted strike has no Black-Scholes vol."""
    ceiling = alpha_upper_bound(_anchor(900.0, 950.0), smile_slope=0.0, r=0.0, q=0.0)

    assert ceiling == AlphaCeiling(ceiling.anchor, None, None, ceiling.refusal)
    assert ceiling.refusal is not None and "implied vol" in ceiling.refusal


def test_an_anchor_outside_its_own_tail_at_alpha_lo_is_a_refusal() -> None:
    """A near-the-money anchor with a rich price calibrates an ``l`` so large
    that the anchor sits inside the Karamata point at every alpha."""
    anchor = _smile_anchor(990.0, 0.60)
    ceiling = alpha_upper_bound(anchor, smile_slope=-0.001, r=0.0, q=0.0)

    assert ceiling.alpha is None
    assert ceiling.refusal is not None and "outside its own calibrated tail" in ceiling.refusal
    assert ceiling.market_slope is not None  # only a missing IV leaves it None


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"smile_slope": math.nan}, "smile_slope"),
        ({"smile_slope": 0.0, "alpha_lo": 3.0, "alpha_hi": 2.0}, "alpha_lo"),
        ({"smile_slope": 0.0, "alpha_lo": 1.0}, "alpha_lo"),
        ({"smile_slope": 0.0, "alpha_hi": math.inf}, "alpha_lo"),
    ],
)
def test_a_caller_bug_raises(kwargs: dict[str, float], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        alpha_upper_bound(_smile_anchor(900.0, 0.25), r=0.0, q=0.0, **kwargs)


@pytest.mark.parametrize("field", ["r", "q"])
def test_a_non_finite_rate_is_a_caller_bug_not_a_missing_iv(field: str) -> None:
    rates = {"r": 0.0, "q": 0.0, field: math.nan}
    with pytest.raises(ValueError, match=f"{field} must be finite"):
        alpha_upper_bound(_smile_anchor(900.0, 0.25), smile_slope=0.0, **rates)


def test_the_anchor_level_refusal_wins_over_the_smile_level_one() -> None:
    """The anchor-outside-its-tail refusal does not depend on smile_slope; the
    smile check does. Checked in the other order, a noisy slope estimate would
    flip which reason P4 shows for an anchor that is unusable either way."""
    ceiling = alpha_upper_bound(_smile_anchor(990.0, 0.60), smile_slope=-0.1, r=0.0, q=0.0)

    assert ceiling.refusal is not None and "outside its own calibrated tail" in ceiling.refusal


def test_a_small_but_legitimate_market_slope_is_not_called_arbitrage() -> None:
    """A 28 %-OTM 30-day anchor puts only 0.00027 % probability below itself
    -- tiny, but a probability. The smile check must bound at exactly 0, not
    at some small positive number that would refuse deep anchors."""
    ceiling = alpha_upper_bound(_smile_anchor(720.0, 0.25), smile_slope=0.0, r=0.0, q=0.0)

    assert ceiling.market_slope == pytest.approx(2.6934e-6, rel=1e-3)
    assert ceiling.refusal is not None and "arbitrageable" not in ceiling.refusal


def test_a_slope_above_the_discount_factor_is_arbitrage_even_below_one() -> None:
    """At r = 4.5 % over 30 days a put spread's value is capped at e^(-rT) =
    0.99631 per unit width. A slope of 0.999 is below 1 but still above that
    cap, so the upper bound must be the discount factor, not 1."""
    anchor = _smile_anchor(900.0, 0.25, r=0.045)
    ceiling = alpha_upper_bound(anchor, smile_slope=0.0273945, r=0.045, q=0.0)

    assert ceiling.market_slope == pytest.approx(0.999, abs=1e-6)
    assert ceiling.refusal is not None and "smile itself is arbitrageable" in ceiling.refusal
