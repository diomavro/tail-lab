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
    return pe.Combo(
        rule, "g", np.array(is_roi + oos_roi), np.ones(len(is_)), np.array(take), is_, 1.0, 1.0
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


# ---------------------------------------------------------------- 2008-09


def test_a_crisis_book_is_rerun_without_its_best_month() -> None:
    # Three monthly rolls; the index halves in the second. That month's put
    # pays 20x; the others expire worthless; one roll date was refused.
    df = pd.DataFrame(
        {
            "entry": pd.to_datetime(["2008-08-15", "2008-09-19", "2008-10-17", "2008-11-21"]),
            "filled": [True, True, False, True],
            "roi": [-1.0, 20.0, np.nan, -1.0],
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
