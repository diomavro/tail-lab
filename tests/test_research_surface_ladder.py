"""Pinned tests for the strike ladder (``AGENT_TODO.md`` P1; ``docs/adr/0026``).

Every expected number here is derivable without this module: the 0.23045 ratio
is ``docs/adr/0026`` §4's own figure, the round trip checks the IV inversion
(the same Black-Scholes pricer, so it verifies the bisection, not the model),
and the "known tail" test builds its market from a
``ParetanTail`` whose parameters the test chooses.
"""

from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import pytest

from tail_lab.research.option_pricer import BlackScholesPricer
from tail_lab.research.skew import IV_MAX, IV_MIN
from tail_lab.research.surface.ladder import Anchor, LadderRung, build_ladder
from tail_lab.research.surface.paretan import ParetanTail

_QUOTE = dt.date(2026, 9, 25)
_EXPIRY = dt.date(2026, 10, 25)  # 30 calendar days
_R, _Q = 0.04, 0.019


def _anchor(*, strike: float = 90.0, price: float = 1.0, spot: float = 100.0) -> Anchor:
    return Anchor(
        underlying="SPY",
        quote_date=_QUOTE,
        expiration=_EXPIRY,
        spot=spot,
        strike=strike,
        price=price,
        bid=price * 0.95,
        ask=price * 1.05,
    )


# ---- the ratio -------------------------------------------------------------------


def test_a_rung_prices_by_the_ratio_to_the_anchor() -> None:
    """``docs/adr/0026`` §4: at ``S0 = 100, alpha = 3`` the correct
    ``P(80)/P(90)`` is 0.23045 (dropping the lambda term would give 0.25000)."""
    ladder = build_ladder(_anchor(price=2.0), alpha=3.0, strikes=[80.0], market_mids={}, r=_R, q=_Q)

    assert ladder[0].paretan_price == pytest.approx(2.0 * 0.23045, abs=1e-5)


def test_rungs_are_sorted_nearest_the_money_first_and_deduplicated() -> None:
    ladder = build_ladder(
        _anchor(), alpha=3.0, strikes=[70.0, 85.0, 80.0, 85.0], market_mids={}, r=_R, q=_Q
    )

    assert [rung.strike for rung in ladder] == [85.0, 80.0, 70.0]


def test_paretan_iv_round_trips_through_black_scholes() -> None:
    """Re-pricing each rung's ``paretan_iv`` recovers ``paretan_price``.

    Tolerance: relative 1e-6. ``skew.implied_vol_put`` bisects sigma on
    ``[IV_MIN, IV_MAX]`` for ``IV_ITERATIONS = 60`` steps, a final bracket of
    ~5 / 2^60 ~ 4e-18 in sigma (in practice the float spacing near the root,
    ~6e-17); times a vega of order 1e-2..1e1 per unit sigma
    that is far below 1e-6 of any price on this ladder, so the bound is
    reachable with orders of magnitude to spare.
    """
    anchor = _anchor(price=1.0)
    ladder = build_ladder(anchor, alpha=3.0, strikes=[85.0, 80.0, 75.0], market_mids={}, r=_R, q=_Q)
    pricer = BlackScholesPricer()

    for rung in ladder:
        assert rung.paretan_iv is not None
        repriced = pricer.price_put(
            spot=anchor.spot,
            strike=rung.strike,
            t_years=anchor.t_years,
            r=_R,
            sigma=rung.paretan_iv,
            q=_Q,
        )
        assert repriced == pytest.approx(rung.paretan_price, rel=1e-6)


