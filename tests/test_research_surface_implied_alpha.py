"""Pinned tests for the implied-alpha fit (``AGENT_TODO.md`` P2).

Every chain here is synthetic, so the right answer is known by construction:
quotes generated FROM a ``ParetanTail`` must give back its ``alpha``, and quotes
generated from something that is not a power law must not be fitted as one.

Spot is 1000, not 100, throughout: prices scale linearly with spot in the
returns basis, and at spot 100 the alpha-4 chain is 0.21 at the 90 anchor and
~0.06 or less deeper -- the quote-hygiene rules (which ``fit_implied_alpha``
applies to every input, synthetic or not) would correctly refuse it.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Sequence

import pandas as pd
import pytest

from tail_lab.research.option_pricer import BlackScholesPricer
from tail_lab.research.surface.implied_alpha import (
    MIN_STRIKE_SPAN,
    MIN_STRIKES,
    MIN_TICK,
    ImpliedAlphaFit,
    _sum_squared_log_miss,
    fit_implied_alpha,
)
from tail_lab.research.surface.ladder import Anchor
from tail_lab.research.surface.paretan import ParetanTail

_QUOTE = dt.date(2026, 9, 25)
_EXPIRY = dt.date(2026, 10, 25)
_SPOT = 1000.0
#: A strike every $5 from 945 down to 695 -- fine enough that each anchor in
#: the invariance test keeps far more than MIN_STRIKES inside its window.
_STRIKES = [float(k) for k in range(945, 690, -5)]

PriceOf = Callable[[float], float]


def _power_law(alpha: float) -> PriceOf:
    """``karamata_l = 0.05``: every anchor used here (<= 920) sits below
    ``(1 - l) * spot = 950``, so it is inside the tail at the true alpha
    (``l = 0.10`` would put a 900 anchor exactly on that boundary -- see P1)."""
    tail = ParetanTail(alpha=alpha, karamata_l=0.05, basis="returns")
    return lambda k: tail.put_price(strike=k, spot=_SPOT)


def _chain(price_of: PriceOf, strikes: Sequence[float], *, spread: float = 0.02) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "strike": k,
                "bid": price_of(k) * (1 - spread),
                "ask": price_of(k) * (1 + spread),
                "quote_date": _QUOTE,
                "expiration": _EXPIRY,
            }
            for k in strikes
        ]
    )


def _anchor(price_of: PriceOf, strike: float, *, underlying: str = "SPY") -> Anchor:
    mid = price_of(strike)
    return Anchor(underlying, _QUOTE, _EXPIRY, _SPOT, strike, mid, mid * 0.98, mid * 1.02)


def _fit_at(
    price_of: PriceOf, anchor_strike: float, strikes: Sequence[float] = _STRIKES
) -> ImpliedAlphaFit:
    quotes = _chain(price_of, [k for k in strikes if k != anchor_strike])
    return fit_implied_alpha(_anchor(price_of, anchor_strike), quotes)


# ---- recovering a known tail ----------------------------------------------------


@pytest.mark.parametrize("alpha", [2.0, 2.75, 4.0])
def test_recovers_the_alpha_that_generated_the_quotes(alpha: float) -> None:
    fit = _fit_at(_power_law(alpha), 900.0)

    assert fit.refusal is None
    assert fit.alpha == pytest.approx(alpha, abs=1e-3)
    assert fit.rmse_log_price == pytest.approx(0.0, abs=1e-6)
    assert fit.strike_span == pytest.approx(0.15)


@pytest.mark.parametrize("alpha", [2.0, 2.75, 4.0])
def test_the_fit_is_anchor_invariant_on_a_true_power_law(alpha: float) -> None:
    """A genuine power law gives the same alpha at every anchor. PX1 relies on
    exactly this: depth-ordered alphas across anchors are the signature of the
    power law FAILING, so the property is pinned here where it is cheapest."""
    fits = [_fit_at(_power_law(alpha), k) for k in (920.0, 900.0, 880.0)]

    alphas = [f.alpha for f in fits if f.alpha is not None]
    assert len(alphas) == len(fits)
    assert max(alphas) - min(alphas) < 1e-6


def test_the_fit_is_deterministic() -> None:
    """``docs/STANDARDS.md``'s bit-for-bit requirement: golden-section search has
    no seed, so two runs on the same inputs are identical floats."""
    first = _fit_at(_power_law(2.75), 900.0)
    second = _fit_at(_power_law(2.75), 900.0)

    assert first == second


# ---- not mistaking something else for a power law ---------------------------------


@pytest.mark.parametrize(
    ("days", "vol", "outcome"),
    [
        (30, 0.35, "refused"),
        (30, 0.55, "dispersed"),
        (60, 0.40, "dispersed"),
        (90, 0.35, "dispersed"),
        (120, 0.30, "dispersed"),
    ],
)
def test_a_thin_tailed_chain_is_not_mistaken_for_a_power_law(
    days: int, vol: float, outcome: str
) -> None:
    """Flat-vol Black-Scholes -- no power law anywhere -- must be either
    REFUSED or visibly NOT anchor-invariant; it must never come back as one
    confident alpha. Each case names which, so neither branch can pass
    vacuously.

    Which one depends on sigma * sqrt(T), not on vol (measured when this was
    written, spot 1000, anchors 920/900/880): at 30 days / 35 % vol no single
    alpha bends a power law fast enough (RMS log miss 0.31-0.42) and every
    anchor is refused; at 90 days / 35 % vol the RMSE ceiling is NOT enough --
    the fit is accepted at alpha ~2.9, right at the paper's headline. What
    gives it away is that the accepted alpha climbs with anchor depth (2.42 ->
    2.88 -> 3.38), where the power-law tests above hold it fixed to 1e-6. This
    is why PX1 reads anchor dispersion, not a single fit.
    """
    pricer = BlackScholesPricer()
    expiry = _QUOTE + dt.timedelta(days=days)

    def black_scholes(k: float) -> float:
        return pricer.price_put(
            spot=_SPOT, strike=k, t_years=days / 365.25, r=0.04, sigma=vol, q=0.019
        )

    def fit_at(anchor_strike: float) -> ImpliedAlphaFit:
        rows = [k for k in _STRIKES if k != anchor_strike]
        quotes = _chain(black_scholes, rows).assign(expiration=expiry)
        mid = black_scholes(anchor_strike)
        anchor = Anchor("SPY", _QUOTE, expiry, _SPOT, anchor_strike, mid, mid * 0.98, mid * 1.02)
        return fit_implied_alpha(anchor, quotes)

    fits = [fit_at(k) for k in (920.0, 900.0, 880.0)]

    if outcome == "refused":
        assert all(f.alpha is None for f in fits)
        assert all(f.refusal is not None and "RMS log-price miss" in f.refusal for f in fits)
        return
    alphas = [f.alpha for f in fits if f.alpha is not None]
    assert len(alphas) == len(fits), "this regime is ACCEPTED by the RMSE ceiling"
    assert alphas == sorted(alphas), "accepted alphas must climb with anchor depth"
    assert max(alphas) - min(alphas) > 0.5, (
        "a thin tail must show anchor dispersion orders of magnitude above the 1e-6 "
        "a true power law gives"
    )


# ---- refusals ---------------------------------------------------------------------


def test_refuses_too_few_strikes() -> None:
    price_of = _power_law(2.75)
    fit = _fit_at(price_of, 900.0, strikes=[900.0, 880.0, 840.0, 780.0])

    assert fit.alpha is None
    assert fit.n_strikes == MIN_STRIKES - 1
    assert fit.refusal is not None and "usable strikes below the anchor" in fit.refusal


def test_refuses_too_narrow_a_span() -> None:
    """Enough strikes, but all within 5 % of the anchor: the fit would be
    measuring the anchor's neighbourhood, not the tail."""
    fit = _fit_at(_power_law(2.75), 900.0, strikes=[float(k) for k in range(900, 849, -5)])

    assert fit.alpha is None
    assert fit.strike_span < MIN_STRIKE_SPAN
    assert fit.refusal is not None and "of spot below the anchor" in fit.refusal


