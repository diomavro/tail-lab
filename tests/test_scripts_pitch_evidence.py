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
import re
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
    out = pe.hedge(mkt, {"5pct_4w_hold": rolls})
    c = out["configs"][2]
    assert c["edge"] > 0  # a 15/share payoff on a 1.00 put
    # The page prints both figures side by side: they must be ONE quantity.
    assert c["drop_best"]["edge"] == c["edge"]
    assert c["drop_best"]["edge_without_best"] == pytest.approx(0.0)  # nothing left
    assert c["drop_best"]["best_entry"] == "2020-01-06"
    # Skipping the roll leaves exactly the unhedged book.
    pd.testing.assert_series_equal(
        pe.book(mkt, rolls, budget=0.01, skip=0), pe.book(mkt, rolls, budget=0.0)
    )


def test_bootstrap_p_is_never_zero() -> None:
    # 2000 positive draws: the honest p is "below 1/2001", not 0. A 0 would
    # clear a Benjamini-Hochberg line of 0.10/408 that 1/2001 does not.
    p = pe.bootstrap_p(np.ones(2000))
    assert p == pytest.approx(1 / 2001)
    assert p > 0.10 / 408
    assert pe.bootstrap_p(-np.ones(9)) == 1.0
    # A draw of exactly zero is "no effect": it counts against the claim.
    assert pe.bootstrap_p(np.array([0.0, 1.0, 1.0])) == pytest.approx(2 / 4)


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


# ---------------------------------------------------------------- point in time
#
# Every timing filter is a claim about what was knowable at the entry close
# (docs/adr/0009). Each test below fails if one look-ahead path opens.


def test_lagged_reads_the_session_before_entry_not_the_entry_day() -> None:
    idx = pd.bdate_range("2020-01-06", periods=3)
    vix = pd.Series([15.0, 30.0, 45.0], index=idx)
    assert pe.lagged(vix, idx[1]) == 15.0  # the entry day's own 30 is not yet known
    assert pe.lagged(vix, idx[0]) is None


def test_a_later_rate_vintage_never_replaces_the_first() -> None:
    rates = pd.DataFrame(
        {
            "series_id": ["DGS3MO", "DGS3MO", "DGS10"],
            "obs_date": pd.to_datetime(["2020-01-06"] * 3),
            "vintage_date": pd.to_datetime(["2020-01-09", "2020-01-07", "2020-01-07"]),
            "value": [9.0, 1.5, 4.0],
        }
    )
    rate = pe.first_vintage_rate(rates)
    assert rate.tolist() == [pytest.approx(0.015)]  # the 2020-01-07 print, not the revision


def _roll_frame(entries: list[str], roi: list[float]) -> pd.DataFrame:
    e = pd.to_datetime(entries)
    return pd.DataFrame({"entry": e, "exit": e + pd.Timedelta(days=28), "roi": roi})


def test_gate_thresholds_come_from_in_sample_signals_only() -> None:
    # In sample the signal runs 1..9 (median 5); out of sample it is 100+.
    # Thresholds fitted on all 18 rolls would sit near 50 and take different rolls.
    entries = [f"2015-{m:02d}-01" for m in range(1, 10)] + [
        f"2018-{m:02d}-01" for m in range(1, 10)
    ]
    df = _roll_frame(entries, [-1.0] * 18)
    values = list(np.arange(1.0, 10.0)) + list(np.arange(100.0, 109.0))
    sig = pd.DataFrame({c: values for c in pe.SIGNALS}, index=df.index)
    sig["regime"] = "calm"
    combos = {c.gate: c for c in pe.rule_combos("5pct_4w_hold", df, sig, np.ones(18))}
    le = combos["vix_lvl<=p50"]
    assert le.take.tolist() == [True] * 5 + [False] * 13
    assert le.is_.tolist() == [True] * 9 + [False] * 9
    assert combos["vix_lvl>=p67"].take[9:].all()


def _combo(is_roi: list[float], oos_roi: list[float], take: list[bool], rule: str) -> object:
    n_is = len(is_roi)
    is_ = np.array([True] * n_is + [False] * len(oos_roi))
    days = tuple(str(d.date()) for d in pd.bdate_range("2010-01-04", periods=len(is_)))
    return pe.Combo(
        rule,
        "g",
        np.array(is_roi + oos_roi),
        np.ones(len(is_)),
        np.array(take),
        is_,
        1.0,
        1.0,
        days,
    )


