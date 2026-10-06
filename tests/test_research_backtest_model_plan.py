"""Tests for the Book's model-priced plan (docs/adr/0027 §3).

Legs are built by hand where the arithmetic matters, with the model price and
the price paid deliberately different and the index moving between entry,
expiry and the end, so a wrong unit count, payoff timing or cash split moves a
number with a closed form. The pricer is pinned against an independent
Black-Scholes with a VIX that changes after entry, so a look-ahead read fails.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.hedge_overlay import OverlayDataMissing
from tail_lab.research.backtest.index_replication import DAYS_PER_YEAR
from tail_lab.research.backtest.model_plan import (
    ACCOUNTING,
    CALIBRATION,
    MEASURED_VOL_GAP,
    PutLeg,
    accuracy_for,
    compute_model_plan,
    model_rolling,
    price_legs,
    run_model_plan,
    run_model_window,
)


def _bs_put(s: float, k: float, t: float, r: float, sig: float, q: float) -> float:
    def n(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    d1 = (math.log(s / k) + (r - q + sig**2 / 2) * t) / (sig * math.sqrt(t))
    d2 = d1 - sig * math.sqrt(t)
    return k * math.exp(-r * t) * n(-d2) - s * math.exp(-q * t) * n(-d1)


def _day(i: int) -> dt.date:
    return dt.date(2000 + i // 12, i % 12 + 1, 15)


def _leg(i: int, *, premium: float, payoff: float = 0.0, model: float | None = None) -> PutLeg:
    return PutLeg(
        entry=_day(i),
        expiry=_day(i + 1),
        model_premium=premium / 2 if model is None else model,
        premium=premium,
        payoff=payoff,
    )


def _legs(n: int, *, premium: float, payoffs: list[float] | None = None) -> list[PutLeg]:
    pays = payoffs or [0.0] * n
    return [_leg(i, premium=premium, payoff=pays[i]) for i in range(n)]


def _index(legs: list[PutLeg], levels: list[float]) -> pd.Series:
    dates = [leg.entry for leg in legs] + [legs[-1].expiry]
    return pd.Series(levels, index=pd.DatetimeIndex(dates))


# ------------------------------------------------------------------ the error bar


def test_only_the_two_measured_depths_are_offered_with_their_gap() -> None:
    assert MEASURED_VOL_GAP == {5.0: 0.022, 10.0: 0.072}
    assert accuracy_for(10.0).vol_gap == 0.072
    with pytest.raises(ValueError, match="offers only"):
        accuracy_for(20.0)


def test_the_statement_carries_the_measured_check_and_its_direction() -> None:
    a = accuracy_for(10.0)
    assert (a.calm_ratio, a.stress_ratio) == (CALIBRATION[10.0].calm, CALIBRATION[10.0].stress)
    for phrase in (
        "Black-Scholes at the VIX plus the measured skew gap",
        "+7.2 vol points",
        "flat 4% rate",
        "214 real monthly SPY put mids",
        "median 0.80x the market in calm months",
        "most so in the zero-rate years",
        "less than a buyer pays at the ask",
        "flatters the puts",
        "sampled at monthly rolls",
        "An estimate, not a quote.",
    ):
        assert phrase in a.statement, phrase


def test_the_premium_paid_is_black_scholes_at_the_vix_plus_the_gap_read_at_entry() -> None:
    """A VIX that jumps after entry must not reach the price (no look-ahead),
    and a strike off the 5-point grid snaps to it."""
    days = pd.bdate_range("2019-01-02", "2019-03-29")
    spx = pd.Series(3003.0, index=days)
    vix = pd.Series(20.0, index=days)
    vix.loc["2019-01-19":] = 60.0  # after the 2019-01-18 entry
    legs = price_legs(spx, vix, moneyness_pct=5.0, dividend_yield=0.02, rate=0.04)
    first = legs[0]
    assert first.entry == dt.date(2019, 1, 18)
    t = (first.expiry - first.entry).days / DAYS_PER_YEAR
    k = 2855.0  # 3003 x 0.95 = 2852.85, nearest listed strike
    assert first.model_premium == pytest.approx(_bs_put(3003.0, k, t, 0.04, 0.20, 0.02), rel=1e-9)
    assert first.premium == pytest.approx(_bs_put(3003.0, k, t, 0.04, 0.222, 0.02), rel=1e-9)
    assert [leg.entry for leg in legs[1:]] == [leg.expiry for leg in legs[:-1]]


def test_the_payoff_is_struck_at_entry_and_settled_at_expiry() -> None:
    days = pd.bdate_range("2019-01-02", "2019-02-28")
    spx = pd.Series(3000.0, index=days)
    spx.loc["2019-02-15"] = 2500.0  # the expiry close
    vix = pd.Series(20.0, index=days)
    first = price_legs(spx, vix, moneyness_pct=10.0, dividend_yield=0.0)[0]
    assert first.expiry == dt.date(2019, 2, 15)
    assert first.payoff == pytest.approx(2700.0 - 2500.0)


# ------------------------------------------------------------------ one window


def test_units_are_bought_at_the_price_paid_not_the_model_price() -> None:
    legs = [_leg(0, premium=5.0, model=2.0, payoff=4.0)]
    w = run_model_window(
        legs, _index(legs, [100.0, 100.0]), first=0, count=1, e0=100.0, monthly=50.0
    )
    # 50 / 5 = 10 units x 4 = 40 (at 50/2 = 25 units it would be 100).
    assert w.hedged.terminal_wealth == pytest.approx(100.0 + 40.0)


def test_a_payoff_buys_the_index_at_expiry_and_rides_from_there() -> None:
    legs = _legs(3, premium=5.0, payoffs=[0.0, 4.0, 0.0])
    spx = _index(legs, [100.0, 80.0, 50.0, 75.0])
    w = run_model_window(legs, spx, first=0, count=3, e0=100.0, monthly=50.0, put_share=0.6)
    # E0: 1 unit at 100. The index part of X (20) buys at 100, 80, 50.
    # Puts: 30/5 = 6 units; the second pays 6 x 4 = 24 at its expiry (level 50).
    units = 1 + 20 / 100 + 20 / 80 + 20 / 50 + 24 / 50
    assert w.hedged.terminal_wealth == pytest.approx(units * 75.0)
    c_units = 150 / 100 + 50 / 80 + 50 / 50
    assert w.comparator.terminal_wealth == pytest.approx(c_units * 75.0)


def test_a_paying_put_cushions_the_drawdown_the_index_alone_takes() -> None:
    legs = [_leg(0, premium=1.0, payoff=2.0)]
    spx = _index(legs, [100.0, 50.0])
    w = run_model_window(legs, spx, first=0, count=1, e0=100.0, monthly=10.0)
    assert w.comparator.max_drawdown == pytest.approx(-0.5)
    # Starts at 100 (index) + 10 (put); ends at 50 + 10 units x 2 = 70.
    assert w.hedged.max_drawdown == pytest.approx(70 / 110 - 1)


def test_the_comparator_drawdown_is_time_weighted_not_wealth() -> None:
    legs = _legs(2, premium=1.0)
    spx = _index(legs, [100.0, 50.0, 100.0])
    w = run_model_window(legs, spx, first=0, count=2, e0=100.0, monthly=100.0)
    # New money at the bottom keeps wealth up, but the investment halved.
    assert w.comparator.max_drawdown == pytest.approx(-0.5)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"first": 0, "count": 0}, "outside"),
        ({"first": 2, "count": 5}, "outside"),
        ({"e0": 0.0, "monthly": 0.0}, "plan needs"),
        ({"put_share": 0.0}, "put_share"),
        ({"put_share": 1.5}, "put_share"),
    ],
)
def test_bad_windows_and_plans_are_caller_bugs(kwargs: dict[str, float], match: str) -> None:
    legs = _legs(4, premium=1.0)
    spx = _index(legs, [100.0] * 5)
    args: dict[str, float] = {"first": 0, "count": 4, "e0": 1.0, "monthly": 1.0} | kwargs
    with pytest.raises(ValueError, match=match):
        run_model_window(
            legs,
            spx,
            first=int(args["first"]),
            count=int(args["count"]),
            e0=args["e0"],
            monthly=args["monthly"],
            put_share=args.get("put_share", 1.0),
        )


# ------------------------------------------------------------------ rolling + plan


def test_rolling_begins_at_every_leg_with_a_full_horizon_left() -> None:
    legs = _legs(30, premium=1.0)
    spx = _index(legs, [100.0] * 31)
    r = model_rolling(legs, spx, horizon_years=1, e0=100.0, monthly=10.0)
    assert r.n_starts == 30 - 12 + 1
    assert r.first_start == legs[0].entry and r.last_start == legs[18].entry
    assert r.share_behind == 1.0


def _market(
    start: str = "1995-01-03", end: str = "2015-12-31", vix: float = 18.0
) -> tuple[pd.Series, pd.Series]:
    days = pd.bdate_range(start, end)
    rng = np.random.default_rng(4)
    spx = pd.Series(1000 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days))), index=days)
    return spx, pd.Series(vix, index=days)


def test_the_illustrated_window_is_the_most_recent_full_horizon() -> None:
    spx, vix = _market()
    r = run_model_plan(
        spx,
        vix,
        as_of=dt.date(2015, 12, 31),
        moneyness_pct=5.0,
        e0=1_000.0,
        monthly=100.0,
        horizon_years=5,
    )
    legs = price_legs(spx, vix, moneyness_pct=5.0, dividend_yield=r.dividend_yield)
    assert r.window is not None
    assert (r.window.start, r.window.end) == (legs[-60].entry, legs[-1].expiry)


def test_exactly_one_horizon_of_history_is_enough() -> None:
    spx, vix = _market()
    legs = price_legs(spx, vix, moneyness_pct=5.0, dividend_yield=0.019)
    cut = pd.Timestamp(legs[59].expiry)
    r = run_model_plan(
        spx[:cut],
        vix[:cut],
        as_of=cut.date(),
        moneyness_pct=5.0,
        e0=1.0,
        monthly=1.0,
        horizon_years=5,
    )
    assert r.refusal is None and r.rolling is not None and r.rolling.n_starts == 1


def test_the_comparator_earns_the_assumed_dividend_on_a_flat_index() -> None:
    days = pd.bdate_range("2000-01-03", "2012-12-31")
    spx = pd.Series(1000.0, index=days)
    r = run_model_plan(
        spx,
        pd.Series(18.0, index=days),
        as_of=dt.date(2012, 12, 31),
        moneyness_pct=5.0,
        e0=1_000.0,
        monthly=100.0,
        horizon_years=10,
        dividend_yield=0.03,
    )
    assert r.window is not None
    assert r.window.comparator.irr == pytest.approx(0.03, abs=2e-4)


def test_the_plan_reports_its_accounting_error_bar_and_verdict() -> None:
    spx, vix = _market()
    r = run_model_plan(
        spx,
        vix,
        as_of=dt.date(2015, 12, 31),
        moneyness_pct=10.0,
        e0=1_000.0,
        monthly=100.0,
        horizon_years=5,
        put_share=0.2,
    )
    assert r.accounting == ACCOUNTING and "contribution-funded" in r.accounting
    assert r.accuracy.vol_gap == 0.072
    assert r.refusal is None and r.window is not None and r.rolling is not None
    assert r.put_share == 0.2


def test_a_history_shorter_than_the_horizon_is_a_refusal() -> None:
    spx, vix = _market("2014-01-02", "2015-12-31")
    r = run_model_plan(
        spx,
        vix,
        as_of=dt.date(2015, 12, 31),
        moneyness_pct=5.0,
        e0=1.0,
        monthly=1.0,
        horizon_years=10,
    )
    assert r.window is None and r.refusal == "history is shorter than one 10-year plan"


@pytest.mark.parametrize(
    ("e0", "monthly", "match"),
    [(1.0, 0.0, "X must be positive"), (0.0, 100.0, "needs a starting book")],
)
def test_the_model_plan_needs_a_book_and_money_for_puts(
    e0: float, monthly: float, match: str
) -> None:
    """With no starting book the puts arm is a standalone put -- its
    time-weighted drawdown reads -100% for a sleeve that later wins -- and
    the Workspace already covers standalone puts."""
    spx, vix = _market()
    with pytest.raises(ValueError, match=match):
        run_model_plan(
            spx,
            vix,
            as_of=dt.date(2015, 12, 31),
            moneyness_pct=5.0,
            e0=e0,
            monthly=monthly,
            horizon_years=1,
        )


def test_the_plan_never_reads_past_as_of() -> None:
    spx, vix = _market(end="2020-12-31")
    as_of = dt.date(2015, 12, 31)
    r = run_model_plan(
        spx, vix, as_of=as_of, moneyness_pct=5.0, e0=1.0, monthly=1.0, horizon_years=5
    )
    assert r.window is not None and r.rolling is not None
    assert r.window.end <= as_of and r.rolling.last_start <= as_of


# ------------------------------------------------------------------ lake


def _seed(store: DeltaLakeStore, ingest: dt.date, *, vix: bool = True) -> None:
    spx, v = _market(end=ingest.isoformat())
    store.write_bronze(
        "cboe_strategy",
        ingest,
        pd.DataFrame({"index_symbol": "SPX", "trade_date": spx.index, "close": spx.to_numpy()}),
    )
    if vix:
        store.write_bronze("vix", ingest, pd.DataFrame({"date": v.index, "close": v.to_numpy()}))


def test_compute_reads_spx_and_vix_point_in_time(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2015, 12, 31)
    _seed(store, ingest)
    r = compute_model_plan(
        store, as_of=ingest, moneyness_pct=5.0, e0=1.0, monthly=1.0, horizon_years=5
    )
    assert r.window is not None
    with pytest.raises(OverlayDataMissing, match="no cboe_strategy known"):
        compute_model_plan(
            store,
            as_of=ingest - dt.timedelta(days=1),
            moneyness_pct=5.0,
            e0=1.0,
            monthly=1.0,
            horizon_years=5,
        )


def test_compute_needs_the_vix(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2015, 12, 31)
    _seed(store, ingest, vix=False)
    with pytest.raises(OverlayDataMissing, match="no vix known"):
        compute_model_plan(
            store, as_of=ingest, moneyness_pct=5.0, e0=1.0, monthly=1.0, horizon_years=5
        )


def test_compute_needs_spx(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2015, 12, 31)
    days = pd.bdate_range("2010-01-04", ingest)
    store.write_bronze(
        "cboe_strategy",
        ingest,
        pd.DataFrame({"index_symbol": "PPUT", "trade_date": days, "close": 1.0}),
    )
    store.write_bronze("vix", ingest, pd.DataFrame({"date": days, "close": 18.0}))
    with pytest.raises(OverlayDataMissing, match="no SPX rows"):
        compute_model_plan(
            store, as_of=ingest, moneyness_pct=5.0, e0=1.0, monthly=1.0, horizon_years=1
        )


def test_a_store_bug_is_not_reported_as_missing_data() -> None:
    class BrokenStore:
        def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
            raise KeyError("typo")

    with pytest.raises(KeyError, match="typo"):
        compute_model_plan(
            BrokenStore(),  # type: ignore[arg-type]
            as_of=dt.date(2015, 12, 31),
            moneyness_pct=5.0,
            e0=1.0,
            monthly=1.0,
            horizon_years=1,
        )
