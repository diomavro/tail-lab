"""Tests for the roll-schedule export (``research/backtest/roll_schedule.py``).

This is the artefact that leaves the platform. ``docs/adr/0007`` draws a wall
between research (here) and execution (a separate process, by hand or
otherwise), and this module is the only thing that crosses it — so what it
says has to be exactly what the backtest measured, and where the backtest was
silent it has to say so rather than invent a number.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tail_lab.research.backtest.ranking import RankedAsset
from tail_lab.research.backtest.roll_schedule import build_roll_schedule

AS_OF = dt.date(2026, 8, 22)


def _ranked(
    asset: str,
    *,
    best_annualized: float | None,
    moneyness: float | None = 8.0,
    tenor: float | None = 4.0,
    spot: float = 100.0,
) -> RankedAsset:
    return RankedAsset(
        asset=asset,
        name=f"{asset.upper()} Inc",
        spot=spot,
        downside_beta=1.0,
        co_skewness=-0.5,
        co_kurtosis=3.0,
        tail_beta=1.0,
        downside_capture=1.0,
        vol_beta=-1.0,
        fragility_score=0.5,
        roi_on_premium=-0.4,
        annualized_return=-0.1,
        verdict="failed",
        hit_rate=0.05,
        biggest_payoff_mult=2.0,
        n_cycles=50,
        best_annualized=best_annualized,
        best_moneyness_pct=moneyness,
        best_tenor_weeks=tenor,
        best_roi_on_premium=None if best_annualized is None else best_annualized * 3,
        best_hit_rate=0.07,
        best_biggest_payoff_mult=3.0,
        best_n_cycles=52,
        best_verdict="confirmed",
    )


def test_the_schedule_is_the_top_k_strategies_in_return_order() -> None:
    ranked = [
        _ranked("aaa", best_annualized=0.10),
        _ranked("bbb", best_annualized=0.90),
        _ranked("ccc", best_annualized=0.50),
    ]

    schedule = build_roll_schedule(ranked, as_of=AS_OF, notional=1000.0, top_k=2, sigma_by_asset={})

    assert [leg.asset for leg in schedule.legs] == ["bbb", "ccc"]
    assert [leg.rank for leg in schedule.legs] == [1, 2]


def test_a_name_with_no_scorable_cell_is_left_out() -> None:
    """Better a shorter schedule than a leg with no parameters to place."""
    ranked = [_ranked("aaa", best_annualized=None), _ranked("bbb", best_annualized=0.2)]

    schedule = build_roll_schedule(ranked, as_of=AS_OF, notional=1000.0, top_k=5, sigma_by_asset={})

    assert [leg.asset for leg in schedule.legs] == ["bbb"]


def test_each_leg_carries_its_own_cell_not_the_screened_one() -> None:
    ranked = [_ranked("aaa", best_annualized=0.4, moneyness=6.0, tenor=12.0, spot=250.0)]

    leg = build_roll_schedule(
        ranked, as_of=AS_OF, notional=1000.0, top_k=1, sigma_by_asset={}
    ).legs[0]

    assert (leg.moneyness_pct, leg.tenor_weeks) == (6.0, 12.0)
    assert leg.action == "BUY_PUT"
    assert leg.target_strike == pytest.approx(250.0 * 0.94)
    assert leg.expected_annualized == pytest.approx(0.4)


def test_the_expiry_is_a_target_the_executor_must_snap() -> None:
    """The backtest counts TRADING days and never models a listed expiry
    calendar, so this is a target date, not a contract that exists. Saying so
    is the difference between an honest export and a silently wrong order."""
    ranked = [_ranked("aaa", best_annualized=0.4, tenor=4.0)]

    schedule = build_roll_schedule(ranked, as_of=AS_OF, notional=1000.0, top_k=1, sigma_by_asset={})

    assert schedule.legs[0].target_expiry == AS_OF + dt.timedelta(weeks=4)
    joined = " ".join(schedule.execution_notes).lower()
    assert "nearest listed" in joined
    assert "trading day" in joined


def test_sizing_is_a_budget_not_a_contract_count_until_a_real_quote_exists() -> None:
    """Every return on this platform divides by the premium BUDGET, and the
    model's premium is not the market's. The executor sizes from the real
    quote; the budget is what must be held constant."""
    ranked = [_ranked("aaa", best_annualized=0.4, spot=100.0)]

    leg = build_roll_schedule(
        ranked, as_of=AS_OF, notional=1000.0, top_k=1, sigma_by_asset={}
    ).legs[0]

    assert leg.premium_budget == 1000.0
    # No volatility supplied -> no model premium claimed, rather than a guess.
    assert leg.model_premium is None
    assert leg.model_contracts is None


def test_the_model_premium_is_quoted_so_the_gap_can_be_measured() -> None:
    """The point of paper-trading these is measuring what the market charges
    against what the model thought. That needs the model's number travelling
    with the order."""
    ranked = [_ranked("aaa", best_annualized=0.4, spot=100.0, moneyness=8.0, tenor=4.0)]

    leg = build_roll_schedule(
        ranked, as_of=AS_OF, notional=1000.0, top_k=1, sigma_by_asset={"aaa": 0.25}
    ).legs[0]

    assert leg.model_premium is not None and leg.model_premium > 0
    # Same contract-sizing rule the backtest uses: at least one contract.
    assert leg.model_contracts == max(1, int(1000.0 // (leg.model_premium * 100)))


def test_the_schedule_id_is_deterministic_and_moves_with_the_content() -> None:
    """The executor dedupes on this: re-fetching the same schedule must not
    place the orders twice, and a changed strategy must not be mistaken for
    one already placed."""
    ranked = [_ranked("aaa", best_annualized=0.4)]
    kwargs = dict(as_of=AS_OF, notional=1000.0, top_k=1, sigma_by_asset={})

    first = build_roll_schedule(ranked, **kwargs)  # type: ignore[arg-type]
    again = build_roll_schedule(ranked, **kwargs)  # type: ignore[arg-type]
    moved = build_roll_schedule(
        [_ranked("aaa", best_annualized=0.4, moneyness=10.0)],
        **kwargs,  # type: ignore[arg-type]
    )

    assert first.schedule_id == again.schedule_id
    assert first.schedule_id != moved.schedule_id


def test_the_schedule_says_what_it_is_and_is_not() -> None:
    """It will be read by a machine that cannot infer context, and by a human
    deciding whether to place it. Both need the caveat attached to the
    artefact, not left behind on the page that generated it."""
    schedule = build_roll_schedule(
        [_ranked("aaa", best_annualized=0.4)],
        as_of=AS_OF,
        notional=1000.0,
        top_k=1,
        sigma_by_asset={},
    )

    basis = schedule.basis.lower()
    assert "in sample" in basis
    assert "model-priced" in basis
    assert "not advice" in basis


def test_a_premium_below_the_minimum_tick_states_no_contract_count() -> None:
    """No US equity option trades below $0.01 per share ($1.00 per contract).
    When the model prices a put under that, its premium is not a price at all —
    and dividing a budget by it yields a position nobody could place (measured:
    XLF at 6% OOM / 2 weeks priced at $0.0010/share, implying 9,874 contracts
    for a $1,000 budget).

    Refusing to state the count is the point. Handing an executor a number that
    cannot be filled is worse than handing it nothing.
    """
    # 2% OOM at one week on a low-vol name -> essentially zero model premium.
    ranked = [_ranked("aaa", best_annualized=0.4, spot=100.0, moneyness=2.0, tenor=1.0)]

    leg = build_roll_schedule(
        ranked, as_of=AS_OF, notional=1000.0, top_k=1, sigma_by_asset={"aaa": 0.03}
    ).legs[0]

    assert leg.model_premium is not None
    assert leg.model_premium < 0.01
    assert leg.model_premium_below_min_tick is True
    assert leg.model_contracts is None


def test_a_normal_premium_is_not_flagged() -> None:
    ranked = [_ranked("aaa", best_annualized=0.4, spot=100.0, moneyness=8.0, tenor=12.0)]

    leg = build_roll_schedule(
        ranked, as_of=AS_OF, notional=1000.0, top_k=1, sigma_by_asset={"aaa": 0.35}
    ).legs[0]

    assert leg.model_premium is not None and leg.model_premium >= 0.01
    assert leg.model_premium_below_min_tick is False
    assert leg.model_contracts is not None and leg.model_contracts >= 1