def test_benjamini_hochberg_scores_the_out_of_sample_rolls_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pe, "P_REPS", 400)
    # In sample the gate takes exactly the winners; out of sample exactly the
    # losers. Scoring the in-sample half would report a discovery.
    is_roi = [5.0, -1.0] * 15
    oos_roi = [5.0, -1.0] * 15
    take = [True, False] * 15 + [False, True] * 15
    c = _combo(is_roi, oos_roi, take, "5pct_8w_sell4w")
    out = pe.benjamini_hochberg_oos([(c, {}, {"taken": 15})], np.random.default_rng(0))
    assert out["survivors"] == []
    assert out["min_p"] > 0.9
    assert out["sell_rules"] == {"tested": 1, "survivors": 0, "min_p": out["min_p"]}
    # The oracle takes the out-of-sample winners and does pass.
    assert out["oracle"]["5pct_8w_sell4w"]["oos_winners"] == 15
    assert out["oracle"]["5pct_8w_sell4w"]["p"] < 0.01


def test_a_held_rule_with_one_winner_is_excluded_from_the_sold_rules_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pe, "P_REPS", 200)
    roi = [-1.0] * 30
    take = [True, False] * 30
    held = _combo(roi, roi, take, "20pct_4w_hold")
    sold = _combo(roi, roi, take, "20pct_8w_sell4w")
    out = pe.benjamini_hochberg_oos([(held, {}, {}), (sold, {}, {})], np.random.default_rng(0))
    assert out["tested"] == 2
    assert out["sell_rules"]["tested"] == 1
    assert [s["rule"] for s in out["smallest_p"]] == ["20pct_4w_hold", "20pct_8w_sell4w"]


def test_the_oracle_takes_the_best_third() -> None:
    assert pe.oracle_take(np.array([-1.0, 3.0, -0.5, 0.2, -0.9, -0.1])).tolist() == [
        False,
        True,
        False,
        True,
        False,
        False,
    ]


def test_ci95_is_the_two_and_a_half_percent_tails() -> None:
    assert pe.ci95(np.arange(1001.0)) == [25.0, 975.0]


def test_a_resample_that_takes_no_roll_is_skipped_not_averaged() -> None:
    roi = np.full(30, -1.0)
    roi[4] = 9.0
    take = np.zeros(30, dtype=bool)
    take[4] = True  # most resamples miss it entirely
    ci, p = pe.boot_roi_diff(roi, take, np.random.default_rng(1))
    assert all(math.isfinite(x) for x in ci)
    assert 0 < p <= 1


def test_the_alpha_anchor_uses_an_expiry_at_least_28_days_out() -> None:
    t = pd.Timestamp("2020-01-06")
    near, far = t + pd.Timedelta(days=7), t + pd.Timedelta(days=35)
    chain = pd.DataFrame(
        {
            "underlying": "SPY",
            "quote_date": t,
            "expiration": [near, far],
            "strike": [95.0, 95.0],
            "bid": [0.10, 0.90],
            "ask": [0.20, 1.00],
            "spot": 100.0,
            "iv": 0.2,
        }
    )
    got = pe._alpha_anchor(pe.market_from_panel(chain, "SPY"), t)
    assert got is not None
    assert got[0].expiration == far.date()


def test_put_richness_is_implied_vol_over_vix_in_decimal() -> None:
    mkt = pe.market_from_panel(_panel([100.0] * 25), "SPY")
    rolls, _ = pe.run_rolls(
        mkt, moneyness=5.0, tenor_weeks=3.0, hold_weeks=None, start="2020-01-07", end="2020-01-07"
    )
    df = pe.rolls_frame(rolls)
    day_before = SESSIONS[0]
    ti = pe.TimingInputs(
        vix=pd.Series([20.0, 99.0], index=[day_before, SESSIONS[1]]),
        vix_z=pd.Series([0.5], index=[day_before]),
        regime=pd.Series(["elevated"], index=[day_before]),
        rate=pd.Series([0.01], index=[day_before]),
    )
    sig = pe.signals(mkt, df, ti)
    from tail_lab.research.skew import implied_vol_put

    r = rolls[0]
    iv = implied_vol_put(
        r.ask, spot=100.0, strike=95.0, t_years=r.dte / 365.25, r=0.01, q=pe.DIV_YIELD
    )
    assert iv is not None
    assert sig["vix_lvl"].iloc[0] == 20.0  # the prior close, not the entry day's 99
    assert sig["put_rich"].iloc[0] == pytest.approx(iv / 0.20)
    assert sig["regime"].iloc[0] == "elevated"
    assert sig["vix_z"].iloc[0] == 0.5
    assert sig["put_cost"].iloc[0] == pytest.approx(1.0)


