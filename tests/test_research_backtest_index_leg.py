"""Tests for the Book's index leg (index_leg).

What these protect, and why it matters: SPY's total return understates the
S&P 500's gross total return, and an index leg that is too LOW flatters every
hedge. So the leg must (a) add back SPY's fee as dated, (b) build the
conservative leg's cash-drag add-back exactly as stated, (c) only borrow a
revised T-bill value where no point-in-time value exists, and (d) never let a
stale weekly feed silently shorten a window or quietly extend it past what a
missed run allows.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research import dividends
from tail_lab.research.backtest.hedge_overlay import run_hedge_overlay
from tail_lab.research.backtest.index_leg import (
    CASH_DRAG_YEARS,
    MAX_EXTENSION_DAYS,
    SPY_LISTING,
    Y_UNMEASURED,
    LegTag,
    _trailing_q,
    build_index_leg,
    fee_in_force,
    latest_vintage_rates,
    measured_leg,
)
from tail_lab.research.backtest.index_replication import DAYS_PER_YEAR
from tail_lab.research.dividends import IndexHistory, dividend_lookup


def spy_rows(
    index: pd.DatetimeIndex, *, daily: float = 0.0003, quarterly_div: float = 0.5
) -> pd.DataFrame:
    """Tiingo-shaped SPY rows: a price drifting ``daily``, a dividend of
    ``quarterly_div`` on the first session of each quarter, and an
    ``adj_close`` that reinvests it (Tiingo's verified convention)."""
    close = 100.0 * np.cumprod(np.r_[1.0, np.full(len(index) - 1, 1.0 + daily)])
    quarters = index.to_period("Q")
    div = np.zeros(len(index))
    div[np.flatnonzero(quarters[1:] != quarters[:-1]) + 1] = quarterly_div
    growth = np.r_[1.0, (close[1:] + div[1:]) / close[:-1]]
    return pd.DataFrame(
        {
            "symbol": "spy",
            "trade_date": index,
            "close": close,
            "adj_close": 50.0 * np.cumprod(growth),
            "div_cash": div,
            "split_factor": 1.0,
        }
    )


def spy_history(
    index: pd.DatetimeIndex, *, daily: float = 0.0003, quarterly_div: float = 0.5
) -> IndexHistory:
    """:func:`spy_rows` as the index leg receives them (rows plus yields)."""
    return IndexHistory.from_rows(spy_rows(index, daily=daily, quarterly_div=quarterly_div))


def rates_rows(
    obs: pd.DatetimeIndex, *, first_vintage: str, value: float, revised: float | None = None
) -> pd.DataFrame:
    """``DGS3MO`` observations all first published on ``first_vintage`` at
    ``value``; with ``revised``, re-published later at that value."""
    rows = [
        pd.DataFrame(
            {
                "series_id": "DGS3MO",
                "obs_date": obs,
                "value": value,
                "vintage_date": pd.Timestamp(first_vintage),
            }
        )
    ]
    if revised is not None:
        rows.append(
            pd.DataFrame(
                {
                    "series_id": "DGS3MO",
                    "obs_date": obs,
                    "value": revised,
                    "vintage_date": pd.Timestamp("2015-01-02"),
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def seed_spy(store: DeltaLakeStore, ingest: dt.date, index: pd.DatetimeIndex) -> None:
    store.write_bronze("tiingo_eod", ingest, spy_rows(index))


def _spx(index: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(1000.0 * np.cumprod(np.full(len(index), 1.0002)), index=index)


# ------------------------------------------------------------ the fee table


def test_the_fee_add_back_is_the_one_in_force_on_each_date() -> None:
    dates = pd.DatetimeIndex(["1995-06-01", "2005-09-30", "2005-10-03", "2007-01-31", "2007-02-01"])
    assert fee_in_force(dates).tolist() == [0.0020, 0.0020, 0.0010, 0.0010, 0.000945]


def test_a_flat_spy_grows_by_exactly_its_fee_over_a_year() -> None:
    """With a flat adj_close the base leg is pure add-back: a calendar year
    of it compounds to exactly 1 + fee, weekends included."""
    index = pd.bdate_range("2010-01-04", "2011-01-04")
    spy = spy_rows(index, daily=0.0, quarterly_div=0.0)
    leg = measured_leg(
        IndexHistory.from_rows(spy), _spx(index), None, as_of=index[-1].date(), snapshot_ids={}
    )
    years = (index[-1] - index[0]).days / DAYS_PER_YEAR
    assert float(leg.headline.iloc[-1] / leg.headline.iloc[0]) == pytest.approx(
        (1 + 0.000945) ** years, rel=1e-12
    )


def test_the_base_leg_is_spy_total_return_not_price() -> None:
    """adj_close carries the dividends; a leg built on close would drop ~2%/yr
    and flatter every hedge by it."""
    index = pd.bdate_range("2010-01-04", "2012-12-31")
    spy = spy_rows(index)
    leg = measured_leg(
        IndexHistory.from_rows(spy), _spx(index), None, as_of=index[-1].date(), snapshot_ids={}
    )
    years = (index[-1] - index[0]).days / DAYS_PER_YEAR
    tr = float(spy["adj_close"].iloc[-1] / spy["adj_close"].iloc[0])
    assert float(leg.headline.iloc[-1] / leg.headline.iloc[0]) == pytest.approx(
        tr * (1 + 0.000945) ** years, rel=1e-10
    )


# ------------------------------------------------------------ the conservative leg


def test_the_cash_drag_add_back_matches_a_hand_computation() -> None:
    """Close flat at 100, $0.50 a quarter: a measured q of -ln(1 - 2/100).
    Three years in, ``y`` is that q; with bills at 4% each day's conservative
    growth is base + (q/8) x (equity - bill)."""
    index = pd.bdate_range("2010-01-04", "2013-12-31")
    spy = spy_rows(index, daily=0.0)
    rates = rates_rows(
        pd.bdate_range("2009-01-02", "2013-12-31"), first_vintage="2005-06-28", value=4.0
    )
    leg = measured_leg(
        IndexHistory.from_rows(spy), _spx(index), rates, as_of=index[-1].date(), snapshot_ids={}
    )
    cons = leg.conservative
    assert cons is not None
    q = -math.log(1 - 2.0 / 100.0)
    for day in ("2013-06-04", "2013-07-01", "2013-11-19"):
        t = index.get_loc(pd.Timestamp(day))
        gap = (index[t] - index[t - 1]).days
        equity = float(leg.headline.iloc[t] / leg.headline.iloc[t - 1]) - 1.0
        bill = 1.04 ** (gap / DAYS_PER_YEAR) - 1.0
        expected = 1.0 + equity + q / 8 * (equity - bill)
        assert float(cons.iloc[t] / cons.iloc[t - 1]) == pytest.approx(expected, rel=1e-12), day


def test_the_cash_drag_holds_dividends_an_eighth_of_a_year() -> None:
    """Quarterly distributions with receipts spread across the quarter: an
    average dividend dollar waits 1/8 year in SPY's cash (the docs' y/8)."""
    assert CASH_DRAG_YEARS == 1 / 8


def test_the_legs_share_one_calendar() -> None:
    """A conservative leg on a different calendar would silently shorten
    whatever window reads it."""
    index = pd.bdate_range("2010-01-04", "2012-12-31")
    rates = rates_rows(
        pd.bdate_range("2009-01-02", "2012-12-31"), first_vintage="2005-06-28", value=1.0
    )
    leg = measured_leg(
        spy_history(index), _spx(index), rates, as_of=index[-1].date(), snapshot_ids={}
    )
    assert [k for k, _ in leg.rows] == [LegTag(leg="base"), LegTag(leg="conservative")]
    assert all(s.index.equals(leg.headline.index) for _, s in leg.rows)


def test_before_point_in_time_bills_the_latest_vintage_is_used_and_labelled() -> None:
    """ADR 0009's stated exception: before DGS3MO's first ALFRED vintage
    (2005-06-28) no point-in-time value exists, so the leg reads the series'
    LATEST vintage there -- and only there."""
    obs = pd.bdate_range("1990-01-02", "2015-12-31")
    rates = rates_rows(obs, first_vintage="2005-06-28", value=4.0, revised=6.0)
    dates = pd.DatetimeIndex(["1995-03-01"])
    assert latest_vintage_rates(rates, dates).tolist() == [6.0]
    index = pd.bdate_range("1994-01-03", "2015-12-31")  # after the 2015 revision
    leg = measured_leg(
        spy_history(index, daily=0.0), _spx(index), rates, as_of=index[-1].date(), snapshot_ids={}
    )
    assert leg.basis.bill_point_in_time_from == dt.date(2005, 6, 28)
    cons, base = leg.conservative, leg.headline
    assert cons is not None

    def implied_bill(day: str) -> float:
        t = index.get_loc(pd.Timestamp(day))
        equity = float(base.iloc[t] / base.iloc[t - 1]) - 1.0
        add = float(cons.iloc[t] / cons.iloc[t - 1]) - 1.0 - equity
        q = -math.log(1 - 2.0 / 100.0)  # close flat at 100, $2 a year
        gap = (index[t] - index[t - 1]).days
        bill = equity - add / (q / 8)
        return ((1 + bill) ** (DAYS_PER_YEAR / gap) - 1) * 100

    assert implied_bill("1999-06-02") == pytest.approx(6.0, abs=0.05)  # revised, latest vintage
    assert implied_bill("2010-06-02") == pytest.approx(
        4.0, abs=0.05
    )  # first vintage, point-in-time


def test_bills_that_start_late_leave_the_conservative_leg_blank_before_them() -> None:
    index = pd.bdate_range("2000-01-03", "2012-12-31")
    rates = rates_rows(
        pd.bdate_range("2004-01-02", "2012-12-31"), first_vintage="2005-06-28", value=2.0
    )
    leg = measured_leg(
        spy_history(index), _spx(index), rates, as_of=index[-1].date(), snapshot_ids={}
    )
    cons = leg.conservative
    assert cons is not None and leg.basis.conservative_from == dt.date(2004, 1, 5)
    assert cons[: pd.Timestamp("2004-01-02")].isna().all()
    assert not cons[pd.Timestamp("2004-01-05") :].isna().any()


def test_without_rates_there_is_no_conservative_leg_and_the_reason_is_named() -> None:
    index = pd.bdate_range("2010-01-04", "2012-12-31")
    leg = measured_leg(
        spy_history(index), _spx(index), None, as_of=index[-1].date(), snapshot_ids={}
    )
    assert leg.conservative is None
    assert [k for k, _ in leg.rows] == [LegTag(leg="base")]
    assert leg.basis.conservative_reason == "sizing needs T-bills for the cash-drag check"


# ------------------------------------------------------------ window end


@pytest.mark.parametrize(("lag", "extended"), [(9, True), (MAX_EXTENSION_DAYS, True), (15, False)])
def test_a_trailing_weekly_feed_is_extended_on_spx_price_up_to_two_weeks(
    lag: int, extended: bool
) -> None:
    """The weekly Tiingo run can trail the Cboe calendar by 8-9 days. Up to 14
    the leg carries on SPX price return (labelled, no dividend accrual) so
    full windows stay full; past 14 it does not, and the gap is reported."""
    cboe = pd.date_range("2010-01-01", "2015-06-30", freq="D")
    last = cboe[-1] - pd.Timedelta(days=lag)
    spy_days = cboe[cboe <= last]
    leg = measured_leg(
        spy_history(spy_days), _spx(cboe), None, as_of=cboe[-1].date(), snapshot_ids={}
    )
    sources = [s.source for s in leg.basis.spans]
    if extended:
        assert sources == ["spy_total_return", "spx_price_only"]
        assert leg.basis.spans[-1].days == lag
        assert leg.headline.index[-1] == cboe[-1]
        # No dividend accrual on the extension: it moves exactly with SPX.
        tail = leg.headline[leg.headline.index > last]
        spx = _spx(cboe)
        assert float(tail.iloc[-1] / tail.iloc[0]) == pytest.approx(
            float(spx.iloc[-1] / spx[tail.index[0]]), rel=1e-12
        )
        assert leg.basis.unextended_gap_days == 0
        assert leg.basis.unextended_reason is None
    else:
        assert sources == ["spy_total_return"]
        assert leg.basis.unextended_gap_days == lag
        assert leg.basis.unextended_reason == "missed_runs"
        assert leg.headline.index[-1] == last


@pytest.mark.parametrize(("lag", "clipped"), [(9, False), (15, True)])
def test_the_full_window_is_clipped_only_past_the_extension(lag: int, clipped: bool) -> None:
    cboe = pd.bdate_range("2004-01-02", "2017-06-30")
    program = pd.Series(np.cumprod(np.full(len(cboe), 1.0001)), index=cboe)
    last = cboe[-1] - pd.Timedelta(days=lag)
    leg = measured_leg(
        spy_history(cboe[cboe <= last]), _spx(cboe), None, as_of=cboe[-1].date(), snapshot_ids={}
    )
    result = run_hedge_overlay(_spx(cboe), {"PPUT": program}, as_of=cboe[-1].date(), leg=leg)
    full = next(w for w in result.programs[0].windows if w.key == "full")
    assert full.clipped is clipped


def test_the_full_window_starts_at_spys_listing_and_is_not_called_clipped() -> None:
    """PPUT's history opens in 1986; the measured leg in 1993. The window
    asked for is the clamp, so it is a full window -- and says where it
    starts."""
    cboe = pd.bdate_range("1986-06-30", "1999-12-31")
    program = pd.Series(np.cumprod(np.full(len(cboe), 1.0002)), index=cboe)
    spy_days = cboe[cboe >= pd.Timestamp(SPY_LISTING)]
    leg = measured_leg(
        spy_history(spy_days), _spx(cboe), None, as_of=cboe[-1].date(), snapshot_ids={}
    )
    result = run_hedge_overlay(_spx(cboe), {"PPUT": program}, as_of=cboe[-1].date(), leg=leg)
    (prog,) = result.programs
    full = next(w for w in prog.windows if w.key == "full")
    assert full.requested_start == SPY_LISTING == full.start
    assert not full.clipped
    assert prog.first_date == dt.date(1986, 6, 30)  # the program's own, unchanged
    assert result.dividend_yield is None and result.dividend.source == "measured"


# ------------------------------------------------------------ reading the store


def _seed_cboe_spx(store: DeltaLakeStore, ingest: dt.date, index: pd.DatetimeIndex) -> None:
    store.write_bronze(
        "cboe_strategy",
        ingest,
        pd.DataFrame({"index_symbol": "SPX", "trade_date": index, "close": _spx(index).to_numpy()}),
    )


def test_build_falls_back_to_the_assumed_yield_without_tiingo(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    index = pd.bdate_range("2010-01-04", "2012-12-31")
    leg = build_index_leg(store, index[-1].date(), spx=_spx(index))
    assert leg.basis.source == "assumed" and leg.basis.assumed_yield == 0.019
    assert "no tiingo_eod snapshot" in (leg.basis.assumed_reason or "")


def test_build_falls_back_when_tiingo_has_no_spy(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    index = pd.bdate_range("2010-01-04", "2012-12-31")
    store.write_bronze("tiingo_eod", index[-1].date(), spy_rows(index).assign(symbol="qqq"))
    leg = build_index_leg(store, index[-1].date(), spx=_spx(index))
    assert leg.basis.source == "assumed"
    assert "no SPY history" in (leg.basis.assumed_reason or "")


def test_build_reads_tiingo_and_rates_point_in_time_and_cites_both(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    index = pd.bdate_range("2010-01-04", "2012-12-31")
    ingest = index[-1].date()
    seed_spy(store, ingest, index)
    store.write_bronze(
        "rates",
        ingest,
        rates_rows(pd.bdate_range("2009-01-02", ingest), first_vintage="2005-06-28", value=1.0),
    )
    leg = build_index_leg(store, ingest, spx=_spx(index))
    assert leg.basis.source == "measured" and leg.conservative is not None
    assert set(leg.basis.snapshot_ids) == {"tiingo_eod", "rates"}
    # A day earlier, neither snapshot is known: the fallback, not a peek.
    before = build_index_leg(store, ingest - dt.timedelta(days=1), spx=_spx(index))
    assert before.basis.source == "assumed"


def test_a_store_bug_is_not_reported_as_absent_dividends() -> None:
    class BrokenStore:
        def bronze_snapshot_id(self, dataset: str, as_of: dt.date) -> str:
            raise KeyError("missing column")

    index = pd.bdate_range("2010-01-04", "2010-06-30")
    with pytest.raises(KeyError, match="missing column"):
        build_index_leg(BrokenStore(), index[-1].date(), spx=_spx(index))  # type: ignore[arg-type]


def test_no_extension_when_spx_lacks_tiingos_last_session() -> None:
    """Chaining from an earlier SPX day would count that day's move twice
    (it is already inside the leg's last level); leave the window clipped."""
    cboe = pd.bdate_range("2020-01-02", "2020-03-31")
    last = pd.Timestamp("2020-03-20")
    spx = _spx(cboe).drop(last)
    leg = measured_leg(
        spy_history(cboe[cboe <= last]), spx, None, as_of=cboe[-1].date(), snapshot_ids={}
    )
    assert leg.headline.index[-1] == last
    assert leg.basis.unextended_gap_days == (cboe[-1] - last).days
    # A missing SPX day is not a missed run, and must not be called one.
    assert leg.basis.unextended_reason == "no_spx_anchor"


def test_vintages_after_as_of_never_reach_the_splice() -> None:
    """A frame carrying a later revision (a 2015 vintage) must not move a leg
    built as of 2006: the latest-vintage splice is latest as of as_of."""
    obs = pd.bdate_range("1995-01-02", "2006-12-29")
    index = pd.bdate_range("1998-01-02", "2006-12-29")
    as_of = index[-1].date()
    clean = rates_rows(obs, first_vintage="2005-06-28", value=4.0)
    leaky = rates_rows(obs, first_vintage="2005-06-28", value=4.0, revised=9.0)
    a = measured_leg(spy_history(index), _spx(index), clean, as_of=as_of, snapshot_ids={})
    b = measured_leg(spy_history(index), _spx(index), leaky, as_of=as_of, snapshot_ids={})
    pd.testing.assert_series_equal(a.conservative, b.conservative)  # type: ignore[arg-type]


def test_the_cash_drag_yield_reads_only_month_ends_already_past() -> None:
    """ADR 0009 on the conservative leg. SPY steps its quarterly dividend from
    $0.50 to $1.00 in 2012 at a flat $100, so the month-end q readings change
    month by month. Each day's growth must use ``y`` as known at the START of
    its period: the mean of the last twelve month-end readings dated on or
    before the previous session. Reading the next month-end, the day's own
    reading, or one month instead of twelve each gives a different number."""
    index = pd.bdate_range("2010-01-04", "2013-12-31")
    spy = spy_rows(index, daily=0.0)
    paid = spy["div_cash"] > 0
    spy.loc[paid & (spy["trade_date"] >= pd.Timestamp("2012-01-01")), "div_cash"] = 1.0
    rates = rates_rows(
        pd.bdate_range("2009-01-02", "2013-12-31"), first_vintage="2005-06-28", value=4.0
    )
    leg = measured_leg(
        IndexHistory.from_rows(spy), _spx(index), rates, as_of=index[-1].date(), snapshot_ids={}
    )
    cons = leg.conservative
    assert cons is not None

    # By hand: on a flat $100 the indicated D is the last four payments.
    pay_days = spy.loc[spy["div_cash"] > 0, ["trade_date", "div_cash"]]
    months = index.to_period("M")
    month_ends = index[np.r_[np.flatnonzero(months[1:] != months[:-1]), len(index) - 1]]

    def reading(day: pd.Timestamp) -> float:
        last4 = pay_days[pay_days["trade_date"] <= day]["div_cash"].tail(4)
        return -math.log(1 - float(last4.sum()) / 100.0)

    def y_known_on(day: pd.Timestamp) -> float:
        ends = [e for e in month_ends if e <= day][-12:]
        return float(np.mean([reading(e) for e in ends]))

    for day in ("2012-05-31", "2012-08-31", "2012-11-15"):
        t = index.get_loc(pd.Timestamp(day))
        y = y_known_on(index[t - 1])
        gap = (index[t] - index[t - 1]).days
        equity = float(leg.headline.iloc[t] / leg.headline.iloc[t - 1]) - 1.0
        bill = 1.04 ** (gap / DAYS_PER_YEAR) - 1.0
        expected = 1.0 + equity + y / 8 * (equity - bill)
        assert float(cons.iloc[t] / cons.iloc[t - 1]) == pytest.approx(expected, rel=1e-12), day
    # ...and the readings really do move, so the test can tell rules apart.
    assert y_known_on(pd.Timestamp("2012-05-30")) != pytest.approx(
        y_known_on(pd.Timestamp("2012-05-31"))
    )


def test_each_day_earns_the_bill_rate_known_at_its_start() -> None:
    """Interest over (t-1, t] accrues at the rate in force on t-1 -- the
    rate of the latest observation before t-1. Bills step from 2% to 8% on
    the 2013-06-03 observation: (06-03, 06-04] still earns 2% (06-03's rate
    was set by 05-31), and (06-04, 06-05] the first 8%. Reading the next
    day's rate would put 8% on 06-04 a day early."""
    index = pd.bdate_range("2010-01-04", "2013-12-31")
    obs = pd.bdate_range("2009-01-02", "2013-12-31")
    rates = rates_rows(obs, first_vintage="2005-06-28", value=2.0)
    rates.loc[pd.to_datetime(rates["obs_date"]) >= pd.Timestamp("2013-06-03"), "value"] = 8.0
    leg = measured_leg(
        spy_history(index, daily=0.0), _spx(index), rates, as_of=index[-1].date(), snapshot_ids={}
    )
    cons, base = leg.conservative, leg.headline
    assert cons is not None
    q = -math.log(1 - 2.0 / 100.0)

    def implied_bill(day: str) -> float:
        t = index.get_loc(pd.Timestamp(day))
        equity = float(base.iloc[t] / base.iloc[t - 1]) - 1.0
        add = float(cons.iloc[t] / cons.iloc[t - 1]) - 1.0 - equity
        gap = (index[t] - index[t - 1]).days
        bill = equity - add / (q / 8)
        return ((1 + bill) ** (DAYS_PER_YEAR / gap) - 1) * 100

    assert implied_bill("2013-06-04") == pytest.approx(2.0, abs=0.01)
    assert implied_bill("2013-06-05") == pytest.approx(8.0, abs=0.01)


def test_an_unknown_yield_is_left_out_of_y_not_counted_as_zero() -> None:
    """Between SPY's first and second payment ``q`` is unknown. Counting
    those months as 0 would understate the cash drag for a year; they are
    left out of the trailing mean instead."""
    index = pd.bdate_range("2010-01-04", "2011-03-31")
    spy = spy_rows(index, daily=0.0)
    y = _trailing_q(IndexHistory.from_rows(spy), pd.DatetimeIndex([pd.Timestamp("2011-03-31")]))
    # Apr-Jun 2010 are unknown (one payment); Jul 2010-Mar 2011 all read q.
    assert y[0] == pytest.approx(-math.log(1 - 2.0 / 100.0), rel=1e-12)


def test_spy_rows_after_as_of_never_reach_the_leg() -> None:
    index = pd.bdate_range("2010-01-04", "2012-12-31")
    as_of = dt.date(2011, 6, 30)
    leg = measured_leg(spy_history(index), _spx(index), None, as_of=as_of, snapshot_ids={})
    assert leg.headline.index[-1] <= pd.Timestamp(as_of)
    assert leg.basis.spans[-1].end <= as_of


def test_months_before_spys_first_dividend_are_not_averaged_in_as_zero() -> None:
    """SPY listed in January 1993 and first paid in March: the trust held its
    constituents' dividends from day one, so its pre-dividend ``non_payer``
    months are no reading of ``y``. Averaged in as zero they dragged 1993's
    ``y`` to 0.8%. Here SPY pays from April 2010: on 2010-12-31 the trailing
    twelve months hold Jan-Mar ``non_payer``, Apr-Jun ``unknown`` and
    Jul-Dec measured, and ``y`` is the measured readings' mean -- not 2/3 of
    it. Before the first measured reading ``y`` is that first reading."""
    index = pd.bdate_range("2010-01-04", "2011-03-31")
    q = -math.log(1 - 2.0 / 100.0)
    y = _trailing_q(
        spy_history(index, daily=0.0),
        pd.DatetimeIndex(["2010-01-04", "2010-02-15", "2010-06-30", "2010-12-31"]),
    )
    assert y.tolist() == pytest.approx([q, q, q, q], rel=1e-12)


def test_a_full_history_from_spys_listing_still_gets_a_size() -> None:
    """The pre-dividend months must not withhold the whole full-history
    window: ``y`` starts from the first measured reading, so the
    conservative leg exists from SPY's first session."""
    index = pd.bdate_range(SPY_LISTING, "2000-12-29")
    rates = rates_rows(
        pd.bdate_range("1990-01-02", "2000-12-29"), first_vintage="1990-01-02", value=3.0
    )
    leg = measured_leg(
        spy_history(index), _spx(index), rates, as_of=index[-1].date(), snapshot_ids={}
    )
    assert leg.basis.conservative_from == index[0].date()
    assert leg.basis.conservative_reason is None
    program = pd.Series(np.cumprod(np.full(len(index), 1.0002)), index=index)
    result = run_hedge_overlay(_spx(index), {"PPUT": program}, as_of=index[-1].date(), leg=leg)
    full = next(w for w in result.programs[0].windows if w.key == "full")
    assert full.sizing.recommended_ratio is not None
    assert set(full.sizing.margin_by_leg) == {"base", "conservative"}


def test_an_unmeasured_y_withholds_the_size_and_says_why() -> None:
    """If SPY's yield is never measured (here one payment on record, so
    every reading is ``unknown``), ``y`` must not silently become 0 -- that
    makes the conservative leg equal the base and lets a size through on what
    is really one leg. The leg is not built, and the size is withheld."""
    index = pd.bdate_range("2000-01-03", "2012-12-31")
    spy = spy_rows(index)
    first = spy.index[spy["div_cash"] > 0][0]
    spy.loc[spy.index != first, "div_cash"] = 0.0
    rates = rates_rows(
        pd.bdate_range("1999-01-04", "2012-12-31"), first_vintage="2005-06-28", value=3.0
    )
    leg = measured_leg(
        IndexHistory.from_rows(spy), _spx(index), rates, as_of=index[-1].date(), snapshot_ids={}
    )
    assert leg.conservative is None
    assert leg.basis.conservative_from is None
    assert leg.basis.conservative_reason == Y_UNMEASURED
    program = pd.Series(np.cumprod(np.full(len(index), 1.0002)), index=index)
    result = run_hedge_overlay(_spx(index), {"PPUT": program}, as_of=index[-1].date(), leg=leg)
    for window in result.programs[0].windows:
        assert window.sizing.recommended_ratio is None
        assert window.sizing.reason == Y_UNMEASURED


def test_the_book_reads_spy_through_the_one_tiingo_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``research/dividends.py`` is the one reader of ``tiingo_eod``: the
    Book's SPY read goes through its memo, so two Book builds and a pricing
    lookup on the same snapshot read bronze once, not once each."""
    dividends._memo.clear()
    store = DeltaLakeStore(tmp_path)
    index = pd.bdate_range("2010-01-04", "2012-12-31")
    seed_spy(store, index[-1].date(), index)
    reads: list[str] = []
    real = store.read_bronze_columns_as_of

    def counting(dataset: str, as_of: dt.date, columns: list[str]) -> pd.DataFrame:
        reads.append(dataset)
        return real(dataset, as_of, columns)

    monkeypatch.setattr(store, "read_bronze_columns_as_of", counting)
    a = build_index_leg(store, index[-1].date(), spx=_spx(index))
    b = build_index_leg(store, index[-1].date(), spx=_spx(index))
    dividend_lookup(store, "spy", index[-1].date())
    assert reads == ["tiingo_eod"]
    assert a.basis.source == b.basis.source == "measured"
    pd.testing.assert_series_equal(a.headline, b.headline)
