"""Tests for the Put Lab roll engine (``research/backtest/put_roll.py``).

The pinned case recomputes the expected cycle arithmetic from first
principles using the independently-tested ``BlackScholesPricer`` as a black
box (``docs/STANDARDS.md``: pin against an independent computation, not a
snapshot of the function's own output). The point-in-time test is the
adversarial no-look-ahead guard required for anything a backtest reads
(``docs/adr/0009``).
"""

from __future__ import annotations

import datetime as dt
import itertools
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.brokerage import roll_cost
from tail_lab.research.backtest.growth import time_average_growth
from tail_lab.research.backtest.put_roll import (
    DEFAULT_RATE,
    IV_WINDOW,
    MIN_YEARS_FOR_ANNUALIZED,
    PREMIUM_FLOOR_FRAC,
    annualized_return,
    annualized_sharpe,
    annualized_so_far_curve,
    compute_put_backtest,
    load_asof_series,
    run_put_roll,
    trailing_realized_vol,
)
from tail_lab.research.backtest.sizing import FixedPremium, WealthFraction
from tail_lab.research.option_pricer import BlackScholesPricer


def _flat_with_dips(n: int, dips: dict[int, float], base: float = 100.0) -> pd.Series:
    px = np.full(n, base, dtype=float)
    for i, v in dips.items():
        px[i] = v
    idx = pd.date_range("2021-01-01", periods=n, freq="B")
    return pd.Series(px, index=idx, name="test")


def test_run_put_roll_pins_arithmetic_against_the_pricer() -> None:
    """Two non-overlapping 8-week rolls on a crafted path; every field is
    recomputed independently from the tested pricer."""
    n = 101  # entries at 20 -> expiry 60, and 60 -> expiry 100
    prices = _flat_with_dips(n, {60: 70.0, 100: 98.0})
    iv = pd.Series(np.full(n, 0.30), index=prices.index)  # explicit, so premiums are pinnable
    notional, moneyness, tenor_w = 1000.0, 5.0, 8.0
    tenor_days = round(tenor_w * 5)  # 40
    t_years = tenor_days / 252.0

    res = run_put_roll(
        prices,
        iv,
        asset="TEST",
        as_of=dt.date(2021, 6, 1),
        notional=notional,
        moneyness_pct=moneyness,
        tenor_weeks=tenor_w,
        lookback_years=10,
    )

    pricer = BlackScholesPricer()
    px = prices.to_numpy(dtype=float)
    expected = []
    for entry in (20, 60):
        spot = px[entry]
        strike = spot * (1 - moneyness / 100)
        prem = max(
            pricer.price_put(spot=spot, strike=strike, t_years=t_years, r=DEFAULT_RATE, sigma=0.30),
            spot * PREMIUM_FLOOR_FRAC,
        )
        contracts = notional / prem
        cost = roll_cost(contracts, notional, tenor_w, moneyness)
        payoff = contracts * max(strike - px[entry + tenor_days], 0.0)
        expected.append(
            {
                "prem": prem,
                "contracts": contracts,
                "cost": cost,
                "payoff": payoff,
                "net": payoff - notional - cost,
            }
        )

    assert res.n_cycles == 2
    for got, exp in zip(res.cycles, expected, strict=True):
        assert got.premium == pytest.approx(exp["prem"])
        assert got.contracts == pytest.approx(exp["contracts"])
        assert got.cost == pytest.approx(exp["cost"])
        assert got.payoff == pytest.approx(exp["payoff"])
        assert got.net == pytest.approx(exp["net"])

    total_payoff = sum(e["payoff"] for e in expected)
    total_cost = sum(e["cost"] for e in expected)
    assert res.total_premium == pytest.approx(2 * notional)
    assert res.total_payoff == pytest.approx(total_payoff)
    assert res.total_brokerage == pytest.approx(total_cost)
    assert res.net_pnl == pytest.approx(total_payoff - 2 * notional - total_cost)
    assert res.roi_on_premium == pytest.approx(
        (total_payoff - 2 * notional - total_cost) / (2 * notional)
    )
    # Paced by the span actually on risk, not the window requested. Pinning
    # `lookback_years` here re-asserts the flatterer fixed on 2026-09-09: on a
    # real SPY run trading the same 20 cycles, `years=20` reported -2.52%/yr
    # where the honest figure over the 4.79 years traded was -10.13%/yr.
    assert res.traded_start is not None and res.traded_end is not None
    traded_years = (res.traded_end - res.traded_start).days / 365.25
    assert res.annualized_return == pytest.approx(
        annualized_return(res.roi_on_premium, traded_years)
    )
    # cycle 1 pays off (dip to 70 << strike 95), cycle 2 expires worthless (98 > 66.5).
    assert res.hit_rate == pytest.approx(0.5)
    assert res.worst_bleed_streak == 1
    assert res.biggest_payoff_mult == pytest.approx(expected[0]["payoff"] / notional)
    # equity curve: one seed point + one point per cycle, monotone bookkeeping.
    assert [p.cum_pnl for p in res.equity_curve] == pytest.approx(
        [0.0, expected[0]["net"], expected[0]["net"] + expected[1]["net"]]
    )