# ---------------------------------------------------------------- the counts the page prints


def _flat_market(spot: list[float]) -> object:
    idx = pd.bdate_range("2020-01-06", periods=len(spot))
    s = pd.Series(spot, index=idx)
    return pe.Market("SPY", None, idx, s, pd.DataFrame())  # type: ignore[arg-type]


def _roll(mkt: object, a: int, b: int, path: list[float]) -> object:
    days = mkt.sessions[a : b + 1]  # type: ignore[attr-defined]
    return pe.Roll(
        days[0], days[-1], 95.0, 1.0, 100.0, 100.0, 28, path[-1] - 1.0, pd.Series(path, index=days)
    )


def test_hedge_counts_beaters_and_drawdown_cuts_on_a_common_window() -> None:
    # Spot falls 20% mid-window and recovers. One rule's put pays 10x through
    # the fall (cuts the drawdown, beats unhedged); two rules' puts expire
    # worthless (deepen it, lose growth).
    spot = [100.0] * 5 + [80.0] * 10 + [100.0] * 10
    mkt = _flat_market(spot)
    win_path = [1.0] * 4 + [10.0] * 17
    lose_path = [1.0] + [0.0] * 20
    rules = {
        "win": [_roll(mkt, 1, 21, win_path)],
        "lose": [_roll(mkt, 0, 20, lose_path)],
        "lose2": [_roll(mkt, 0, 23, lose_path + [0.0] * 3)],
    }
    out = pe.hedge(mkt, rules)
    assert out["window"] == [str(mkt.sessions[1].date()), str(mkt.sessions[20].date())]
    assert out["unhedged_max_dd"] == pytest.approx(-0.2, abs=1e-3)  # dividend accrual
    assert out["n_beat"] == 3  # the win rule at each of the three budgets
    assert out["n_cut_dd"] == 3
    assert {c["rule"] for c in out["configs"] if "drop_best" in c} == {"win"}
    win = [c for c in out["configs"] if c["rule"] == "win"]
    yrs = (mkt.sessions[21] - mkt.sessions[1]).days / 365.25
    # "Premium/yr": one roll at b of the book, over the rule's own span, in %.
    assert [c["premium_pct_per_year"] for c in win] == pytest.approx(
        [b / yrs * 100 for b in pe.BUDGETS]
    )
    # Drop-best is measured on the same common window as the edge itself.
    assert all(c["drop_best"]["edge"] == c["edge"] for c in win)


def test_share_of_payoff_from_best_roll_and_by_period() -> None:
    df = pd.DataFrame(
        {
            "entry": pd.to_datetime(["2010-02-01", "2014-02-01", "2019-02-01"]),
            "exit": pd.to_datetime(["2010-03-01", "2014-03-01", "2019-03-01"]),
            "roi": [-1.0, 3.0, 1.0],
            "ask": [1.0, 1.0, 1.0],
            "spot_entry": [100.0, 100.0, 100.0],
            "dte": [28, 28, 28],
        }
    )
    st = pe.standalone_stats(df, np.random.default_rng(0))
    assert st["share_of_payoff_from_best_roll"] == pytest.approx(4 / 6)
    assert st["hits"] == 2
    assert st["best_entry"] == "2014-02-01"


def test_a_phase_keeps_every_period_th_entry() -> None:
    # Entries 0 and 20 caught a crash: only phase 0 holds them both.
    rois = pd.Series([-1.0] * 40)
    rois[[0, 20]] = 9.0
    rois[5] = np.nan  # a refused day is dropped, not counted as a loss
    means = pe.phase_means(rois, 20)
    assert means[0] == 9.0
    assert sum(m > 0 for m in means) == 1
    assert means[5] == -1.0


def test_every_day_entries_count_positive_phases() -> None:
    # Weekly expiries, all quoted every session, spot flat: every put expires
    # worthless, so no phase of the cycle is positive.
    sessions = pd.bdate_range("2020-01-06", periods=60)
    expiries = sessions[4::5]
    rows = [{"quote_date": d, "expiration": e} for d in sessions for e in expiries if e > d]
    panel = pd.DataFrame(rows).assign(
        underlying="SPY", strike=95.0, bid=0.9, ask=1.0, spot=100.0, iv=0.2
    )
    mkt = pe.market_from_panel(panel, "SPY")
    out = pe.every_day_entries(mkt, 5.0, ("2020-01-06", "2020-03-27"))
    assert out["entry_days"] > 20
    assert out["phases_positive"] == 0
    assert out["mean_roi"] == pytest.approx(-1.0)