def test_refuses_an_rmse_above_the_ceiling() -> None:
    """A true power law with every other mid pushed 30 % up or down."""
    base = _power_law(2.75)

    def noisy(k: float) -> float:
        return base(k) * (1.3 if int(k) % 10 == 0 else 1 / 1.3)

    quotes = _chain(noisy, [k for k in _STRIKES if k != 900.0])
    fit = fit_implied_alpha(_anchor(base, 900.0), quotes)

    assert fit.alpha is None
    assert fit.refusal is not None and "RMS log-price miss" in fit.refusal


def test_refuses_an_optimum_on_the_bracket_edge() -> None:
    """Quotes from a tail FATTER than the bracket allows (alpha 1.02 < 1.05):
    the best alpha in range is the bracket's floor, which is the bracket
    talking, not the quotes."""
    fit = _fit_at(_power_law(1.02), 900.0)

    assert fit.alpha is None
    assert fit.refusal is not None and "search boundary" in fit.refusal


def test_refuses_a_sub_quarter_anchor() -> None:
    def cheap(k: float) -> float:
        return 0.2 * (k / 900.0) ** 8

    quotes = _chain(lambda k: max(cheap(k), 1.0), [k for k in _STRIKES if k != 900.0])
    fit = fit_implied_alpha(_anchor(cheap, 900.0), quotes)

    assert fit.alpha is None
    assert fit.refusal is not None and "below 0.25" in fit.refusal