def test_costs_strictly_reduce_net_pnl_and_roi() -> None:
    """The same strategy run cost-free (commission 0, spread 0) has zero
    brokerage and a strictly higher net P&L / ROI than the realistic-cost run —
    a bought put's brokerage can only reduce what you keep."""
    prices = _flat_with_dips(120, {60: 70.0, 100: 80.0})
    iv = pd.Series(np.full(120, 0.30), index=prices.index)
    common = dict(
        asset="T",
        as_of=dt.date(2021, 6, 1),
        notional=1000.0,
        moneyness_pct=8.0,
        tenor_weeks=4.0,
        lookback_years=10,
    )
    free = run_put_roll(prices, iv, commission_per_contract=0.0, spread_scale=0.0, **common)  # type: ignore[arg-type]
    costed = run_put_roll(prices, iv, **common)  # type: ignore[arg-type]

    assert free.total_brokerage == 0.0
    assert costed.total_brokerage > 0.0
    assert costed.net_pnl < free.net_pnl
    assert costed.roi_on_premium < free.roi_on_premium
    # net is the gross net (payoff - notional) less exactly the roll's brokerage.
    for got, ref in zip(costed.cycles, free.cycles, strict=True):
        assert got.net == pytest.approx(ref.net - got.cost)


def test_less_frequent_tenor_bleeds_less_to_brokerage() -> None:
    """The point of the cost model: on a flat path where every OOM put expires
    worthless (so the *gross* return is identical -- a total loss -- at any
    tenor), a frequent (weekly) roll pays far more brokerage per year and per
    premium dollar than an infrequent (quarterly) one, so its net return is
    strictly worse. Short tenors roll ~12x as often and, being cheaper, buy far
    more (share-equivalent) contracts per premium dollar -> more commission, and
    quote a wider bid-ask spread."""
    n = 252 * 4 + 60
    prices = pd.Series(
        np.full(n, 500.0), index=pd.date_range("2016-01-01", periods=n, freq="B"), name="T"
    )
    iv = pd.Series(np.full(n, 0.18), index=prices.index)
    common = dict(asset="T", as_of=dt.date(2020, 1, 1), notional=1000.0, moneyness_pct=5.0)
    years = 4.0
    weekly = run_put_roll(prices, iv, tenor_weeks=1.0, lookback_years=years, **common)  # type: ignore[arg-type]
    quarterly = run_put_roll(prices, iv, tenor_weeks=12.0, lookback_years=years, **common)  # type: ignore[arg-type]

    # Gross returns tie exactly: on a flat path no put ever pays, so both lose
    # 100% of premium before costs.
    for r in (weekly, quarterly):
        assert (r.total_payoff) == pytest.approx(0.0)
    # The frequency penalty: weekly brokerage per year and per premium dollar is
    # materially larger, and its net ROI is strictly worse.
    assert weekly.total_brokerage / years > 10.0 * (quarterly.total_brokerage / years)
    assert (weekly.total_brokerage / weekly.total_premium) > (
        quarterly.total_brokerage / quarterly.total_premium
    )
    assert weekly.roi_on_premium < quarterly.roi_on_premium


def test_annualized_return_geometric_pinned_cases() -> None:
    """Pinned against hand-computed geometric annualization: (1+roi)**(1/years)-1."""
    assert annualized_return(-0.72, 4.0) == pytest.approx(-0.27257, abs=1e-4)
    assert annualized_return(1.0, 4.0) == pytest.approx(0.18921, abs=1e-4)
    assert annualized_return(-1.0, 4.0) == -1.0
    assert annualized_return(-1.5, 4.0) == -1.0  # can't lose more than the premium; clamped
    assert annualized_return(0.37, 0.0) == 0.37  # years=0 passes total_roi through unchanged