def test_a_sold_put_never_exits_after_its_own_expiry() -> None:
    # A 4-week put cannot be held 4.5 weeks and then sold: the exit session
    # (2020-02-07) is inside the panel but after the 2020-02-03 expiry, so the
    # roll is dropped rather than "sold" at a bid that no longer exists.
    mkt = pe.market_from_panel(_panel([100.0] * 25), "SPY")
    rolls, _ = pe.run_rolls(
        mkt, moneyness=5.0, tenor_weeks=4.0, hold_weeks=4.5, start="2020-01-06", end="2020-01-06"
    )
    assert rolls == []


def test_missing_days_are_counted_against_the_exchange_calendar() -> None:
    # Cboe's VIX file has rows on exchange holidays since 2022; the SPX close
    # does not. 2023-01-16 (MLK) is not a missing session.
    cboe = pd.DataFrame(
        {
            "index_symbol": ["SPX"] * 3 + ["PPUT"],
            "trade_date": pd.to_datetime(["2023-01-13", "2023-01-17", "2023-01-18", "2023-01-16"]),
            "close": 1.0,
        }
    )
    cal = pe.exchange_calendar(cboe)
    panel = pd.Series(1.0, index=pd.to_datetime(["2023-01-13", "2023-01-16", "2023-01-18"]))
    dq = pe.data_quality(panel, cal)
    assert dq["missing_trading_days"] == 1  # 2023-01-17
    assert dq["sessions_on_closed_days"] == ["2023-01-16"]
    # The holiday row stands where the next session is missing: a shifted date.
    assert dq["closed_day_then_next_missing"] == 1
    whole = pd.Series(1.0, index=pd.to_datetime(["2023-01-13", "2023-01-16", "2023-01-17"]))
    assert pe.data_quality(whole, cal)["closed_day_then_next_missing"] == 0


def test_rolls_on_closed_days_count_entries_and_exits() -> None:
    mkt = _flat_market([100.0] * 10)
    d = mkt.sessions  # type: ignore[attr-defined]
    rolls = {"a": [_roll(mkt, 0, 3, [1.0] * 4), _roll(mkt, 3, 6, [1.0] * 4)]}
    assert pe.touching_closed_days(rolls, {d[6]}) == {"rolls": 2, "touching": 1}
    assert pe.touching_closed_days(rolls, {d[3]}) == {"rolls": 2, "touching": 2}
    assert pe.touching_closed_days(rolls, {d[5]}) == {"rolls": 2, "touching": 0}


def test_episodes_are_declines_of_ten_percent() -> None:
    # The page and the key both say "10% or more".
    assert pe.EPISODE_DD == 0.10


# ---------------------------------------------------------------- 2008-09


def test_a_crisis_book_is_rerun_without_its_best_month() -> None:
    # Three monthly rolls; the index halves in the second. That month's put
    # pays 20x; the others expire worthless; one roll date was refused.
    df = pd.DataFrame(
        {
            "entry": pd.to_datetime(["2008-08-15", "2008-09-19", "2008-10-17", "2008-11-21"]),
            "filled": [True, True, False, True],
            "roi": [-1.0, 20.0, np.nan, -1.0],
            "reason": [None, None, "spread 80% of mid", None],
        }
    )
    eq = np.array([1.0, 0.5, 1.0, 1.0])
    out = pe.crisis_books(df, eq)
    assert out["unhedged"] == {"terminal": pytest.approx(0.5), "max_dd": pytest.approx(-0.5)}
    b1 = out["books"][2]
    assert b1["budget"] == 0.01
    # 0.99 * (0.99*0.5 + 0.01*21) * 1.0 * 0.99: the refused roll is unhedged.
    assert b1["terminal"] == pytest.approx(0.99 * (0.99 * 0.5 + 0.21) * 0.99)
    assert b1["terminal_without_best"] == pytest.approx(0.99 * 0.5 * 0.99)
    assert out["n_beat"] == 3
    assert out["filled"] == 3
    assert out["best_entry"] == "2008-09-19"
    assert out["refused"] == [{"entry": "2008-10-17", "reason": "spread 80% of mid"}]
    # A book that never beats is not rerun, and is not counted.
    worthless = pe.crisis_books(df.assign(roi=[-1.0, -1.0, np.nan, -1.0]), eq)
    assert worthless["n_beat"] == 0
    assert all("terminal_without_best" not in b for b in worthless["books"])


