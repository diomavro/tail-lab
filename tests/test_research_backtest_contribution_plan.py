"""Tests for the Book's monthly-contributions plan (docs/adr/0027 §3).

The pinned cases use NAVs that grow at a constant calendar rate, where the
money-weighted return of ANY contribution schedule equals that rate exactly
-- so a wrong cash-flow timing, a wrong day count or a wrong IRR solve each
moves a number that has a closed form.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.contribution_plan import (
    BILL_SERIES,
    COMPARATOR_KEYS,
    OTHER_CBOE_COMPARATORS,
    Comparator,
    PlanRequest,
    _window_bounds,
    bill_levels,
    comparator_levels,
    compute_book_plan,
    dense_tail,
    month_starts,
    rolling_gaps,
    run_book_plan,
    run_window,
    summarize,
    xirr,
    yield_shares,
)
from tail_lab.research.backtest.hedge_overlay import MIN_MARGIN, OverlayDataMissing
from tail_lab.research.backtest.index_replication import DAYS_PER_YEAR, SENSITIVITY_YIELDS


def _grow(index: pd.DatetimeIndex, rate: float) -> pd.Series:
    """A NAV compounding at exactly ``rate`` per calendar year."""
    days = (index - index[0]).days.to_numpy(dtype=float)
    return pd.Series((1.0 + rate) ** (days / DAYS_PER_YEAR), index=index)


def _days(start: str = "2000-01-03", end: str = "2012-12-31") -> pd.DatetimeIndex:
    return pd.bdate_range(start, end)


# ------------------------------------------------------------------ xirr


def test_xirr_of_one_payment_is_its_growth_rate() -> None:
    assert xirr([100.0], [1.0], 110.0) == pytest.approx(0.10, abs=1e-12)


def test_xirr_solves_a_two_payment_schedule_exactly() -> None:
    # 100 for 2 years and 100 for 1 year at 5%: 100*1.05**2 + 100*1.05.
    terminal = 100 * 1.05**2 + 100 * 1.05
    assert xirr([100.0, 100.0], [2.0, 1.0], terminal) == pytest.approx(0.05, abs=1e-12)


def test_a_total_loss_is_exactly_minus_one_hundred_percent() -> None:
    assert xirr([100.0], [1.0], 0.0) == -1.0


def test_a_short_heavy_loss_still_solves_below_minus_99_9_percent() -> None:
    """500 paid over four months ending at 100: the true rate is far below
    -99.9%/yr, which an over-tight bracket turned into a crash."""
    flows = np.array([200.0, 100.0, 100.0, 100.0])
    t = np.array([4, 3, 2, 1]) / 12
    r = xirr(flows, t, 100.0)
    assert r < -0.999
    assert float(np.sum(flows * (1 + r) ** t)) == pytest.approx(100.0, rel=1e-9)


def test_xirr_refuses_a_terminal_above_the_cap() -> None:
    with pytest.raises(ValueError, match="no IRR"):
        xirr([1.0], [0.01], 1e9)


# ------------------------------------------------------------------ one window


def test_any_schedule_into_a_constant_rate_nav_earns_exactly_that_rate() -> None:
    """Money-weighted return is schedule-independent only when the NAV grows
    at a constant rate -- which pins timing, day count and the solve at once."""
    index = _days()
    starts = month_starts(index)
    w = run_window(
        _grow(index, 0.08),
        pd.Series(1.0, index=index),
        hedged_label="h",
        comparator_label="cash",
        start=int(starts[0]),
        end=int(starts[60]),
        e0=10_000.0,
        monthly=500.0,
    )
    assert w.hedged.irr == pytest.approx(0.08, abs=1e-9)
    assert w.comparator.irr == pytest.approx(0.0, abs=1e-9)
    assert w.hedged.contributed == w.comparator.contributed == 10_000 + 60 * 500
    assert w.comparator.terminal_wealth == pytest.approx(40_000.0)
    assert w.hedged.max_drawdown == 0.0
    assert w.outcome == "holds"
    assert w.irr_gap == pytest.approx(0.08, abs=1e-9)


def test_terminal_wealth_compounds_each_payment_from_its_own_date() -> None:
    index = _days()
    starts = month_starts(index)
    nav = _grow(index, 0.10)
    w = run_window(
        nav,
        nav,
        hedged_label="h",
        comparator_label="c",
        start=int(starts[0]),
        end=int(starts[24]),
        e0=1_000.0,
        monthly=100.0,
    )
    pay = starts[:24]
    flows = np.full(24, 100.0)
    flows[0] += 1_000.0
    expected = float((flows * nav.iloc[starts[24]] / nav.iloc[pay].to_numpy()).sum())
    assert w.hedged.terminal_wealth == pytest.approx(expected)
    # Identical arms: no gap, and a zero gap is never called a win.
    assert w.irr_gap == 0.0
    assert w.outcome == "inconclusive"


def test_drawdown_is_the_investments_not_masked_by_new_money() -> None:
    """A NAV that halves and recovers has a -50% drawdown, however much cash
    arrives during the fall."""
    index = _days("2001-01-01", "2003-12-31")
    path = np.concatenate([np.linspace(1, 0.5, 300), np.linspace(0.5, 1.2, len(index) - 300)])
    nav = pd.Series(path, index=index)
    starts = month_starts(index)
    w = run_window(
        nav,
        pd.Series(1.0, index=index),
        hedged_label="h",
        comparator_label="c",
        start=int(starts[0]),
        end=len(index) - 1,
        e0=0.0,
        monthly=1_000.0,
    )
    assert w.hedged.max_drawdown == pytest.approx(-0.5)


@pytest.mark.parametrize(("e0", "monthly"), [(0.0, 0.0), (-1.0, 100.0), (100.0, -1.0)])
def test_a_plan_needs_money_in(e0: float, monthly: float) -> None:
    index = _days()
    with pytest.raises(ValueError, match="plan needs"):
        run_window(
            _grow(index, 0.05),
            _grow(index, 0.05),
            hedged_label="h",
            comparator_label="c",
            start=0,
            end=100,
            e0=e0,
            monthly=monthly,
        )


def test_a_plan_starts_on_a_month_start() -> None:
    index = _days()
    with pytest.raises(ValueError, match="month start"):
        run_window(
            _grow(index, 0.05),
            _grow(index, 0.05),
            hedged_label="h",
            comparator_label="c",
            start=3,
            end=200,
            e0=100.0,
            monthly=10.0,
        )


# ------------------------------------------------------------------ rolling


def test_rolling_begins_on_every_month_start_with_a_full_horizon_left() -> None:
    index = _days()
    starts = month_starts(index)
    gaps, begun = rolling_gaps(
        _grow(index, 0.09), _grow(index, 0.04), horizon_years=5, e0=1_000.0, monthly=100.0
    )
    assert len(gaps) == len(starts) - 60
    assert begun[0] == index[starts[0]].date()
    assert begun[-1] == index[starts[len(starts) - 61]].date()
    assert gaps == pytest.approx([0.05] * len(gaps), abs=1e-8)
    summary = summarize(gaps, begun, 5)
    assert summary.share_ahead == 1.0
    assert summary.share_ahead + summary.share_behind + summary.share_inconclusive == 1.0
    assert summary.median_gap == pytest.approx(0.05, abs=1e-8)


def test_rolling_shares_use_the_one_basis_point_band_both_ways() -> None:
    gaps = [2 * MIN_MARGIN, MIN_MARGIN, 0.0, -MIN_MARGIN, -2 * MIN_MARGIN]
    begun = [dt.date(2000, m, 1) for m in range(1, 6)]
    s = summarize(gaps, begun, 1)
    assert (s.share_ahead, s.share_behind, s.share_inconclusive) == (0.2, 0.2, 0.6)
    assert (s.worst_gap, s.best_gap) == (-2 * MIN_MARGIN, 2 * MIN_MARGIN)


def test_history_shorter_than_one_plan_is_a_refusal() -> None:
    with pytest.raises(OverlayDataMissing, match="shorter than one 10-year plan"):
        summarize([], [], 10)


# ------------------------------------------------------------------ T-bills


def _rates(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_id": BILL_SERIES,
            "obs_date": pd.to_datetime([r[0] for r in rows]),
            "vintage_date": pd.to_datetime([r[1] for r in rows]),
            "value": [r[2] for r in rows],
        }
    )


def test_bills_compound_the_known_rate_by_calendar_day() -> None:
    dates = pd.DatetimeIndex(["2020-01-06", "2020-01-07", "2020-01-10"])
    rates = _rates(
        [
            ("2020-01-03", "2020-01-06", 5.0),
            ("2020-01-06", "2020-01-07", 5.0),
            ("2020-01-07", "2020-01-08", 5.0),
        ]
    )
    levels = bill_levels(rates, dates)
    assert levels.to_list() == pytest.approx(
        [1.0, 1.05 ** (1 / DAYS_PER_YEAR), 1.05 ** (4 / DAYS_PER_YEAR)]
    )


def test_bills_read_the_first_vintage_and_never_a_later_revision() -> None:
    """A revision published years later must not leak into a past accrual."""
    dates = pd.DatetimeIndex(["2020-01-06", "2020-01-07"])
    rates = _rates([("2020-01-03", "2020-01-06", 1.0), ("2020-01-03", "2024-05-01", 9.0)])
    levels = bill_levels(rates, dates)
    assert levels.iloc[-1] == pytest.approx(1.01 ** (1 / DAYS_PER_YEAR))


def test_bills_without_the_series_are_refused() -> None:
    with pytest.raises(OverlayDataMissing, match="no DGS3MO"):
        bill_levels(_rates([]).iloc[0:0], pd.DatetimeIndex(["2020-01-06"]))


# ------------------------------------------------------------------ comparators


def test_the_offered_cboe_comparators_exclude_the_programs_ltv_and_spx() -> None:
    """LTV is a quoted level and SPX is price-only; neither is a total return.
    The programs themselves are what is being tested."""
    assert not {"LTV", "SPX", "PPUT", "PPUT3M", "VXTH"} & set(OTHER_CBOE_COMPARATORS)
    assert COMPARATOR_KEYS[:3] == ("spx", "cash", "bills")


def test_a_cboe_comparator_that_starts_late_runs_on_the_shared_history() -> None:
    """Most Cboe indices start years after PPUT. Refusing them would grey out
    the whole picker; the plan runs on the overlap and says where it starts."""
    spx, cboe = _market()
    cboe["PUT"] = cboe["PUT"].loc["1995-01-02":]
    r = run_book_plan(
        spx,
        cboe,
        None,
        PlanRequest("PPUT", 0.5, 1_000.0, 100.0, "PUT", 5),
        as_of=dt.date(2015, 12, 31),
    )
    assert r.rolling is not None
    assert r.rolling.first_start == dt.date(1995, 1, 2)


def test_a_cboe_comparator_with_no_shared_history_is_refused() -> None:
    dates = _days("2000-01-03", "2001-12-31")
    later = _grow(_days("2005-01-03", "2005-12-30"), 0.03)
    with pytest.raises(OverlayDataMissing, match="shares no history"):
        comparator_levels(
            "PUT",
            spx=_grow(dates, 0.0),
            dates=dates,
            dividend_yield=0.0,
            rates=None,
            cboe={"PUT": later},
        )


def test_an_unknown_comparator_is_a_caller_bug() -> None:
    dates = _days("2000-01-03", "2000-03-31")
    with pytest.raises(ValueError, match="unknown comparator"):
        comparator_levels(
            "QQQ", spx=_grow(dates, 0.0), dates=dates, dividend_yield=0.0, rates=None, cboe={}
        )


# ------------------------------------------------------------------ the whole plan


def _market(
    start: str = "1990-01-02", end: str = "2015-12-31"
) -> tuple[pd.Series, dict[str, pd.Series]]:
    index = _days(start, end)
    spx = _grow(index, 0.07) * 100
    return spx, {"PPUT": _grow(index, 0.05) * 50, "PUT": _grow(index, 0.06) * 80}


def test_the_plan_reports_every_comparator_and_refuses_bills_without_rates() -> None:
    spx, cboe = _market()
    r = run_book_plan(
        spx,
        cboe,
        None,
        PlanRequest("PPUT", 0.5, 10_000.0, 500.0, "bills", 10),
        as_of=dt.date(2015, 12, 31),
    )
    assert [o.key for o in r.comparators] == list(COMPARATOR_KEYS)
    by_key = {o.key: o for o in r.comparators}
    assert by_key["spx"].available and by_key["cash"].available and by_key["PUT"].available
    assert not by_key["bills"].available and "rates not ingested" in (by_key["bills"].reason or "")
    assert r.window is None and r.rolling is None
    assert r.refusal == "T-bills: no T-bill history in the lake yet (rates not ingested)"


def test_the_plan_runs_window_rolling_and_yield_sensitivity() -> None:
    spx, cboe = _market()
    r = run_book_plan(
        spx,
        cboe,
        None,
        PlanRequest("PPUT", 1.0, 10_000.0, 500.0, "PUT", 10),
        as_of=dt.date(2015, 12, 31),
    )
    assert r.refusal is None and r.window is not None and r.rolling is not None
    # PPUT at 5% vs PUT at 6% a year, both constant: every start trails by 1pp.
    assert r.window.irr_gap == pytest.approx(-0.01, abs=1e-8)
    assert r.rolling.share_behind == 1.0
    # Default window: the most recent full ten years.
    assert (r.window.end - r.window.start).days / DAYS_PER_YEAR == pytest.approx(10, abs=0.02)
    assert [y.dividend_yield for y in r.by_yield] == list(SENSITIVITY_YIELDS)


def test_the_assumed_yield_moves_the_spx_comparator_in_the_sensitivity() -> None:
    """Against the S&P, both arms hold the yield assumption: a richer yield
    lifts the unhedged comparator more than a half-hedged book, so the share
    of starts the hedged arm wins can only fall."""
    spx, cboe = _market()
    r = run_book_plan(
        spx,
        cboe,
        None,
        PlanRequest("PPUT", 0.5, 10_000.0, 500.0, "spx", 10),
        as_of=dt.date(2015, 12, 31),
    )
    gaps = [y.median_gap for y in r.by_yield]
    assert gaps == sorted(gaps, reverse=True)
    assert len(set(gaps)) == 3


def test_the_plan_never_reads_past_as_of() -> None:
    spx, cboe = _market(end="2020-12-31")
    as_of = dt.date(2015, 12, 31)
    r = run_book_plan(
        spx, cboe, None, PlanRequest("PPUT", 0.5, 1_000.0, 100.0, "cash", 5), as_of=as_of
    )
    assert r.window is not None and r.rolling is not None
    assert r.window.end <= as_of and r.rolling.last_start <= as_of


@pytest.mark.parametrize(
    ("request_", "match"),
    [
        (PlanRequest("LTV", 0.5, 1.0, 1.0, "cash", 5), "not a hedged"),
        (PlanRequest("PPUT", 1.5, 1.0, 1.0, "cash", 5), "hedge_ratio"),
        (PlanRequest("PPUT", 0.5, 1.0, 1.0, "QQQ", 5), "unknown comparator"),
    ],
)
def test_bad_requests_are_caller_bugs(request_: PlanRequest, match: str) -> None:
    spx, cboe = _market()
    with pytest.raises(ValueError, match=match):
        run_book_plan(spx, cboe, None, request_, as_of=dt.date(2015, 12, 31))


def test_a_start_date_snaps_to_the_next_month_start() -> None:
    index = _days("2000-01-03", "2010-12-31")
    lo, hi = _window_bounds(index, horizon_years=2, start=dt.date(2003, 5, 17))
    assert index[lo].date() == dt.date(2003, 6, 2)
    assert index[hi].date() == dt.date(2005, 6, 1)


def test_a_window_past_the_end_of_history_is_cut_there() -> None:
    index = _days("2000-01-03", "2001-06-29")
    lo, hi = _window_bounds(index, horizon_years=5, start=dt.date(2000, 1, 3))
    assert (lo, hi) == (0, len(index) - 1)


def test_a_history_shorter_than_the_horizon_is_a_refusal_not_a_crash() -> None:
    spx, cboe = _market(start="2010-01-04", end="2012-12-31")
    r = run_book_plan(
        spx,
        cboe,
        None,
        PlanRequest("PPUT", 0.5, 1_000.0, 100.0, "cash", 10),
        as_of=dt.date(2012, 12, 31),
    )
    assert r.window is None
    assert r.refusal == "history is shorter than one 10-year plan"


# ------------------------------------------------------------------ lake


def _seed(store: DeltaLakeStore, ingest: dt.date, *, with_spx: bool = True) -> None:
    spx, cboe = _market(end=ingest.isoformat())
    series = ({"SPX": spx} if with_spx else {}) | cboe
    frames = [
        pd.DataFrame({"index_symbol": k, "trade_date": v.index, "close": v.to_numpy()})
        for k, v in series.items()
    ]
    store.write_bronze("cboe_strategy", ingest, pd.concat(frames, ignore_index=True))


def test_compute_reads_cboe_point_in_time_and_works_without_rates(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2015, 12, 31)
    _seed(store, ingest)
    r = compute_book_plan(store, PlanRequest("PPUT", 0.5, 1_000.0, 100.0, "spx", 5), as_of=ingest)
    assert r.window is not None
    assert {o.key: o.available for o in r.comparators}["bills"] is False
    with pytest.raises(OverlayDataMissing, match="no Cboe strategy indices"):
        compute_book_plan(
            store, PlanRequest("PPUT", 0.5, 1.0, 1.0, "spx", 5), as_of=ingest - dt.timedelta(days=1)
        )


def test_compute_offers_bills_once_rates_are_in_the_lake(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2015, 12, 31)
    _seed(store, ingest)
    obs = _days("1989-12-01", "2015-12-30")
    store.write_bronze(
        "rates",
        ingest,
        pd.DataFrame(
            {
                "series_id": BILL_SERIES,
                "obs_date": obs,
                "value": 3.0,
                "vintage_date": obs + pd.Timedelta(days=1),
            }
        ),
    )
    r = compute_book_plan(store, PlanRequest("PPUT", 0.5, 1_000.0, 100.0, "bills", 5), as_of=ingest)
    assert {o.key: o.available for o in r.comparators}["bills"] is True
    assert r.window is not None
    assert r.window.comparator.irr == pytest.approx(0.03, abs=2e-4)


def test_compute_needs_spx(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2015, 12, 31)
    _seed(store, ingest, with_spx=False)
    with pytest.raises(OverlayDataMissing, match="no SPX rows"):
        compute_book_plan(store, PlanRequest("PPUT", 0.5, 1.0, 1.0, "spx", 5), as_of=ingest)


def test_a_store_bug_is_not_reported_as_missing_data() -> None:
    class BrokenStore:
        def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
            raise KeyError("typo in the store")

    with pytest.raises(KeyError, match="typo"):
        compute_book_plan(
            BrokenStore(),  # type: ignore[arg-type]
            PlanRequest("PPUT", 0.5, 1.0, 1.0, "spx", 5),
            as_of=dt.date(2015, 12, 31),
        )


def test_misaligned_arms_are_a_caller_bug() -> None:
    a = _grow(_days("2000-01-03", "2000-06-30"), 0.05)
    with pytest.raises(ValueError, match="share one date index"):
        run_window(
            a,
            a.iloc[:-1],
            hedged_label="h",
            comparator_label="c",
            start=0,
            end=50,
            e0=1.0,
            monthly=1.0,
        )


def test_a_start_after_the_last_month_start_is_a_refusal() -> None:
    index = _days("2000-01-03", "2000-06-30")
    with pytest.raises(OverlayDataMissing, match="no month start on or after 2000-06-05"):
        _window_bounds(index, horizon_years=1, start=dt.date(2000, 6, 5))


def test_a_program_absent_from_the_snapshot_is_a_refusal() -> None:
    spx, cboe = _market()
    del cboe["PPUT"]
    with pytest.raises(OverlayDataMissing, match="PPUT is not in the Cboe snapshot"):
        run_book_plan(
            spx,
            cboe,
            None,
            PlanRequest("PPUT", 0.5, 1.0, 1.0, "cash", 5),
            as_of=dt.date(2015, 12, 31),
        )


def test_a_rates_store_bug_is_not_reported_as_missing_rates(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2015, 12, 31)
    _seed(store, ingest)

    class RatesBug:
        def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
            if dataset == "rates":
                raise KeyError("column vanished")
            return store.read_bronze_as_of(dataset, as_of)

    with pytest.raises(KeyError, match="column vanished"):
        compute_book_plan(
            RatesBug(),  # type: ignore[arg-type]
            PlanRequest("PPUT", 0.5, 1.0, 1.0, "spx", 5),
            as_of=ingest,
        )


# ------------------------------------------------------------------ round-1 review


def test_bills_take_the_latest_observation_not_the_latest_publication() -> None:
    """FRED backfills old observations in bulk. A 2019-12-31 print first
    published on 2020-01-07 must not displace the 2020-01-03 print already
    in force -- the bug that applied 8-10% rates in 2005 and 2020."""
    dates = pd.DatetimeIndex(["2020-01-06", "2020-01-08", "2020-01-09"])
    rates = _rates(
        [
            ("2020-01-03", "2020-01-06", 1.6),
            ("2019-12-31", "2020-01-07", 9.9),
            ("2020-01-08", "2020-01-09", 1.6),
        ]
    )
    levels = bill_levels(rates, dates)
    # Into 01-08 at the rate in force on 01-06 (the 01-03 print, 1.6%), and
    # into 01-09 at the rate in force on 01-08 (still the 01-03 print).
    assert levels.iloc[1] == pytest.approx(1.016 ** (2 / DAYS_PER_YEAR))
    assert levels.iloc[2] == pytest.approx(1.016 ** (3 / DAYS_PER_YEAR))


def test_bills_accrue_at_the_rate_known_the_day_before_not_on_the_day() -> None:
    dates = pd.DatetimeIndex(["2020-01-06", "2020-01-07"])
    rates = _rates([("2020-01-03", "2020-01-06", 1.0), ("2020-01-06", "2020-01-07", 9.0)])
    levels = bill_levels(rates, dates)
    assert levels.iloc[-1] == pytest.approx(1.01 ** (1 / DAYS_PER_YEAR))


def test_an_observation_dated_today_is_not_in_force_today() -> None:
    dates = pd.DatetimeIndex(["2020-01-06", "2020-01-07", "2020-01-08"])
    rates = _rates([("2020-01-03", "2020-01-04", 2.0), ("2020-01-07", "2020-01-07", 9.0)])
    levels = bill_levels(rates, dates)
    # 01-07's own print is published on 01-07 but applies from 01-08 on.
    assert levels.iloc[1] == pytest.approx(1.02 ** (1 / DAYS_PER_YEAR))
    assert levels.iloc[2] == pytest.approx(levels.iloc[1] * 1.02 ** (1 / DAYS_PER_YEAR))


def test_bills_narrow_to_the_first_day_a_rate_is_known_instead_of_refusing() -> None:
    """ALFRED's vintage history starts in 2005; a plan over 1986+ must run on
    the known span, as a late-starting Cboe index does, not go dark."""
    dates = pd.DatetimeIndex(["2020-01-06", "2020-01-07", "2020-01-08", "2020-01-09"])
    rates = _rates([("2020-01-03", "2020-01-08", 3.0)])
    levels = bill_levels(rates, dates)
    assert levels.index[0] == pd.Timestamp("2020-01-08")
    assert levels.iloc[0] == 1.0
    with pytest.raises(OverlayDataMissing, match="not known point-in-time"):
        bill_levels(_rates([("2020-01-03", "2021-01-01", 3.0)]), dates)


def test_a_sparse_comparator_history_is_cut_to_its_dense_tail() -> None:
    """Cboe's PUT file has 7 points in 1991-2004; each would pass as a month
    start and stretch a 10-year plan to 25 years."""
    sparse = pd.DatetimeIndex(["1991-03-04", "1993-06-01", "1999-01-04"])
    dense = pd.bdate_range("2007-01-03", "2007-03-30")
    levels = pd.Series(1.0, index=sparse.append(dense))
    assert dense_tail(levels).index[0] == pd.Timestamp("2007-01-03")
    # A long weekend is not a hole.
    holiday = pd.DatetimeIndex(["2001-09-10", "2001-09-17", "2001-09-18"])
    assert len(dense_tail(pd.Series(1.0, index=holiday))) == 3


@pytest.mark.parametrize(
    ("before", "after", "kept"),
    [
        ("2015-01-30", "2015-03-02", False),  # 31 days, and all of February is gone
        ("2015-01-09", "2015-02-10", True),  # 32 days, yet no month is skipped
    ],
)
def test_a_hole_is_a_skipped_calendar_month_not_a_day_count(
    before: str, after: str, kept: bool
) -> None:
    head = pd.bdate_range("2014-06-02", before)
    tail = pd.bdate_range(after, periods=40)
    levels = pd.Series(1.0, index=head.append(tail))
    assert (dense_tail(levels).index[0] == head[0]) is kept


def test_a_programs_own_mid_month_first_day_is_not_a_month_start() -> None:
    """PPUT opens on 1986-06-30: paying E0 + X that day and X again on
    07-01 would be two payments a day apart. The plan starts at the first
    month start it actually observed."""
    spx, cboe = _market(start="1990-01-15")
    r = run_book_plan(
        spx,
        cboe,
        None,
        PlanRequest("PPUT", 0.5, 1_000.0, 100.0, "cash", 5),
        as_of=dt.date(2015, 12, 31),
    )
    assert r.rolling is not None and r.rolling.first_start == dt.date(1990, 2, 1)


def test_dense_tail_reads_an_unsorted_series_in_date_order() -> None:
    index = pd.DatetimeIndex(["2007-03-01", "2001-01-02", "2007-03-02"])
    assert dense_tail(pd.Series(1.0, index=index)).index[0] == pd.Timestamp("2007-03-01")


def test_a_comparator_that_starts_mid_month_starts_the_plan_at_the_next_month() -> None:
    """T-bills start 2005-06-28; a plan must not treat that day as a month
    start and pay twice three days apart."""
    spx, cboe = _market()
    cboe["PUT"] = cboe["PUT"].loc["2003-06-17":]
    r = run_book_plan(
        spx,
        cboe,
        None,
        PlanRequest("PPUT", 0.5, 1_000.0, 100.0, "PUT", 5),
        as_of=dt.date(2015, 12, 31),
    )
    assert r.rolling is not None and r.rolling.first_start == dt.date(2003, 7, 1)


def test_the_irr_ceiling_admits_a_one_month_window_that_gains_half() -> None:
    """+50% in a month is ~+13,000%/yr; the old +1000% ceiling could not
    bracket it and the request crashed."""
    r = xirr([100.0], [1 / 12], 150.0)
    assert r == pytest.approx(1.5**12 - 1, rel=1e-9)


def test_a_rate_known_on_only_one_day_is_not_enough() -> None:
    dates = pd.DatetimeIndex(["2020-01-06", "2020-01-07", "2020-01-08"])
    one_day = _rates([("2020-01-06", "2020-01-08", 3.0)])
    with pytest.raises(OverlayDataMissing, match="not known point-in-time"):
        bill_levels(one_day, dates)
    two_days = _rates([("2020-01-03", "2020-01-07", 3.0)])
    assert len(bill_levels(two_days, dates)) == 2


def test_the_last_month_start_has_no_full_month_after_it() -> None:
    index = _days("2000-01-03", "2000-03-15")
    starts = month_starts(index)
    _window_bounds(index, horizon_years=1, start=index[starts[-2]].date())  # Feb: fine
    with pytest.raises(OverlayDataMissing, match="less than one full month"):
        _window_bounds(index, horizon_years=1, start=index[starts[-1]].date())


def test_every_rolling_plan_spans_its_stated_horizon_on_a_sparse_comparator() -> None:
    spx, cboe = _market(end="2015-12-31")
    sparse_head = cboe["PUT"].loc[["1991-03-04", "1995-06-01", "1999-01-04"]]
    # 2003-01-01 is this synthetic calendar's first January business day.
    cboe["PUT"] = pd.concat([sparse_head, cboe["PUT"].loc["2003-01-01":]])
    r = run_book_plan(
        spx,
        cboe,
        None,
        PlanRequest("PPUT", 0.5, 1_000.0, 100.0, "PUT", 5),
        as_of=dt.date(2015, 12, 31),
    )
    assert r.rolling is not None and r.rolling.first_start == dt.date(2003, 1, 1)


def test_a_window_shorter_than_a_full_month_is_a_refusal_not_a_crash() -> None:
    index = _days("2000-01-03", "2000-06-02")
    with pytest.raises(OverlayDataMissing, match="less than one full month"):
        _window_bounds(index, horizon_years=1, start=dt.date(2000, 6, 1))


def test_the_spx_comparator_carries_the_assumed_dividend_yield() -> None:
    """A flat S&P price plus a 3% yield is a 3% total return; a price-only
    comparator would flip every verdict against it."""
    index = _days("2000-01-03", "2009-12-31")
    c = comparator_levels(
        "spx",
        spx=pd.Series(100.0, index=index),
        dates=index,
        dividend_yield=0.03,
        rates=None,
        cboe={},
    )
    years = (index[-1] - index[0]).days / DAYS_PER_YEAR
    assert c.levels.iloc[-1] ** (1 / years) - 1 == pytest.approx(0.03, abs=1e-9)


def test_the_default_window_is_exactly_the_most_recent_full_horizon() -> None:
    index = _days("2000-01-03", "2010-12-31")
    starts = month_starts(index)
    lo, hi = _window_bounds(index, horizon_years=2, start=None)
    assert (lo, hi) == (int(starts[-25]), int(starts[-1]))


def test_window_years_and_drawdown_are_the_windows_own() -> None:
    """``years`` is the first payment's time to the end; a crash before the
    window is not this window's drawdown."""
    index = _days("2000-01-03", "2004-12-31")
    path = np.ones(len(index))
    path[:200] = np.linspace(1.0, 0.3, 200)  # a crash in 2000, all before the window
    path[200:] = 0.3
    nav = pd.Series(path, index=index)
    starts = month_starts(index)
    first = int(starts[starts > 200][0])
    w = run_window(
        nav,
        nav,
        hedged_label="h",
        comparator_label="c",
        start=first,
        end=int(starts[-1]),
        e0=1.0,
        monthly=1.0,
    )
    assert w.hedged.max_drawdown == 0.0
    assert w.years == pytest.approx((index[starts[-1]] - index[first]).days / DAYS_PER_YEAR)