def test_annualized_so_far_curve_pinned_and_skips_short_horizons() -> None:
    """Hand-built settled rolls -> hand-computed running annualized ROI. The
    first roll (< a quarter-year elapsed) is skipped; the rest annualize the
    cumulative net-on-premium over the actual elapsed years."""
    first_entry = dt.date(2021, 1, 1)
    settled = [
        (dt.date(2021, 2, 1), -500.0),  # 31 days -> 0.085 yr, skipped
        (dt.date(2021, 5, 1), 2000.0),  # 120 days -> 0.329 yr
        (dt.date(2021, 12, 1), -600.0),  # 334 days -> 0.914 yr
    ]
    points = annualized_so_far_curve(settled, first_entry_date=first_entry, notional=1000.0)

    assert len(points) == 2  # the 31-day point is below MIN_YEARS_FOR_ANNUALIZED
    assert [p.date for p in points] == [dt.date(2021, 5, 1), dt.date(2021, 12, 1)]
    # roll 2: cum net 1500 over 2 rolls -> roi 0.75 over 120/365.25 years.
    assert points[0].annualized == pytest.approx(annualized_return(0.75, 120 / 365.25))
    # roll 3: cum net 900 over 3 rolls -> roi 0.30 over 334/365.25 years.
    assert points[1].annualized == pytest.approx(annualized_return(0.30, 334 / 365.25))
    assert points[1].annualized == pytest.approx(0.3323, abs=1e-3)  # hardcoded sanity
    # Dates are strictly increasing (monotone horizon).
    assert all(a.date < b.date for a, b in itertools.pairwise(points))


def test_annualized_so_far_curve_empty_when_all_horizons_too_short() -> None:
    first_entry = dt.date(2021, 1, 1)
    settled = [(dt.date(2021, 1, 20), 100.0), (dt.date(2021, 2, 10), -50.0)]  # both < 0.25 yr
    assert annualized_so_far_curve(settled, first_entry_date=first_entry, notional=1000.0) == []


def test_annualized_sharpe_pinned_against_hand_computation() -> None:
    """Known per-roll returns -> known annualized Sharpe. mean 0.05, sample std
    (ddof=1) 0.122474, rf/roll 0.04/12, times sqrt(12)."""
    returns = [0.10, -0.05, 0.20, -0.05]
    sharpe = annualized_sharpe(returns, rolls_per_year=12.0, rate=0.04)
    assert sharpe is not None
    assert sharpe == pytest.approx(1.3200, abs=1e-3)


def test_annualized_sharpe_guards() -> None:
    assert annualized_sharpe([0.1], rolls_per_year=12.0) is None  # < 2 rolls
    # Zero dispersion (identical returns, as on a flat path where every put
    # expires worthless for the same net) -> undefined ratio.
    assert annualized_sharpe([0.5, 0.5], rolls_per_year=12.0) is None
    assert annualized_sharpe([0.1, 0.2], rolls_per_year=0.0) is None  # no cadence


