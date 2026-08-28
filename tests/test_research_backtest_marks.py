from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tail_lab.research.backtest.marks import (
    MAX_RELATIVE_SPREAD,
    MIN_OPEN_INTEREST,
    mark_schedule,
)
from tail_lab.research.backtest.roll_schedule import RollLeg, RollSchedule

SESSION = pd.Timestamp("2026-08-27")


def _leg(**overrides: object) -> RollLeg:
    base: dict[str, object] = {
        "rank": 1,
        "asset": "kre",
        "name": "Regional Banks (KRE)",
        "moneyness_pct": 8.0,
        "tenor_weeks": 2.0,
        "spot": 75.0,
        "target_strike": 69.0,
        "target_expiry": dt.date(2026, 9, 11),
        "premium_budget": 1000.0,
        "model_premium": 0.002,
    }
    base.update(overrides)
    return RollLeg(**base)  # type: ignore[arg-type]


def _schedule(*legs: RollLeg) -> RollSchedule:
    return RollSchedule(
        schedule_id="abc123",
        as_of=dt.date(2026, 8, 28),
        screen_moneyness_pct=5.0,
        screen_tenor_weeks=4.0,
        lookback_years=4.0,
        universe_size=70,
        top_k=len(legs),
        premium_budget_per_leg=1000.0,
        legs=list(legs) or [_leg()],
    )


def _chain(*rows: dict[str, object]) -> pd.DataFrame:
    base: dict[str, object] = {
        "underlying": "KRE",
        "quote_date": SESSION,
        "expiration": pd.Timestamp("2026-09-11"),
        "strike": 69.0,
        "bid": 1.10,
        "ask": 1.16,
        "volume": 40,
        "open_interest": 500,
        "spot": 75.0,
        "iv": 0.30,
        "delta": -0.20,
        "theo": 1.13,
    }
    return pd.DataFrame([{**base, **r} for r in (rows or [{}])])


# ---- the happy path, and what it is for ------------------------------------


def test_a_liquid_listed_contract_is_quoted_and_sized_from_the_offer() -> None:
    """Contracts come from the ASK, not the mid: you pay the offer when you buy,
    and a size computed off the mid is a size you cannot get filled at."""
    marked = mark_schedule(_schedule(), _chain())
    leg = marked.legs[0]

    assert leg.quote_status == "quoted"
    assert leg.listed_strike == 69.0
    assert leg.listed_expiry == dt.date(2026, 9, 11)
    assert leg.market_mid == pytest.approx(1.13)
    # $1000 budget / ($1.16 x 100 per contract) -> 8 whole contracts.
    assert leg.market_contracts == 8
    assert marked.quoted_legs == 1
    assert marked.quote_session == dt.date(2026, 8, 27)


def test_the_market_to_model_ratio_is_the_measurement_this_exists_for() -> None:
    """The KRE case that motivated the module: model $0.002, market $1.13. Every
    expected_* figure on that leg divides by the model premium, so the ratio is
    roughly the factor by which they are optimistic (docs/adr/0018)."""
    leg = mark_schedule(_schedule(), _chain()).legs[0]
    assert leg.market_to_model_ratio == pytest.approx(1.13 / 0.002)
    assert leg.market_to_model_ratio > 500


def test_the_schedule_id_survives_marking() -> None:
    """Marking says what the market thinks of a recommendation; it does not
    change the recommendation. An executor dedupes on this id, so it must keep
    identifying the same screen output."""
    marked = mark_schedule(_schedule(), _chain())
    assert marked.schedule_id == "abc123"


# ---- the refusals, which are the point -------------------------------------


def test_a_contract_with_no_bid_is_illiquid_however_tight_the_ask() -> None:
    """A zero bid means nobody will take the position back, so the mid is half
    of a number that does not exist. This is the real KRE quote from
    2026-08-27, and it is not a price."""
    marked = mark_schedule(_schedule(), _chain({"bid": 0.0, "ask": 0.21}))
    leg = marked.legs[0]
    assert leg.quote_status == "illiquid"
    assert leg.market_contracts is None
    assert marked.quoted_legs == 0
    # The observed quote is still reported — the refusal is evidenced, not bare.
    assert leg.market_bid == 0.0
    assert leg.market_ask == 0.21


def test_thin_open_interest_is_illiquid() -> None:
    marked = mark_schedule(_schedule(), _chain({"open_interest": MIN_OPEN_INTEREST - 1}))
    assert marked.legs[0].quote_status == "illiquid"


def test_a_spread_wider_than_the_mid_is_not_a_price() -> None:
    """XLE on 2026-08-27: bid 0.01, ask 0.20 — a 190%-of-mid spread. Quoting a
    mid there and sizing from it would invent a fill nobody offered."""
    marked = mark_schedule(_schedule(), _chain({"bid": 0.01, "ask": 0.20}))
    assert marked.legs[0].quote_status == "illiquid"
    # ...and a spread just inside the cap is still tradeable.
    mid, half = 1.0, MAX_RELATIVE_SPREAD / 2 * 0.9
    ok = mark_schedule(_schedule(), _chain({"bid": mid - half, "ask": mid + half}))
    assert ok.legs[0].quote_status == "quoted"


def test_an_uncollected_underlying_says_so_rather_than_looking_priced() -> None:
    """Coverage is 24 of 70 names (docs/adr/0020). A leg on one of the other 46
    is a coverage gap, not a judgement about the trade — and it must not be
    silently indistinguishable from an illiquid one."""
    marked = mark_schedule(_schedule(_leg(asset="jpm")), _chain())
    leg = marked.legs[0]
    assert leg.quote_status == "not_collected"
    assert leg.listed_strike is None and leg.market_bid is None


def test_no_expiry_at_or_after_the_target_is_reported_not_rounded_down() -> None:
    """Rounding an expiry DOWN buys a shorter, cheaper, different option — the
    one direction that quietly flatters the backtest it is compared against."""
    marked = mark_schedule(
        _schedule(_leg(target_expiry=dt.date(2026, 12, 1))),
        _chain({"expiration": pd.Timestamp("2026-09-11")}),
    )
    assert marked.legs[0].quote_status == "no_listed_contract"


# ---- snapping --------------------------------------------------------------


def test_it_snaps_to_the_nearest_listed_strike() -> None:
    marked = mark_schedule(
        _schedule(_leg(target_strike=69.4)),
        _chain({"strike": 68.0}, {"strike": 69.5}, {"strike": 71.0}),
    )
    assert marked.legs[0].listed_strike == 69.5


def test_it_snaps_to_the_first_listed_expiry_at_or_after_the_target() -> None:
    marked = mark_schedule(
        _schedule(_leg(target_expiry=dt.date(2026, 9, 9))),
        _chain(
            {"expiration": pd.Timestamp("2026-09-04")},
            {"expiration": pd.Timestamp("2026-09-11")},
            {"expiration": pd.Timestamp("2026-09-18")},
        ),
    )
    assert marked.legs[0].listed_expiry == dt.date(2026, 9, 11)


def test_an_empty_chain_marks_every_leg_as_uncollected() -> None:
    """The collection has a first day, and a schedule built before it must not
    crash — it must simply report that nothing was priced."""
    empty = _chain().iloc[0:0]
    marked = mark_schedule(_schedule(_leg(), _leg(rank=2, asset="xlf")), empty)
    assert [leg.quote_status for leg in marked.legs] == ["not_collected"] * 2
    assert marked.quoted_legs == 0
    assert marked.quote_session is None