def test_a_drawdown_whose_trough_is_the_last_day_counts() -> None:
    index = _days("2001-01-01", "2001-12-31")
    nav = pd.Series(np.linspace(1.0, 0.6, len(index)), index=index)
    w = run_window(
        nav,
        nav,
        hedged_label="h",
        comparator_label="c",
        start=0,
        end=len(index) - 1,
        e0=1.0,
        monthly=0.0,
    )
    assert w.hedged.max_drawdown == pytest.approx(-0.4)


def test_each_rolling_plan_is_exactly_the_horizon_long() -> None:
    index = _days()
    starts = month_starts(index)
    hedged, comp = _grow(index, 0.09), _grow(index, 0.04)
    hedged.iloc[starts[60] :] *= 1.5  # a jump exactly at the 60th month start
    gaps, _ = rolling_gaps(hedged, comp, horizon_years=5, e0=1_000.0, monthly=0.0)
    direct = run_window(
        hedged,
        comp,
        hedged_label="h",
        comparator_label="c",
        start=int(starts[0]),
        end=int(starts[60]),
        e0=1_000.0,
        monthly=0.0,
    )
    assert gaps[0] == pytest.approx(direct.irr_gap, abs=1e-12)


def test_the_summary_reports_the_median_and_the_tenth_percentile() -> None:
    gaps = [-0.10, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.50]
    s = summarize(gaps, [dt.date(2000, 1, 1)] * 10, 1)
    assert s.median_gap == pytest.approx(0.025)  # the mean would be 0.06
    assert s.p10_gap == pytest.approx(float(np.quantile(gaps, 0.1)))
    assert s.p90_gap == pytest.approx(float(np.quantile(gaps, 0.9)))


