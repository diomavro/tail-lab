"""The real-quote study behind the Pitch tab (scripts/pitch_evidence.py).

The Pitch's verdict -- "no demonstrable edge" -- is read straight off these
numbers, so each test pins an arithmetic step whose failure would move that
verdict: a roll that lost less than its premium, a hedge that looked free, a
gate that looked selective because it skipped rolls with no reading, or a
bootstrap p-value of exactly zero clearing a 408-way Benjamini-Hochberg line
it cannot honestly clear.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.backtest.brokerage import COMMISSION_PER_CONTRACT


def _load() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "pitch_evidence.py"
    spec = importlib.util.spec_from_file_location("pitch_evidence", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["pitch_evidence"] = module
    spec.loader.exec_module(module)
    return module


pe = _load()

SESSIONS = pd.bdate_range("2020-01-06", periods=25)


def _panel(spots: list[float], *, bid: float = 0.90, ask: float = 1.00) -> pd.DataFrame:
    """One listed put (strike 95, expiry the 21st session) quoted on every
    session at a fixed bid/ask, with the given spot path."""
    expiry = SESSIONS[20]
    return pd.DataFrame(
        {
            "underlying": "SPY",
            "quote_date": SESSIONS[: len(spots)],
            "expiration": expiry,
            "strike": 95.0,
            "bid": bid,
            "ask": ask,
            "spot": spots,
            "iv": 0.2,
        }
    )


def test_a_held_put_returns_intrinsic_over_the_ask_plus_commission() -> None:
    # Spot 100 -> 90 at expiry: the 95 put is worth 5/share. One unit of budget
    # buys 1 / (100 * 1.00 + 0.65) contracts, settled without a commission.
    spots = [100.0] * 20 + [90.0] * 5
    mkt = pe.market_from_panel(_panel(spots), "SPY")
    rolls, skips = pe.run_rolls(
        mkt, moneyness=5.0, tenor_weeks=4.0, hold_weeks=None, start="2020-01-06", end="2020-01-06"
    )
    assert skips == 0
    assert len(rolls) == 1
    expected = 500.0 / (100.0 + COMMISSION_PER_CONTRACT) - 1.0
    assert rolls[0].roi == pytest.approx(expected)
    assert rolls[0].exit == SESSIONS[20]


def test_an_expiring_worthless_put_loses_exactly_its_premium() -> None:
    mkt = pe.market_from_panel(_panel([100.0] * 25), "SPY")
    rolls, _ = pe.run_rolls(
        mkt, moneyness=5.0, tenor_weeks=4.0, hold_weeks=None, start="2020-01-06", end="2020-01-06"
    )
    assert rolls[0].roi == -1.0
    # The daily path starts at one unit less the commission and ends at zero.
    assert rolls[0].path.iloc[0] == pytest.approx(1.0 - COMMISSION_PER_CONTRACT / 100.65)
    assert rolls[0].path.iloc[-1] == 0.0


def test_a_sold_put_exits_at_the_bid_and_pays_commission() -> None:
    mkt = pe.market_from_panel(_panel([100.0] * 25, bid=0.90, ask=1.00), "SPY")
    rolls, _ = pe.run_rolls(
        mkt, moneyness=5.0, tenor_weeks=4.0, hold_weeks=2.0, start="2020-01-06", end="2020-01-06"
    )
    c = 1.0 / (100.0 + COMMISSION_PER_CONTRACT)
    assert rolls[0].roi == pytest.approx(c * 90.0 - c * COMMISSION_PER_CONTRACT - 1.0)


def test_a_refused_fill_is_counted_and_the_next_session_tried() -> None:
    # Bid 0 on the first session fails the liquidity guard; the second fills.
    panel = _panel([100.0] * 25)
    panel.loc[0, "bid"] = 0.0
    mkt = pe.market_from_panel(panel, "SPY")
    # 3 weeks, so the 2020-02-03 expiry is still at-or-after the target a day later.
    rolls, skips = pe.run_rolls(
        mkt, moneyness=5.0, tenor_weeks=3.0, hold_weeks=None, start="2020-01-06", end="2020-01-07"
    )
    assert skips == 1
    assert rolls[0].entry == SESSIONS[1]


def test_a_zero_budget_book_is_the_unhedged_book() -> None:
    spots = list(np.linspace(100, 110, 25))
    mkt = pe.market_from_panel(_panel(spots), "SPY")
    rolls, _ = pe.run_rolls(
        mkt, moneyness=5.0, tenor_weeks=4.0, hold_weeks=None, start="2020-01-06", end="2020-01-06"
    )
    w = pe.book(mkt, rolls, budget=0.0)
    w0 = pe.unhedged(mkt, SESSIONS[0], SESSIONS[20])
    pd.testing.assert_series_equal(w, w0, check_freq=False)
    years = (SESSIONS[20] - SESSIONS[0]).days / 365.25
    assert w.iloc[-1] == pytest.approx(spots[20] / 100 * math.exp(pe.DIV_YIELD * years))


def test_a_worthless_hedge_costs_its_budget_and_nothing_else() -> None:
    mkt = pe.market_from_panel(_panel([100.0] * 25), "SPY")
    rolls, _ = pe.run_rolls(
        mkt, moneyness=5.0, tenor_weeks=4.0, hold_weeks=None, start="2020-01-06", end="2020-01-06"
    )
    w = pe.book(mkt, rolls, budget=0.01)
    w0 = pe.unhedged(mkt, SESSIONS[0], SESSIONS[20])
    assert w.iloc[-1] == pytest.approx(0.99 * w0.iloc[-1])


def test_dropping_the_best_roll_removes_its_contribution() -> None:
    mkt = pe.market_from_panel(_panel([100.0] * 20 + [80.0] * 5), "SPY")
    rolls, _ = pe.run_rolls(
        mkt, moneyness=5.0, tenor_weeks=4.0, hold_weeks=None, start="2020-01-06", end="2020-01-06"
    )
    out = pe.drop_best_roll_growth(rolls, 0.01)
    assert out["edge"] > 0  # a 15/share payoff on a 1.00 put
    assert out["edge_without_best"] == 0.0  # nothing left
    assert out["best_entry"] == "2020-01-06"


def test_bootstrap_p_is_never_zero() -> None:
    # 2000 positive draws: the honest p is "below 1/2001", not 0. A 0 would
    # clear a Benjamini-Hochberg line of 0.10/408 that 1/2001 does not.
    p = pe.bootstrap_p(np.ones(2000))
    assert p == pytest.approx(1 / 2001)
    assert p > 0.10 / 408
    assert pe.bootstrap_p(-np.ones(9)) == 1.0


def test_block_bootstrap_keeps_neighbours_together() -> None:
    idx = pe.block_bootstrap_idx(10, 3, np.random.default_rng(0))
    assert len(idx) == 10
    # Within each block of three, indices are consecutive (mod n).
    for k in range(0, 9, 3):
        assert (idx[k + 1] - idx[k]) % 10 == 1
        assert (idx[k + 2] - idx[k + 1]) % 10 == 1


def test_a_gate_never_takes_a_roll_it_has_no_reading_for() -> None:
    sig = pd.DataFrame({"alpha": [1.0, np.nan, 3.0], "regime": ["calm", None, "crisis"]})
    assert pe.apply_gate(sig, ("alpha", "le", 5.0)).tolist() == [True, False, True]
    assert pe.apply_gate(sig, ("alpha", "ge", 2.0)).tolist() == [False, False, True]
    assert pe.apply_gate(sig, ("regime", "in", frozenset({"calm"}))).tolist() == [
        True,
        False,
        False,
    ]


def test_roi_diff_rewards_picking_better_rolls_not_buying_fewer() -> None:
    roi = np.array([-1.0, -1.0, 3.0, -1.0])
    # Taking only the losers "saves" premium but picks worse rolls.
    assert pe.roi_diff(roi, np.array([True, True, False, True])) == pytest.approx(-1.0)
    assert pe.roi_diff(roi, np.array([False, False, True, False])) == pytest.approx(3.0)
    assert math.isnan(pe.roi_diff(roi, np.zeros(4, dtype=bool)))


def test_an_all_taking_gate_has_no_hedge_edge() -> None:
    eq = np.array([1.01, 0.95, 1.02])
    roi = np.array([-1.0, 4.0, -1.0])
    terms = pe.hedge_diff_terms(eq, roi, np.ones(3, dtype=bool))
    assert np.allclose(terms, 0.0)


def test_gates_use_in_sample_percentiles_and_five_regime_sets() -> None:
    sig = pd.DataFrame({c: np.arange(1.0, 10.0) for c in pe.SIGNALS})
    g = pe.gates(sig)
    assert len(g) == 35
    assert g["vix_lvl<=p50"] == ("vix_lvl", "le", 5.0)


def test_drawdown_episodes_need_a_full_recovery_between_them() -> None:
    idx = pd.bdate_range("2020-01-01", periods=7)
    spot = pd.Series([100, 85, 95, 88, 101, 80, 90], index=idx, dtype=float)
    eps = pe.drawdown_episodes(spot, 0.10)
    assert [round(float(e["dd"]), 4) for e in eps] == [-0.15, round(80 / 101 - 1, 4)]
    assert eps[-1]["unrecovered"] is True


def test_losing_streak_and_unit_value() -> None:
    assert pe.longest_losing_streak(np.array([-1, -1, 2, -1, -1, -1, 0.5])) == 3
    assert pe.unit_value(1.0, 0.0, sold=True) == 0.0  # no fee on a worthless sale
    assert pe.unit_value(1.0, 1.0, sold=True) == pytest.approx(100.0 - COMMISSION_PER_CONTRACT)


def test_the_p_floor_can_clear_the_first_benjamini_hochberg_step() -> None:
    # 408 combinations at FDR 10%: the smallest p must be resolvable below
    # 0.10/408, or the multiple-testing verdict is decided by the draw count.
    assert 1 / (pe.P_REPS + 1) < pe.FDR / 408


def test_mean_without_best_drops_one_roll() -> None:
    assert pe._mean_without_best(np.array([-1.0, 5.0, -0.5])) == pytest.approx(-0.75)
    assert pe._mean_without_best(np.array([2.0])) is None


def test_the_hedge_comparison_is_spend_matched() -> None:
    # A gate that buys half the rolls is compared with an ungated book spending
    # half as much on every roll. If every roll is the same, that is a wash; an
    # unmatched comparison would credit the gate with the premium it skipped.
    eq = np.ones(4)
    roi = np.full(4, -1.0)
    take = np.array([True, False, True, False])
    assert abs(pe.hedge_diff_terms(eq, roi, take).sum()) < 1e-4