def test_run_put_roll_threads_annualized_so_far_and_sharpe() -> None:
    """The engine attaches the running-annualized curve (monotone expiry dates,
    each annualizing the cumulative net-on-premium over the elapsed years) and
    the per-roll annualized Sharpe."""
    n = 261  # 8-week rolls: entries 20,60,...,220 -> ~6 cycles spanning ~1 year
    prices = _flat_with_dips(n, {60: 70.0, 140: 75.0})
    iv = pd.Series(np.full(n, 0.30), index=prices.index)
    notional, tenor_w, years = 1000.0, 8.0, 10.0
    res = run_put_roll(
        prices,
        iv,
        asset="T",
        as_of=dt.date(2021, 6, 1),
        notional=notional,
        moneyness_pct=5.0,
        tenor_weeks=tenor_w,
        lookback_years=years,
    )

    # A couple of mid-horizon points exist, all on expiry dates, in order.
    assert len(res.annualized_so_far) >= 2
    expiry_dates = [c.expiry_date for c in res.cycles]
    assert all(p.date in expiry_dates for p in res.annualized_so_far)
    assert [p.date for p in res.annualized_so_far] == sorted(p.date for p in res.annualized_so_far)
    # Every emitted point is past the quarter-year floor.
    first_entry = res.cycles[0].entry_date
    for p in res.annualized_so_far:
        assert (p.date - first_entry).days / 365.25 >= MIN_YEARS_FOR_ANNUALIZED

    # The final point's roi basis is exactly roi_on_premium (same numerator and
    # denominator), annualized over the actual elapsed span -> reconstructs exactly.
    last = res.annualized_so_far[-1]
    assert last.date == res.cycles[-1].expiry_date
    elapsed_years = (last.date - first_entry).days / 365.25
    assert last.annualized == pytest.approx(annualized_return(res.roi_on_premium, elapsed_years))

    # Sharpe matches the pure helper over the per-roll returns — paced by the
    # TRADED span, the same clock `annualized_return` uses nine lines above.
    # These were on different clocks until 2026-09-09: `rolls_per_year` divided
    # by the requested window while `annualized_so_far` used the elapsed one,
    # an inconsistency visible inside this single test. On a real SPY market
    # run that understated |Sharpe| by 1.7x — flattering, for a losing
    # strategy.
    expected_sharpe = annualized_sharpe(
        [c.net / notional for c in res.cycles],
        rolls_per_year=res.n_cycles / elapsed_years,
        rate=res.rate,
    )
    assert res.sharpe_ratio == pytest.approx(expected_sharpe)
    # Benchmark hurdle is engine-agnostic; the pure roll leaves it unset.
    assert res.benchmark_annualized is None


def test_price_path_spans_the_traded_window() -> None:
    """price_path covers exactly first-entry → last-expiry, aligned with the
    equity curve, so the tape's stock line and PnL share one x-axis."""
    prices = _flat_with_dips(101, {60: 70.0, 100: 98.0})
    iv = pd.Series(np.full(101, 0.30), index=prices.index)
    res = run_put_roll(
        prices,
        iv,
        asset="T",
        as_of=dt.date(2021, 6, 1),
        notional=1000.0,
        moneyness_pct=5.0,
        tenor_weeks=8.0,
        lookback_years=10,
    )
    assert res.price_path
    assert res.price_path[0].date == res.cycles[0].entry_date == res.equity_curve[0].date
    assert res.price_path[-1].date == res.cycles[-1].expiry_date == res.equity_curve[-1].date
    # Prices are the real underlying (the crafted dips show through).
    prices_by_date = {p.date: p.price for p in res.price_path}
    assert prices_by_date[res.cycles[0].expiry_date] == pytest.approx(70.0)


def test_mtm_curve_spans_price_path_and_shares_dates() -> None:
    """The daily mark-to-market curve has one point per trading day over the
    same window as price_path, on the same dates (so it plots on one x-axis)."""
    prices = _flat_with_dips(101, {60: 70.0, 100: 98.0})
    iv = pd.Series(np.full(101, 0.30), index=prices.index)
    res = run_put_roll(
        prices,
        iv,
        asset="T",
        as_of=dt.date(2021, 6, 1),
        notional=1000.0,
        moneyness_pct=5.0,
        tenor_weeks=8.0,
        lookback_years=10,
    )
    assert len(res.mtm_curve) == len(res.price_path)
    assert [p.date for p in res.mtm_curve] == [p.date for p in res.price_path]


def test_mtm_curve_converges_to_realized_equity_at_every_expiry() -> None:
    """Load-bearing correctness (net of brokerage): because contracts * premium
    == notional, the raw BS mark at t_years=0 is the intrinsic payoff, so at a
    roll's expiry ``unrealized = contracts*intrinsic - notional - cost == net``.
    The daily curve therefore meets the realized equity curve on every expiry
    date -- except that a roll re-enters on the *same* date it expires, and the
    daily mark steps down by that new roll's entry brokerage, so at a shared
    expiry/entry date the curve sits below realized equity by exactly the new
    roll's cost (and equals it at the final expiry, where nothing re-enters)."""
    prices = _flat_with_dips(101, {60: 70.0, 100: 98.0})
    iv = pd.Series(np.full(101, 0.30), index=prices.index)
    res = run_put_roll(
        prices,
        iv,
        asset="T",
        as_of=dt.date(2021, 6, 1),
        notional=1000.0,
        moneyness_pct=5.0,
        tenor_weeks=8.0,
        lookback_years=10,
    )
    mtm_by_date = {p.date: p.cum_pnl for p in res.mtm_curve}
    equity_by_date = {p.date: p.cum_pnl for p in res.equity_curve}
    entry_cost_on = {c.entry_date: c.cost for c in res.cycles}
    for cyc in res.cycles:
        opened_cost = entry_cost_on.get(cyc.expiry_date, 0.0)  # a roll re-entering that day
        assert mtm_by_date[cyc.expiry_date] == pytest.approx(
            equity_by_date[cyc.expiry_date] - opened_cost
        )
    # The final expiry has no re-entry, so the curve fully converges there.
    last = res.cycles[-1].expiry_date
    assert mtm_by_date[last] == pytest.approx(equity_by_date[last])
    assert mtm_by_date[last] == pytest.approx(res.net_pnl)