def test_refuses_zero_bid_rows() -> None:
    """A zero bid is not a price (marking it at ask/2 biases alpha -0.34):
    such rows are dropped, and a chain whose deep rows are all zero-bid is
    left with too few strikes to fit."""
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [k for k in _STRIKES if k != 900.0])
    quotes.loc[quotes["strike"] < 890.0, "bid"] = 0.0
    fit = fit_implied_alpha(_anchor(price_of, 900.0), quotes)

    assert fit.alpha is None
    assert fit.n_strikes == 2  # only 895 and 890 keep a bid
    assert fit.refusal is not None and "usable strikes" in fit.refusal


def test_a_scattering_of_bad_rows_is_dropped_not_fatal() -> None:
    """Hygiene removes individual rows -- tick-pinned bids, wide spreads, thin
    open interest, unsolved IVs -- and the fit on what survives is unchanged."""
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [k for k in _STRIKES if k != 900.0])
    quotes["open_interest"] = 500
    quotes["iv"] = 0.3
    # Tick-pinned with a TIGHT spread, so only the tick rule can remove it (a
    # wide one would fall to the spread rule and leave the tick rule untested).
    quotes.loc[quotes["strike"] == 880.0, ["bid", "ask"]] = [MIN_TICK, MIN_TICK * 1.1]
    quotes.loc[quotes["strike"] == 870.0, "ask"] *= 3.0  # spread far past 0.50
    quotes.loc[quotes["strike"] == 860.0, "open_interest"] = 3  # below MIN_OPEN_INTEREST
    quotes.loc[quotes["strike"] == 850.0, "iv"] = math.nan  # solver failure

    fit = fit_implied_alpha(_anchor(price_of, 900.0), quotes)
    full = _fit_at(price_of, 900.0)

    assert fit.n_strikes == full.n_strikes - 4
    assert fit.alpha == pytest.approx(2.75, abs=1e-3)


def test_refuses_vix() -> None:
    """VIX's chain spot is the index; its options settle on futures."""
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [k for k in _STRIKES if k != 900.0])
    fit = fit_implied_alpha(_anchor(price_of, 900.0, underlying="VIX"), quotes)

    assert fit.alpha is None
    assert fit.refusal is not None and "settlement basis" in fit.refusal