def test_crisis_equity_runs_roll_date_to_roll_date() -> None:
    spx = pd.Series(
        [100.0, 50.0, 75.0], index=pd.to_datetime(["2008-08-15", "2008-09-19", "2008-10-17"])
    )
    dates = list(spx.index[:2])
    eq = pe.crisis_equity(spx, dates, spx.index[2])
    accrual = [math.exp(pe.DIV_YIELD * d / 365.25) for d in (35, 28)]
    assert eq.tolist() == pytest.approx([0.5 * accrual[0], 1.5 * accrual[1]])


def test_the_evidence_file_never_carries_nan() -> None:
    assert pe.finite({"a": [float("nan"), 1.0], "b": (float("inf"),), "c": "x"}) == {
        "a": [None, 1.0],
        "b": [None],
        "c": "x",
    }


def _quotes(rows: list[tuple[str, str, float, float, float, float, int]]) -> pd.DataFrame:
    """option_quotes-shaped rows: (quote_date, expiration, spot, strike, bid, ask, oi)."""
    df = pd.DataFrame(
        rows, columns=["quote_date", "expiration", "spot", "strike", "bid", "ask", "open_interest"]
    )
    return df.assign(
        underlying="SPY",
        quote_date=pd.to_datetime(df.quote_date),
        expiration=pd.to_datetime(df.expiration),
    )


def test_a_crisis_roll_settles_on_spy_moved_by_the_spx_move() -> None:
    # SPX halves between entry and expiry; SPY at 120 is assumed to halve too,
    # to 60, so the 108 put is worth 48 a share. The second date's only 10%
    # put is quoted 0.10 / 1.00: a 164% spread, refused -- and the row says so.
    q = _quotes(
        [
            ("2008-09-19", "2008-10-17", 120.0, 108.0, 0.90, 1.00, 100),
            ("2008-10-17", "2008-11-21", 60.0, 54.0, 0.10, 1.00, 100),
        ]
    )
    spx = pd.Series([100.0, 50.0], index=pd.to_datetime(["2008-09-19", "2008-10-17"]))
    df = pe.crisis_rolls(q, spx, 10.0, list(pd.to_datetime(["2008-09-19", "2008-10-17"])))
    assert df.roi.iloc[0] == pytest.approx(4800.0 / (100.0 + COMMISSION_PER_CONTRACT) - 1.0)
    assert df.expiry.iloc[0] == pd.Timestamp("2008-10-17")
    assert df.filled.tolist() == [True, False]
    assert df.reason.iloc[1] == "spread 164% of mid, over the 50% cap"


def test_refusal_reasons_name_the_guard() -> None:
    def one(strike: float, bid: float, ask: float, oi: int) -> str:
        return pe.refusal_reason(
            _quotes([("2008-09-19", "2008-10-17", 100.0, strike, bid, ask, oi)]), 10.0
        )

    assert one(85.0, 1.0, 1.1, 100) == "no strike within 1pp (nearest 15.0% OOM)"
    assert one(90.0, 0.0, 1.0, 100) == "no bid"
    assert one(90.0, 1.0, 1.1, 9) == "open interest under 10"
    assert one(90.0, 1.0, 1.1, 100) == "refused by another guard"
    assert one(90.0, 0.16, 0.31, 100) == "spread 64% of mid, over the 50% cap"


def test_a_refusal_reason_never_carries_a_quote() -> None:
    # option_quotes is cleared for private research only and the page is
    # public: a reason names the guard, never the bid, ask or strike.
    for strike, bid, ask, oi in (
        (85.0, 1.0, 1.1, 100),
        (90.0, 0.0, 1.37, 100),
        (90.0, 1.23, 1.37, 3),
        (90.0, 0.16, 0.31, 100),
    ):
        reason = pe.refusal_reason(
            _quotes([("2008-09-19", "2008-10-17", 100.0, strike, bid, ask, oi)]), 10.0
        )
        for figure in (f"{bid:.2f}", f"{ask:.2f}", f"{strike:g}"):
            assert figure not in reason, (figure, reason)
        assert not re.search(r"\b(bid|ask|strike) \d", reason), reason