def test_mtm_curve_moves_intra_cycle_on_a_sharp_drop() -> None:
    """The whole point of the daily mark: a sharp drop inside an open cycle
    lifts the mark that day, so the curve is not flat between expiries."""
    # first entry idx 20, tenor 20d -> cycles 20->40, 40->60, 60->80; the dip at
    # day 50 sits strictly inside the 40->60 roll (not on any entry/expiry).
    prices = _flat_with_dips(85, {50: 70.0})
    iv = trailing_realized_vol(prices)  # real backward-looking proxy (spikes after the dip)
    res = run_put_roll(
        prices,
        iv,
        asset="T",
        as_of=dt.date(2021, 6, 1),
        notional=1000.0,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
        lookback_years=10,
    )
    mtm_by_date = {p.date: p.cum_pnl for p in res.mtm_curve}
    drop_day = prices.index[50].date()
    prior_day = prices.index[49].date()
    assert mtm_by_date[drop_day] > mtm_by_date[prior_day]


def test_sigma_is_the_positive_iv_proxy_used_at_entry() -> None:
    """Each cycle's sigma records the (clamped) IV proxy its premium was priced with."""
    prices = _flat_with_dips(101, {60: 70.0, 100: 98.0})
    iv = pd.Series(np.full(101, 0.30), index=prices.index)
    res = run_put_roll(
        prices,
        iv,
        asset="T",
        as_of=dt.date(2021, 6, 1),
        notional=1000.0,
        moneyness_pct=5.0,
        tenor_weeks=8.0,
        lookback_years=10,
    )
    for cyc in res.cycles:
        assert cyc.sigma > 0.0
        assert cyc.sigma == pytest.approx(0.30)


def test_deeper_oom_is_cheaper_per_cycle() -> None:
    """Property: a further out-of-the-money put costs less, so the same
    budget buys strictly more contracts (a monotonicity the pricer guarantees
    and the engine must not scramble)."""
    prices = _flat_with_dips(80, {60: 60.0})
    iv = pd.Series(np.full(80, 0.35), index=prices.index)
    common = dict(
        asset="T", as_of=dt.date(2021, 6, 1), notional=1000.0, tenor_weeks=8.0, lookback_years=10
    )
    near = run_put_roll(prices, iv, moneyness_pct=3.0, **common)
    far = run_put_roll(prices, iv, moneyness_pct=12.0, **common)
    assert far.cycles[0].premium < near.cycles[0].premium
    assert far.cycles[0].contracts > near.cycles[0].contracts


def test_non_finite_iv_entry_is_skipped_not_priced() -> None:
    """If the IV proxy is NaN at a would-be entry (too little trailing
    history), that entry steps forward rather than pricing on a stub."""
    prices = _flat_with_dips(90, {70: 60.0})
    iv = pd.Series(np.full(90, 0.30), index=prices.index)
    iv.iloc[IV_WINDOW] = np.nan  # first candidate entry has no vol
    res = run_put_roll(
        prices,
        iv,
        asset="T",
        as_of=dt.date(2021, 6, 1),
        notional=1000.0,
        moneyness_pct=5.0,
        tenor_weeks=8.0,
        lookback_years=10,
    )
    assert res.cycles[0].entry_date != prices.index[IV_WINDOW].date()


def test_run_put_roll_validates_inputs() -> None:
    prices = _flat_with_dips(60, {})
    iv = pd.Series(np.full(60, 0.3), index=prices.index)
    ok = dict(
        asset="T",
        as_of=dt.date(2021, 6, 1),
        notional=1000.0,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
        lookback_years=10,
    )
    with pytest.raises(ValueError, match="same date index"):
        run_put_roll(prices, iv.iloc[:-1], **ok)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="moneyness_pct"):
        run_put_roll(prices, iv, **{**ok, "moneyness_pct": 0.0})  # type: ignore[arg-type]