def test_refuses_when_the_anchor_is_inside_its_tail_for_almost_every_alpha() -> None:
    """An extravagant anchor (the 940 put at 100, spot 1000) calibrates ``l``
    past the anchor for every alpha above ~1.2046 (measured), leaving less than
    ``MIN_BRACKET_WIDTH`` to search. The domain errors ``anchor_l_put`` raises
    across that range must come back as a refusal, never escape."""
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [k for k in _STRIKES if k != 940.0])
    rich = Anchor("SPY", _QUOTE, _EXPIRY, _SPOT, 940.0, 100.0, 98.0, 102.0)

    fit = fit_implied_alpha(rich, quotes)

    assert fit.alpha is None
    assert fit.refusal is not None and "no range left to search" in fit.refusal


def test_refuses_an_anchor_that_fails_hygiene_itself() -> None:
    """The anchor is held to the same rule as the rungs: a mid inside a spread
    wider than MAX_RELATIVE_SPREAD is not a price to extrapolate from."""
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [k for k in _STRIKES if k != 900.0])
    wide = Anchor("SPY", _QUOTE, _EXPIRY, _SPOT, 900.0, 8.0, 4.0, 12.0)

    fit = fit_implied_alpha(wide, quotes)

    assert fit.alpha is None
    assert fit.refusal is not None and "fails hygiene" in fit.refusal


def test_refuses_when_even_alpha_lo_puts_the_anchor_inside_its_tail() -> None:
    """The same extravagant anchor with the bracket moved up to [3, 10]: it is
    invalid at every alpha searched, including the floor."""
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [k for k in _STRIKES if k != 940.0])
    rich = Anchor("SPY", _QUOTE, _EXPIRY, _SPOT, 940.0, 100.0, 98.0, 102.0)

    fit = fit_implied_alpha(rich, quotes, alpha_lo=3.0, alpha_hi=10.0)

    assert fit.alpha is None
    assert fit.refusal is not None and "valid up to None" in fit.refusal


def test_a_zero_paretan_ratio_scores_infinitely_badly_not_as_a_crash() -> None:
    """``put_ratio`` clamps ulp-scale cancellation noise to 0.0 at absurdly deep
    strikes (P1's review reproduced it at strike 1e-7). ``log(0)`` would raise
    out of the objective and turn a refusal into an exception; scoring the
    trial alpha as infinitely bad keeps the search a search. No strike inside
    the 15 % fit window reaches it, so this is exercised directly."""
    anchor = Anchor("SPY", _QUOTE, _EXPIRY, 100.0, 90.0, 1.0, 0.95, 1.05)

    assert _sum_squared_log_miss(anchor, [1e-7], [1.0], 3.0) == math.inf


def test_refuses_an_optimum_on_the_upper_edge_of_the_cut_bracket() -> None:
    """The spec says EITHER edge. A 920 anchor priced at 60 against alpha-4
    quotes: the anchor is valid only up to alpha ~2.2, and the best alpha in
    that range is its ceiling (measured: 2.2035)."""
    price_of = _power_law(4.0)
    quotes = _chain(price_of, [k for k in _STRIKES if k != 920.0])
    rich = Anchor("SPY", _QUOTE, _EXPIRY, _SPOT, 920.0, 60.0, 58.8, 61.2)

    fit = fit_implied_alpha(rich, quotes)

    assert fit.alpha is None
    assert fit.refusal is not None and "search boundary" in fit.refusal


@pytest.mark.parametrize("ask_factor", [1.0, 0.9])
def test_a_locked_or_crossed_quote_is_dropped(ask_factor: float) -> None:
    """A crossed quote has a negative spread and would sail through a
    ``<= MAX_RELATIVE_SPREAD`` check; a locked one is a stale print."""
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [k for k in _STRIKES if k != 900.0])
    bid = float(quotes.loc[quotes["strike"] == 880.0, "bid"].iloc[0])
    quotes.loc[quotes["strike"] == 880.0, "ask"] = bid * ask_factor

    fit = fit_implied_alpha(_anchor(price_of, 900.0), quotes)

    assert fit.n_strikes == _fit_at(price_of, 900.0).n_strikes - 1