def test_the_2008_book_runs_to_the_last_filled_rolls_expiry() -> None:
    # Two monthly roll dates, every depth listed; the 2009-01-16 date also
    # lists a quarterly. The last roll is filled, so the unhedged arm must run
    # to its expiry (2009-02-20), not stop at its entry.
    rows = []
    for d, e, sp in (("2008-12-19", "2009-01-16", 100.0), ("2009-01-16", "2009-02-20", 90.0)):
        for k in (95.0, 90.0, 85.0, 80.0):
            rows.append((d, e, sp, k * sp / 100, 0.9, 1.0, 100))
    rows.append(("2009-01-16", "2009-03-31", 90.0, 81.0, 2.0, 2.1, 100))
    q = _quotes(rows)
    cboe = pd.DataFrame(
        {
            "index_symbol": "SPX",
            "trade_date": pd.to_datetime(["2008-12-19", "2009-01-16", "2009-02-20"]),
            "close": [100.0, 90.0, 45.0],
        }
    )
    out = pe.crisis_2008(q, cboe)
    assert out["roll_dates"] == 2
    # Every 2008-09 month but the two with a roll date is reported as a gap.
    gaps = out["months_without_roll_date"]
    assert len(gaps) == 22
    assert "2008-12" not in gaps and "2009-01" not in gaps
    assert gaps[0] == "2008-01" and gaps[-1] == "2009-12"
    assert out["nearest_dte"] == [28, 35]
    assert out["dates_with_two_expiries"] == ["2009-01-16"]
    r = out["10pct"]
    assert r["last"] == "2009-02-20"
    # The final month's halving is in the unhedged arm.
    assert r["unhedged"]["terminal"] == pytest.approx(
        0.45 * math.exp(pe.DIV_YIELD * (28 + 35) / 365.25)
    )


def test_cboe_benchmark_reads_only_the_hedge_window() -> None:
    days = pd.to_datetime(["2009-12-31", "2010-01-04", "2011-01-04", "2012-01-04"])
    cboe = pd.DataFrame(
        [
            {"index_symbol": sym, "trade_date": d, "close": c}
            for sym in ("SPX", "PPUT", "PPUT3M", "VXTH")
            for d, c in zip(days, [50.0, 100.0, 110.0, 400.0], strict=True)
        ]
    )
    out = pe.cboe_benchmark(cboe, ["2010-01-04", "2011-01-04"])
    assert out["span"] == ["2010-01-04", "2011-01-04"]
    assert out["SPX"]["growth"] == pytest.approx(math.log(1.1) / (365 / 365.25))
    assert out["VXTH"]["max_dd"] == 0.0


def _timing_combo(gate: str, take_is: list[bool], take_oos: list[bool]) -> object:
    roi = np.array([float(i) for i in range(30)] * 2) / 10 - 1.0
    is_ = np.array([True] * 30 + [False] * 30)
    days = tuple(str(d.date()) for d in pd.bdate_range("2010-01-04", periods=60))
    return pe.Combo(
        "5pct_8w_sell4w", gate, roi, np.ones(60), np.array(take_is + take_oos), is_, 1.0, 1.0, days
    )


def test_timing_picks_the_winner_in_sample_and_scores_it_out_of_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Three gates on the same rolls. In sample "top" takes the best ten rolls
    # and "bottom" the worst; out of sample they swap. The selected winner must
    # be the IN-SAMPLE best ("top"), scored on its out-of-sample rolls, and the
    # in-sample/out-of-sample ranks are exactly reversed.
    monkeypatch.setattr(pe, "P_REPS", 200)

    def band(lo: int) -> list[bool]:
        return [lo <= i < lo + 10 for i in range(30)]

    combos = [
        _timing_combo("bottom", band(0), band(20)),
        _timing_combo("top", band(20), band(0)),
        _timing_combo("middle", band(12), band(8)),  # +0.2 in sample, -0.2 out
    ]
    out = pe.timing(combos, np.random.default_rng(0))
    for key in ("roi_diff", "dg"):
        sel = out[key]
        assert sel["gate"] == "top", key
        assert sel["is"]["roi_diff"] == pytest.approx(1.0)
        assert sel["oos"]["roi_diff"] == pytest.approx(-1.0)
        assert sel["spearman_is_oos"] == pytest.approx(-1.0)
        assert sel["eligible"] == 3
        assert sel["share_positive_oos"] == pytest.approx(1 / 3)