def test_prices_are_generated_by_a_known_tail() -> None:
    """A market built FROM a power law, read back through the ladder.

    ``karamata_l`` is fixed at 0.05, not 0.10: with ``l = 0.10`` the 90-strike
    anchor is worth 6.19, and ``anchor_l_put`` at alpha 3.5 raises (the anchor
    would sit inside the calibrated Karamata point), so the second half of this
    test could not build a ladder at all.
    """
    spot = 100.0
    tail = ParetanTail(alpha=2.5, karamata_l=0.05, basis="returns")
    strikes = [85.0, 80.0, 75.0, 70.0, 65.0, 60.0]
    mids = {k: tail.put_price(strike=k, spot=spot) for k in strikes}
    anchor_price = tail.put_price(strike=90.0, spot=spot)
    anchor = _anchor(strike=90.0, price=anchor_price, spot=spot)

    # The model agrees with itself at the alpha that generated the market.
    same = build_ladder(anchor, alpha=2.5, strikes=strikes, market_mids=mids, r=_R, q=_Q)
    for rung in same:
        assert rung.paretan_price == pytest.approx(mids[rung.strike], rel=1e-12)
        assert rung.iv_ratio == pytest.approx(1.0, abs=1e-6)

    # A thinner tail prices every deeper strike cheaper. `put_ratio(90->80)` at
    # S0 = 100 is 0.30678 / 0.23045 / 0.16903 for alpha 2.5 / 3.0 / 3.5.
    thinner = build_ladder(anchor, alpha=3.5, strikes=strikes, market_mids=mids, r=_R, q=_Q)
    assert all(rung.iv_ratio is not None for rung in thinner), (
        "every strike chosen must invert inside [IV_MIN, IV_MAX]; if one does not, "
        "pick strikes that do rather than weakening the assertion below"
    )
    assert all(rung.iv_ratio is not None and rung.iv_ratio < 1.0 for rung in thinner)
    assert all(
        rung.paretan_iv is not None and IV_MIN < rung.paretan_iv < IV_MAX for rung in thinner
    )


# ---- refusals ----------------------------------------------------------------------


def test_an_anchor_outside_the_tail_domain_raises() -> None:
    """``anchor_l_put`` is the only domain check. A 6.19 anchor at 90/100
    calibrates ``l`` so large at alpha 3.5 that the anchor sits inside the
    Karamata point -- nothing extrapolated from it would be valid."""
    with pytest.raises(ValueError, match="Karamata point"):
        build_ladder(_anchor(price=6.19), alpha=3.5, strikes=[80.0], market_mids={}, r=_R, q=_Q)


@pytest.mark.parametrize("strike", [90.0, 95.0])
def test_the_ladder_refuses_a_strike_at_or_above_the_anchor(strike: float) -> None:
    with pytest.raises(ValueError, match="at or above the anchor strike"):
        build_ladder(_anchor(), alpha=3.0, strikes=[80.0, strike], market_mids={}, r=_R, q=_Q)


@pytest.mark.parametrize(
    ("bid", "price", "ask"),
    [
        (0.0, 0.5, 1.0),  # zero bid: not a price
        (1.1, 1.0, 1.2),  # crossed: bid above the mid
        (0.9, 1.3, 1.2),  # price above the ask
    ],
)
def test_the_anchor_refuses_a_crossed_or_zero_bid_quote(
    bid: float, price: float, ask: float
) -> None:
    with pytest.raises(ValueError, match="0 < bid <= price <= ask"):
        Anchor(
            underlying="SPY",
            quote_date=_QUOTE,
            expiration=_EXPIRY,
            spot=100.0,
            strike=90.0,
            price=price,
            bid=bid,
            ask=ask,
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("strike", 100.0, "downside put"),
        ("strike", 0.0, "downside put"),
        ("spot", math.nan, "finite"),
        ("strike", math.inf, "finite"),
        ("price", math.nan, "finite"),
        ("bid", math.nan, "finite"),
        ("ask", math.inf, "finite"),
        ("quote_date", dt.datetime(2026, 9, 25, 23, 0), "not a datetime"),
        ("expiration", dt.datetime(2026, 10, 25, 1, 0), "not a datetime"),
        ("expiration", _QUOTE, "must follow its quote date"),
        ("underlying", "", "must be named"),
    ],
)
def test_the_anchor_refuses_malformed_fields(field: str, value: object, match: str) -> None:
    kwargs: dict[str, object] = {
        "underlying": "SPY",
        "quote_date": _QUOTE,
        "expiration": _EXPIRY,
        "spot": 100.0,
        "strike": 90.0,
        "price": 1.0,
        "bid": 0.95,
        "ask": 1.05,
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=match):
        Anchor(**kwargs)  # type: ignore[arg-type]  # deliberately ill-typed: this tests runtime validation