def test_the_full_chain_including_the_anchor_strike_is_fine() -> None:
    """A real caller passes the whole chain, anchor strike included; that row
    must not be fitted against itself (strikes strictly below the anchor)."""
    price_of = _power_law(2.75)
    fit = fit_implied_alpha(_anchor(price_of, 900.0), _chain(price_of, _STRIKES))

    assert fit.n_strikes == _fit_at(price_of, 900.0).n_strikes
    assert fit.alpha == pytest.approx(2.75, abs=1e-3)


def test_vix_is_refused_whatever_its_case() -> None:
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [k for k in _STRIKES if k != 900.0])

    fit = fit_implied_alpha(_anchor(price_of, 900.0, underlying="vix"), quotes)

    assert fit.refusal is not None and "settlement basis" in fit.refusal


def test_the_tick_is_the_non_penny_minimum() -> None:
    """Pinned by value: the tests above read MIN_TICK, so a change to it would
    otherwise go unnoticed."""
    assert MIN_TICK == 0.05


def test_a_very_large_alpha_hi_is_searched_not_crashed() -> None:
    """``S0^alpha`` overflows near alpha 100 at spot 1000; that must read as
    'outside the tail', not escape as OverflowError."""
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [k for k in _STRIKES if k != 900.0])

    fit = fit_implied_alpha(_anchor(price_of, 900.0), quotes, alpha_hi=150.0)

    assert fit.alpha == pytest.approx(2.75, abs=1e-3)


# ---- caller bugs raise ------------------------------------------------------------


def test_quotes_from_another_expiry_are_a_caller_bug() -> None:
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [880.0, 860.0])
    quotes.loc[0, "expiration"] = dt.date(2026, 11, 20)

    with pytest.raises(ValueError, match="expiration"):
        fit_implied_alpha(_anchor(price_of, 900.0), quotes)


def test_missing_columns_are_a_caller_bug() -> None:
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [880.0]).drop(columns=["ask"])

    with pytest.raises(ValueError, match="missing columns: ask"):
        fit_implied_alpha(_anchor(price_of, 900.0), quotes)


def test_duplicate_strikes_are_a_caller_bug() -> None:
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [880.0, 880.0])

    with pytest.raises(ValueError, match="duplicate strikes"):
        fit_implied_alpha(_anchor(price_of, 900.0), quotes)


def test_an_inverted_bracket_is_a_caller_bug() -> None:
    price_of = _power_law(2.75)

    with pytest.raises(ValueError, match="finite 1 < alpha_lo < alpha_hi"):
        fit_implied_alpha(
            _anchor(price_of, 900.0), _chain(price_of, [880.0]), alpha_lo=5.0, alpha_hi=2.0
        )


@pytest.mark.parametrize(("lo", "hi"), [(1.0, 5.0), (0.5, 5.0), (1.05, math.inf)])
def test_a_bracket_that_is_not_finite_or_not_above_one_is_a_caller_bug(
    lo: float, hi: float
) -> None:
    """``alpha_hi = inf`` used to hang the domain bisection forever."""
    price_of = _power_law(2.75)

    with pytest.raises(ValueError, match="finite 1 < alpha_lo < alpha_hi"):
        fit_implied_alpha(
            _anchor(price_of, 900.0), _chain(price_of, [880.0]), alpha_lo=lo, alpha_hi=hi
        )


def test_a_missing_quote_date_is_a_caller_bug() -> None:
    price_of = _power_law(2.75)
    quotes = _chain(price_of, [880.0, 860.0])
    quotes["quote_date"] = [_QUOTE, None]

    with pytest.raises(ValueError, match="missing quote_date"):
        fit_implied_alpha(_anchor(price_of, 900.0), quotes)