def test_every_benjamini_hochberg_pass_runs_at_the_stated_fdr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADR 0021 §6 requires the correction, and the page prints its level.
    monkeypatch.setattr(pe, "P_REPS", 50)
    seen: list[float] = []
    real = pe.benjamini_hochberg

    def spy(ps: list[float], alpha: float) -> list[bool]:
        seen.append(alpha)
        return list(real(ps, alpha=alpha))

    monkeypatch.setattr(pe, "benjamini_hochberg", spy)
    roi = [-1.0, 2.0] * 15
    c = _combo(roi, roi, [True, False] * 30, "5pct_8w_sell4w")
    out = pe.benjamini_hochberg_oos([(c, {}, {"taken": 15})], np.random.default_rng(0))
    assert seen == [0.10, 0.10]
    assert out["fdr"] == 0.10


def _git(cwd: Path, *args: str) -> None:
    import subprocess

    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_code_version_reports_dirty_src_and_survivable_tree_hashes(tmp_path: Path) -> None:
    for d in ("src", "scripts", "docs"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "f.py").write_text("x = 1\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "c")
    clean = pe.code_version(tmp_path)
    assert clean["dirty"] is False
    assert len(clean["sha"]) == 40
    assert set(clean["tree"]) == {"src", "scripts"}
    (tmp_path / "docs" / "f.py").write_text("x = 2\n")
    assert pe.code_version(tmp_path)["dirty"] is False  # docs/ did not run
    (tmp_path / "src" / "f.py").write_text("x = 2\n")
    assert pe.code_version(tmp_path)["dirty"] is True


def test_the_generator_refuses_another_checkouts_tail_lab(tmp_path: Path) -> None:
    pe.check_imports_from(tmp_path, tmp_path / "src" / "tail_lab" / "__init__.py")
    with pytest.raises(RuntimeError, match="PYTHONPATH"):
        pe.check_imports_from(tmp_path, Path("/elsewhere/src/tail_lab/__init__.py"))


def test_drop_best_leaves_out_the_best_roll_not_the_first() -> None:
    # Two rolls: a loser, then a 10x winner. Without the winner the book is
    # the unhedged book less the loser's premium, so it falls behind.
    mkt = _flat_market([100.0] * 12)
    rolls = {"r": [_roll(mkt, 0, 5, [1.0] + [0.0] * 5), _roll(mkt, 5, 11, [1.0] * 6 + [10.0])]}
    out = pe.hedge(mkt, rolls)
    c = out["configs"][0]
    assert c["drop_best"]["best_entry"] == str(mkt.sessions[5].date())  # type: ignore[attr-defined]
    assert c["drop_best"]["edge_without_best"] < 0 < c["edge"]


def test_drawdown_span_is_the_worst_declines_own_peak_and_trough() -> None:
    # Peak 2 on day 1, trough 0.8 on day 3 (-60%); the later 1.9 never makes
    # a new peak, so the span is (day 1, day 3), not the global max/min dates.
    idx = pd.bdate_range("2020-01-06", periods=6)
    w = pd.Series([1.0, 2.0, 1.5, 0.8, 1.9, 0.9], index=idx)
    assert pe.drawdown_span(w) == (str(idx[1].date()), str(idx[3].date()))


def test_peak_put_reports_the_open_rolls_mark_and_settlement() -> None:
    # A roll marked at 74x its cost on the peak day, then expiring worthless:
    # the page calls that a paper mark given back.
    mkt = _flat_market([100.0] * 10)
    r = _roll(mkt, 1, 8, [1.0, 1.0, 74.0, 30.0, 5.0, 1.0, 0.5, 0.0])
    peak = str(mkt.sessions[3].date())  # type: ignore[attr-defined]
    assert pe.peak_put([r], peak) == {
        "entry": str(mkt.sessions[1].date()),  # type: ignore[attr-defined]
        "exit": str(mkt.sessions[8].date()),  # type: ignore[attr-defined]
        "mark_multiple": 74.0,
        "exit_multiple": 0.0,
    }
    # On an entry or exit day nothing is open between rolls: no paper mark.
    assert pe.peak_put([r], str(mkt.sessions[1].date())) is None  # type: ignore[attr-defined]
    assert pe.peak_put([r], str(mkt.sessions[8].date())) is None  # type: ignore[attr-defined]


def test_a_sold_put_with_no_qualifying_exit_bid_is_credited_intrinsic_and_flagged() -> None:
    # Spot drops to 80 before the 2-week exit, where the bid is 0 (refused):
    # the 95 put is credited its exercise value, 15 a share, less the sale's
    # commission -- and the roll says so, so the page can count it.
    panel = _panel([100.0] * 10 + [80.0] * 15)
    panel.loc[10, "bid"] = 0.0
    mkt = pe.market_from_panel(panel, "SPY")
    rolls, _ = pe.run_rolls(
        mkt, moneyness=5.0, tenor_weeks=4.0, hold_weeks=2.0, start="2020-01-06", end="2020-01-06"
    )
    c = 1.0 / (100.0 + COMMISSION_PER_CONTRACT)
    assert rolls[0].exit == SESSIONS[10]
    assert rolls[0].roi == pytest.approx(c * 1500.0 - c * COMMISSION_PER_CONTRACT - 1.0)
    assert rolls[0].exit_at_intrinsic is True
    # With a qualifying bid the same sale is at the bid and not flagged.
    ok, _ = pe.run_rolls(
        pe.market_from_panel(_panel([100.0] * 10 + [80.0] * 15), "SPY"),
        moneyness=5.0,
        tenor_weeks=4.0,
        hold_weeks=2.0,
        start="2020-01-06",
        end="2020-01-06",
    )
    assert ok[0].roi == pytest.approx(c * 90.0 - c * COMMISSION_PER_CONTRACT - 1.0)
    assert ok[0].exit_at_intrinsic is False


def test_overlap_says_when_several_tests_are_one_result() -> None:
    # Two rules on the same dates whose filters skip the same out-of-sample
    # roll: one effect seen twice. A third on other dates is not comparable.
    a = _combo([0.0] * 4, [1.0, -1.0, 0.5, -0.5], [True] * 4 + [True, False, True, True], "a")
    b = _combo([0.0] * 4, [2.0, -1.0, 0.4, -0.6], [True] * 4 + [True, False, True, True], "b")
    out = pe.overlap([a, b])
    assert out["same_oos_entries"] is True
    assert out["oos_entries"] == 4
    assert out["same_skipped"] is True
    assert out["skipped"] == [a.entries[5]]  # type: ignore[attr-defined]
    assert out["roi_corr"][0] == pytest.approx(
        np.corrcoef([1.0, -1.0, 0.5, -0.5], [2.0, -1.0, 0.4, -0.6])[0, 1]
    )
    c = _combo([0.0] * 4, [1.0, -1.0, 0.5, -0.5], [True] * 4 + [False, True, True, True], "c")
    assert pe.overlap([a, c])["same_skipped"] is False
    shifted = pe.Combo(**{**vars(a), "entries": tuple(reversed(a.entries))})  # type: ignore[attr-defined]
    moved = pe.overlap([a, shifted])
    assert moved["same_oos_entries"] is False
    assert moved["roi_corr"] is None


def test_lifting_the_spread_cap_buys_a_wide_quote_at_its_ask_and_restores_the_cap() -> None:
    # The 10% put on the second date is quoted 0.10 / 1.00 (164% of mid):
    # refused under the cap, bought at 1.00 with it lifted.
    rows = []
    for d, e, sp in (("2008-12-19", "2009-01-16", 100.0), ("2009-01-16", "2009-02-20", 90.0)):
        for k in (95.0, 90.0, 85.0, 80.0):
            wide = d == "2009-01-16" and k == 90.0
            rows.append((d, e, sp, k * sp / 100, 0.10 if wide else 0.9, 1.0, 100))
    q = _quotes(rows)
    cboe = pd.DataFrame(
        {
            "index_symbol": "SPX",
            "trade_date": pd.to_datetime(["2008-12-19", "2009-01-16", "2009-02-20"]),
            "close": [100.0, 90.0, 45.0],
        }
    )
    out = pe.crisis_2008(q, cboe)
    assert out["10pct"]["filled"] == 1
    off = out["spread_guard_off"]["depths"]["10pct"]
    assert (off["filled"], off["rolls"]) == (2, 2)
    # The halving after 2009-01-16 is now hedged: the book beats its own
    # unhedged arm (which, the last roll now filled, runs to its expiry).
    assert off["books"][-1]["terminal"] > off["unhedged"]["terminal"]
    assert off["n_beat"] == 3
    assert pe.marks.MAX_RELATIVE_SPREAD == 0.50