def _const_market() -> tuple[pd.Series, pd.Series, pd.DatetimeIndex]:
    index = _days("1995-01-02", "2010-12-31")
    return _grow(index, 0.05) * 100, pd.Series(100.0, index=index), index


def test_yield_sensitivity_never_swaps_a_non_spx_comparator_for_the_spx() -> None:
    program, spx, index = _const_market()
    cash = Comparator("cash", "Cash", pd.Series(1.0, index=index))
    out = yield_shares(
        program,
        spx,
        cash,
        index,
        hedge_ratio=1.0,
        horizon_years=5,
        e0=1.0,
        monthly=1.0,
        rebuild=False,
    )
    # A 5% program beats cash at every start, whatever yield the S&P assumes.
    assert [y.share_ahead for y in out] == [1.0, 1.0, 1.0]


def test_yield_sensitivity_moves_the_hedged_arms_equity_leg() -> None:
    program, spx, index = _const_market()
    cash = Comparator("cash", "Cash", pd.Series(1.0, index=index))
    out = yield_shares(
        program,
        spx,
        cash,
        index,
        hedge_ratio=0.5,
        horizon_years=5,
        e0=1.0,
        monthly=1.0,
        rebuild=False,
    )
    medians = [y.median_gap for y in out]
    assert medians == sorted(medians) and len(set(medians)) == 3


