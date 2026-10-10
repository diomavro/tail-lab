"""Pinned tests for the assembled Surface (``research/surface/reading.py``)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.surface.paretan import ParetanTail
from tail_lab.research.surface.reading import (
    MAX_RUNGS,
    pick_expiry,
    read_realised,
    read_surface,
    survival_curve,
)

QUOTE = dt.date(2026, 9, 25)
SPOT = 1000.0
R, Q = 0.04, 0.019


def make_chain(alpha: float = 3.0, expiries=(30, 60), underlying: str = "SPY") -> pd.DataFrame:
    tail = ParetanTail(alpha=alpha, karamata_l=0.05, basis="returns")
    rows = []
    for days in expiries:
        for k in range(945, 600, -5):
            p = tail.put_price(strike=float(k), spot=SPOT)
            rows.append(
                {
                    "underlying": underlying,
                    "quote_date": pd.Timestamp(QUOTE),
                    "expiration": pd.Timestamp(QUOTE + dt.timedelta(days=days)),
                    "strike": float(k),
                    "bid": p * 0.98,
                    "ask": p * 1.02,
                    "spot": SPOT,
                    "iv": 0.25,
                }
            )
    return pd.DataFrame(rows)


def random_walk(n: int = 320) -> pd.Series:
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.Series(closes, index=pd.date_range("2025-01-01", periods=n, freq="B"))


def test_survival_curve_is_tie_aware_and_ends_at_zero() -> None:
    curve = survival_curve([1.0, 2.0, 2.0, 3.0])
    assert curve.points == ((1.0, 0.75), (2.0, 0.25), (2.0, 0.25), (3.0, 0.0))


def test_survival_curve_thins_to_the_cap_keeping_both_ends() -> None:
    curve = survival_curve([float(i) for i in range(1000)], max_points=50)
    assert len(curve.points) == 50
    assert curve.points[0][0] == 0.0 and curve.points[-1] == (999.0, 0.0)


def test_pick_expiry_takes_nearest_tenor_and_none_when_empty() -> None:
    chain = make_chain()
    assert pick_expiry(chain, 40) == (QUOTE, QUOTE + dt.timedelta(days=30))
    assert pick_expiry(chain, 50) == (QUOTE, QUOTE + dt.timedelta(days=60))
    assert pick_expiry(chain.iloc[0:0], 30) is None
    expired = chain.assign(expiration=pd.Timestamp(QUOTE))
    assert pick_expiry(expired, 30) is None


def test_surface_recovers_a_planted_power_law_with_zero_dispersion() -> None:
    surface = read_surface(
        make_chain(3.0), random_walk(), underlying="spy", moneyness_pct=7.0, tenor_days=30, r=R, q=Q
    )
    assert surface is not None
    assert surface.t_days == 30 and surface.underlying == "SPY"
    assert "fixed moneyness" in surface.parameterisation
    assert surface.anchors.dispersion is not None and surface.anchors.dispersion < 1e-6
    first = surface.anchors.readings[0].fit
    assert first is not None and first.alpha == pytest.approx(3.0, abs=1e-4)
    assert 0 < len(surface.ladder) <= MAX_RUNGS
    assert all(rung.strike < 930.0 for rung in surface.ladder)
    assert all(
        rung.bid is not None and rung.black_scholes_price is not None for rung in surface.ladder
    )
    # The ladder prices off the planted tail, so Paretan agrees with the market mid.
    assert surface.ladder[0].paretan_price == pytest.approx(
        surface.ladder[0].market_price, rel=0.03
    )
    assert surface.anchor_iv is not None and surface.lambda_guard_ok is True


def test_realised_refusal_is_a_value_and_blocks_the_gap() -> None:
    surface = read_surface(
        make_chain(), random_walk(), underlying="SPY", moneyness_pct=7.0, tenor_days=30, r=R, q=Q
    )
    assert surface is not None and surface.realised is not None
    assert surface.realised.alpha is None and surface.realised.refusal
    assert surface.alpha_gap is None and "no realised alpha" in (surface.alpha_gap_reason or "")
    # Log basis is computed even though the gate refused; ~10 stepped rows -> too few losses.
    assert surface.realised.log_basis.alpha is None
    assert "dismissed" in surface.realised.log_basis.note


def test_log_basis_is_reported_when_the_gate_refuses_with_enough_losses() -> None:
    realised = read_realised(random_walk(2000), horizon_days=1)
    assert realised.refusal is not None
    assert realised.log_basis.alpha is not None and realised.log_basis.k == 100
    assert realised.survival_loss is not None and realised.survival_gross is not None


def test_missing_prices_leave_the_implied_side_standing() -> None:
    surface = read_surface(
        make_chain(), None, underlying="SPY", moneyness_pct=7.0, tenor_days=30, r=R, q=Q
    )
    assert surface is not None and surface.realised is None
    assert (
        surface.alpha_gap is None and surface.alpha_gap_reason == "no OHLCV known as of this date"
    )
    assert surface.anchors.dispersion is not None


def test_unknown_name_returns_none() -> None:
    assert (
        read_surface(
            make_chain(), None, underlying="QQQ", moneyness_pct=7.0, tenor_days=30, r=R, q=Q
        )
        is None
    )


def test_zero_bid_anchor_is_listed_refused_and_no_ladder() -> None:
    chain = make_chain()
    chain.loc[chain["strike"] == 930.0, "bid"] = 0.0
    surface = read_surface(
        chain, None, underlying="SPY", moneyness_pct=7.0, tenor_days=30, r=R, q=Q
    )
    assert surface is not None
    first = surface.anchors.readings[0]
    assert first.fit is None and first.refusal
    assert surface.ladder == () and "no accepted implied alpha" in (surface.ladder_reason or "")
    assert surface.anchor_iv is None and surface.lambda_guard_ok is None


def test_no_strike_below_spot_has_no_anchor_and_no_ladder() -> None:
    chain = make_chain().assign(spot=500.0)
    surface = read_surface(
        chain, None, underlying="SPY", moneyness_pct=7.0, tenor_days=30, r=R, q=Q
    )
    assert surface is not None and surface.anchors.readings == ()
    assert surface.ladder_reason == "no anchor strike below spot on this expiry"
    assert surface.alpha_gap is None


def test_gap_is_implied_minus_realised_when_both_exist() -> None:
    from tail_lab.research.surface import reading as module

    side = module.RealisedSide(
        30, 2.0, 0.1, 40, 0.02, 80, True, None, module.LogBasis(None, None, "x", "n"), None, None
    )
    surface = read_surface(
        make_chain(3.0), None, underlying="SPY", moneyness_pct=7.0, tenor_days=30, r=R, q=Q
    )
    assert surface is not None
    gap, why = module._gap(surface.anchors, side, None)
    assert why is None and gap == pytest.approx(1.0, abs=1e-3)
    assert module._gap(surface.anchors.__class__((), None, None), side, None) == (
        None,
        "no accepted implied alpha at the first anchor",
    )


@pytest.mark.parametrize(
    ("source", "says"),
    [
        ("measured", "from its paid dividends"),
        ("short_history", "scaled up"),
        ("carried", "carried from the data's last day"),
        ("stale", "Tiingo needs a refresh"),
        ("non_payer", "never paid a dividend"),
        ("suspended", "stopped paying"),
    ],
)
def test_the_q_note_says_where_q_came_from_in_words(source: str, says: str) -> None:
    # A reader sees this sentence under the Surface: a raw label ("non_payer")
    # is jargon, and "from its paid dividends" is false for a zero.
    from tail_lab.research.surface.reading import rate_note_for

    note = rate_note_for(source)
    assert says in note and f"({source})" not in note and "_" not in note.split(";")[0]
    if source in ("non_payer", "suspended"):
        assert "paid dividends" not in note