def test_run_put_roll_raises_when_window_too_short() -> None:
    prices = _flat_with_dips(IV_WINDOW + 3, {})
    iv = pd.Series(np.full(IV_WINDOW + 3, 0.3), index=prices.index)
    with pytest.raises(LookupError, match="not enough price history"):
        run_put_roll(
            prices,
            iv,
            asset="T",
            as_of=dt.date(2021, 6, 1),
            notional=1000.0,
            moneyness_pct=5.0,
            tenor_weeks=8.0,
            lookback_years=10,
        )


def test_trailing_realized_vol_is_backward_looking() -> None:
    prices = _flat_with_dips(60, {})  # flat -> zero returns -> zero realized vol
    rv = trailing_realized_vol(prices)
    assert rv.iloc[:IV_WINDOW].isna().all()  # not enough history early
    assert rv.iloc[IV_WINDOW:].abs().max() == pytest.approx(0.0)  # flat path, no vol


# ---------- orchestration: point-in-time ----------


def _write_ohlcv(
    store: DeltaLakeStore, ingest_date: dt.date, closes: np.ndarray, symbol: str = "spy"
) -> None:
    n = len(closes)
    df = pd.DataFrame(
        {
            "symbol": symbol.upper(),
            "trade_date": pd.date_range(end=ingest_date, periods=n, freq="B"),
            "open": closes,
            "high": closes * 1.001,
            "low": closes * 0.999,
            "close": closes,
            "volume": np.full(n, 1_000_000, dtype=int),
            "adj_close": closes,
        }
    )
    store.write_bronze(dataset_id(symbol), ingest_date, df)


def test_compute_put_backtest_respects_no_look_ahead(tmp_path: Path) -> None:
    """A backtest as-of an earlier ingest date must not reflect a later
    snapshot's (restated) prices."""
    store = DeltaLakeStore(tmp_path)
    day1, day2 = dt.date(2026, 3, 1), dt.date(2026, 3, 2)
    rng = np.random.default_rng(3)
    closes1 = 100 + np.cumsum(rng.normal(0, 1.2, size=80))
    closes1 = np.clip(closes1, 20, None)
    _write_ohlcv(store, day1, closes1)

    kw = dict(asset="spy", notional=1000.0, moneyness_pct=5.0, tenor_weeks=4.0, lookback_years=10)
    res1 = compute_put_backtest(store, as_of=day1, **kw)  # type: ignore[arg-type]

    # A later snapshot with entirely different prices must not change the day1 read.
    closes2 = np.clip(100 + np.cumsum(rng.normal(0, 3.0, size=81)), 20, None)
    _write_ohlcv(store, day2, closes2)
    res1_again = compute_put_backtest(store, as_of=day1, **kw)  # type: ignore[arg-type]

    assert res1_again.model_dump() == res1.model_dump()
    res2 = compute_put_backtest(store, as_of=day2, **kw)  # type: ignore[arg-type]
    assert res2.net_pnl != res1.net_pnl  # the restatement really would have differed


def test_compute_put_backtest_sizing_mode_overrides_notional(tmp_path: Path) -> None:
    """``sizing_mode`` resolves the budget and ``notional`` is ignored; a
    single asset is one leg (``n_legs=1``), so ``FixedPremium(x)`` must
    reproduce plain ``notional=x`` exactly, and ``WealthFraction`` must
    resolve to ``alpha * wealth``."""
    store = DeltaLakeStore(tmp_path)
    day1 = dt.date(2026, 3, 1)
    rng = np.random.default_rng(3)
    closes = np.clip(100 + np.cumsum(rng.normal(0, 1.2, size=80)), 20, None)
    _write_ohlcv(store, day1, closes)

    kw = dict(asset="spy", as_of=day1, moneyness_pct=5.0, tenor_weeks=4.0, lookback_years=10)
    plain = compute_put_backtest(store, notional=2000.0, **kw)  # type: ignore[arg-type]
    via_fixed = compute_put_backtest(store, notional=1.0, sizing_mode=FixedPremium(2000.0), **kw)  # type: ignore[arg-type]
    assert via_fixed.model_dump() == plain.model_dump()

    # alpha/wealth chosen so alpha * wealth == 2000.0, same as FixedPremium
    # above, but with wealth large relative to the premium actually spent --
    # unlike alpha=0.5/wealth=4000.0, which resolves to the identical 2000.0
    # budget but lets 2 rolls' worth of premium (~4000) exceed the stated
    # wealth outright, making combined wealth go non-positive and
    # time_average_growth legitimately undefined (see
    # test_time_average_growth_undefined_cases_return_none).
    via_wealth = compute_put_backtest(
        store, notional=1.0, sizing_mode=WealthFraction(alpha=0.01, wealth=200_000.0), **kw
    )  # type: ignore[arg-type]
    assert via_wealth.notional == pytest.approx(2000.0)
    # WealthFraction also populates time_average_growth (see the dedicated
    # test below); every other field matches the plain notional run exactly.
    assert plain.time_average_growth is None
    assert via_wealth.time_average_growth is not None
    assert via_wealth.model_dump(exclude={"time_average_growth"}) == plain.model_dump(
        exclude={"time_average_growth"}
    )