def test_yield_sensitivity_uses_the_one_basis_point_band() -> None:
    index = _days("1995-01-02", "2010-12-31")
    program = _grow(index, 0.00005)  # 0.5bp/yr over a flat book: inside the band
    spx = pd.Series(100.0, index=index)
    cash = Comparator("cash", "Cash", pd.Series(1.0, index=index))
    out = yield_shares(
        program,
        spx,
        cash,
        index,
        hedge_ratio=1.0,
        horizon_years=5,
        e0=1.0,
        monthly=1.0,
        rebuild=False,
    )
    assert [y.share_ahead for y in out] == [0.0, 0.0, 0.0]


def test_a_comparator_frozen_inside_the_last_month_is_a_refusal_not_a_crash() -> None:
    """Round-4 review: a feed frozen past a month leaves a record inside the
    program's latest month; the month-start cut then left no dates and
    blend_nav raised an IndexError (a 500)."""
    spx, cboe = _market()
    cboe["PUT"] = cboe["PUT"].iloc[-5:]
    r = run_book_plan(
        spx,
        cboe,
        None,
        PlanRequest("PPUT", 0.5, 1_000.0, 100.0, "PUT", 5),
        as_of=dt.date(2015, 12, 31),
    )
    assert r.window is None
    assert r.refusal is not None and "no full month of history shared with PPUT" in r.refusal