@pytest.mark.parametrize("mid", [0.0, -1.0, math.inf, math.nan, True])
def test_a_market_mid_that_is_not_a_price_raises(mid: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        build_ladder(_anchor(), alpha=3.0, strikes=[80.0], market_mids={80.0: mid}, r=_R, q=_Q)


# ---- missing and refused data --------------------------------------------------------


def test_a_strike_with_no_quote_gets_a_rung_without_market_fields() -> None:
    ladder = build_ladder(
        _anchor(), alpha=3.0, strikes=[80.0, 75.0], market_mids={80.0: 0.3}, r=_R, q=_Q
    )
    by_strike = {rung.strike: rung for rung in ladder}

    unquoted = by_strike[75.0]
    assert unquoted == LadderRung(
        strike=75.0,
        paretan_price=unquoted.paretan_price,
        market_price=None,
        paretan_iv=unquoted.paretan_iv,
        market_iv=None,
        iv_ratio=None,
    )
    assert unquoted.paretan_price > 0.0 and unquoted.paretan_iv is not None
    assert by_strike[80.0].market_price == 0.3 and by_strike[80.0].iv_ratio is not None


def test_an_unreachable_market_price_is_reported_not_clamped() -> None:
    """A market mid above what ``IV_MAX`` can produce inverts to ``None``
    (``skew.implied_vol_put``), and the ratio with it -- a fabricated 500 %
    vol must never enter a ratio."""
    ladder = build_ladder(
        _anchor(), alpha=3.0, strikes=[80.0], market_mids={80.0: 79.0}, r=_R, q=_Q
    )

    assert ladder[0].market_iv is None
    assert ladder[0].iv_ratio is None
    assert ladder[0].paretan_iv is not None


def test_t_years_uses_the_skew_day_count() -> None:
    from tail_lab.research.backtest.index_replication import DAYS_PER_YEAR

    assert _anchor().t_years == pytest.approx(30 / DAYS_PER_YEAR)


def test_the_anchor_domain_is_checked_before_anything_else() -> None:
    """The spec's order: ``anchor_l_put`` first. A bad anchor with a shallow
    strike AND a non-finite rate must fail on the anchor."""
    with pytest.raises(ValueError, match="Karamata point"):
        build_ladder(
            _anchor(price=6.19), alpha=3.5, strikes=[95.0], market_mids={}, r=math.nan, q=_Q
        )


@pytest.mark.parametrize(("r", "q"), [(math.nan, _Q), (_R, math.inf)])
def test_a_non_finite_rate_or_yield_raises(r: float, q: float) -> None:
    with pytest.raises(ValueError, match="r and q must be finite"):
        build_ladder(_anchor(), alpha=3.0, strikes=[80.0], market_mids={}, r=r, q=q)


def test_a_zero_paretan_price_is_refused_not_given_the_floor_vol() -> None:
    """At absurdly deep strikes ``put_ratio`` clamps cancellation noise to 0.0,
    and inverting 0.0 returns ``IV_MIN`` -- a fabricated vol. It must come back
    ``None`` (adversarial review: strike 1e-7 at spot 100 reproduced it)."""
    ladder = build_ladder(
        _anchor(), alpha=3.0, strikes=[1e-7], market_mids={1e-7: 1e-300}, r=_R, q=_Q
    )

    assert ladder[0].paretan_price == 0.0
    assert ladder[0].paretan_iv is None
    assert ladder[0].iv_ratio is None


def test_two_datetimes_are_refused_even_though_they_would_compare() -> None:
    """The case the guard exists for: both fields as datetimes (e.g. pandas
    Timestamps from a quote row) would validate and floor the day count."""
    with pytest.raises(ValueError, match="not a datetime"):
        Anchor(
            underlying="SPY",
            quote_date=pd.Timestamp("2026-09-25 23:00"),
            expiration=pd.Timestamp("2026-10-25 01:00"),
            spot=100.0,
            strike=90.0,
            price=1.0,
            bid=0.95,
            ask=1.05,
        )