def test_compute_put_backtest_time_average_growth_matches_the_pure_function(
    tmp_path: Path,
) -> None:
    """``compute_put_backtest`` must delegate to
    ``research.backtest.growth.time_average_growth`` with exactly the run's
    own ``price_path``/``mtm_curve`` and the ``WealthFraction``'s ``wealth`` --
    not a reimplementation -- for both a well-defined case and one where
    combined wealth goes non-positive (see the comment on
    ``test_compute_put_backtest_sizing_mode_overrides_notional`` for why this
    particular seed/window produces ruin at a smaller wealth)."""
    store = DeltaLakeStore(tmp_path)
    day1 = dt.date(2026, 3, 1)
    rng = np.random.default_rng(3)
    closes = np.clip(100 + np.cumsum(rng.normal(0, 1.2, size=80)), 20, None)
    _write_ohlcv(store, day1, closes)
    kw = dict(asset="spy", as_of=day1, moneyness_pct=5.0, tenor_weeks=4.0, lookback_years=10)

    for wealth in (200_000.0, 4_000.0):
        result = compute_put_backtest(
            store, notional=1.0, sizing_mode=WealthFraction(alpha=0.5, wealth=wealth), **kw
        )  # type: ignore[arg-type]
        expected = time_average_growth(
            [(p.date, p.price) for p in result.price_path],
            result.mtm_curve[-1].cum_pnl if result.mtm_curve else 0.0,
            wealth=wealth,
        )
        assert result.time_average_growth == expected


def test_compute_put_backtest_missing_symbol_raises(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(LookupError, match="no OHLCV"):
        compute_put_backtest(
            store,
            asset="nope",
            as_of=dt.date(2026, 3, 1),
            notional=1000.0,
            moneyness_pct=5.0,
            tenor_weeks=4.0,
            lookback_years=10,
        )


def test_backtest_prices_off_raw_close_not_adjusted_close(tmp_path: Path) -> None:
    """An option is written on the price that actually printed, so the engine
    must read ``close`` and never ``adj_close``.

    Every other bronze fixture in this file sets close == adj_close, which
    means none of them can tell the two apart -- this one deliberately makes
    them differ. Dividend back-adjustment lowers historical adj_close, so
    pricing off it would set strikes off a price that never traded: on real
    SPY data (2021-08-23) that was a 6.5% strike error, turning a nominal
    "5% OTM" put into an ~11% OTM one.
    """
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 1, 30)
    n = 120
    closes = np.full(n, 100.0)
    df = pd.DataFrame(
        {
            "symbol": "SPY",
            "trade_date": pd.date_range(end=ingest_date, periods=n, freq="B"),
            "open": closes,
            "high": closes * 1.001,
            "low": closes * 0.999,
            "close": closes,
            "volume": np.full(n, 1_000_000, dtype=int),
            # Half the level: if the engine ever reads this column the assert
            # below fails loudly rather than drifting quietly.
            "adj_close": closes * 0.5,
        }
    )
    store.write_bronze(dataset_id("spy"), ingest_date, df)

    prices, _ = load_asof_series(store, "spy", ingest_date)

    assert prices.iloc[-1] == pytest.approx(100.0)
    assert prices.iloc[0] == pytest.approx(100.0)
    # The adjusted series would have put every price at 50.0.
    assert not np.isclose(prices.to_numpy(), 50.0).any()
