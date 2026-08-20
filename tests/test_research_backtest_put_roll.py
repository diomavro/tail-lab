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
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.put_roll import (
    DEFAULT_RATE,
    IV_WINDOW,
    PREMIUM_FLOOR_FRAC,
    compute_put_backtest,
    run_put_roll,
    trailing_realized_vol,
)
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
        payoff = contracts * max(strike - px[entry + tenor_days], 0.0)
        expected.append(
            {"prem": prem, "contracts": contracts, "payoff": payoff, "net": payoff - notional}
        )

    assert res.n_cycles == 2
    for got, exp in zip(res.cycles, expected, strict=True):
        assert got.premium == pytest.approx(exp["prem"])
        assert got.contracts == pytest.approx(exp["contracts"])
        assert got.payoff == pytest.approx(exp["payoff"])
        assert got.net == pytest.approx(exp["net"])

    total_payoff = sum(e["payoff"] for e in expected)
    assert res.total_premium == pytest.approx(2 * notional)
    assert res.total_payoff == pytest.approx(total_payoff)
    assert res.net_pnl == pytest.approx(total_payoff - 2 * notional)
    assert res.roi_on_premium == pytest.approx((total_payoff - 2 * notional) / (2 * notional))
    # cycle 1 pays off (dip to 70 << strike 95), cycle 2 expires worthless (98 > 66.5).
    assert res.hit_rate == pytest.approx(0.5)
    assert res.worst_bleed_streak == 1
    assert res.biggest_payoff_mult == pytest.approx(expected[0]["payoff"] / notional)
    # equity curve: one seed point + one point per cycle, monotone bookkeeping.
    assert [p.cum_pnl for p in res.equity_curve] == pytest.approx(
        [0.0, expected[0]["net"], expected[0]["net"] + expected[1]["net"]]
    )


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
