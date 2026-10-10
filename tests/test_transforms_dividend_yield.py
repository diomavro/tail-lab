"""The dividend-yield estimator, pinned on synthetic histories that reproduce
each real-world case adversarial review found breaking a simpler rule.

Why these cases and not others: a 365-day window counted five SPY payments on
most ex-dates; a "special" filter dropped JPM's 2011 restoration; a frequency
read over all history locked TSM to annual; a last-N rule kept paying Boeing a
4.4% yield years after it suspended. Each test below would fail under the rule
that got it wrong.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.transforms.dividend_yield import (
    CARRY_STALE_DAYS,
    DividendYields,
    build_dividend_yields,
)


def _history(
    start: str,
    end: str,
    close: float,
    dividends: dict[str, float],
    splits: dict[str, float] | None = None,
    close_by_date: dict[str, float] | None = None,
) -> pd.DataFrame:
    days = pd.bdate_range(start, end)
    frame = pd.DataFrame(
        {
            "symbol": "x",
            "trade_date": days,
            "close": close,
            "adj_close": close,
            "div_cash": 0.0,
            "split_factor": 1.0,
        }
    )
    for day, cash in dividends.items():
        frame.loc[frame["trade_date"] == pd.Timestamp(day), "div_cash"] = cash
    for day, factor in (splits or {}).items():
        frame.loc[frame["trade_date"] == pd.Timestamp(day), "split_factor"] = factor
    for day, price in (close_by_date or {}).items():
        frame.loc[frame["trade_date"] >= pd.Timestamp(day), "close"] = price
    assert frame["div_cash"].sum() == pytest.approx(sum(dividends.values())), (
        "ex-date not a weekday"
    )
    return frame


def _third_fridays(years: range, months: tuple[int, ...]) -> list[str]:
    out = []
    for year in years:
        for month in months:
            first = dt.date(year, month, 1)
            friday = first + dt.timedelta(days=(4 - first.weekday()) % 7 + 14)
            out.append(friday.isoformat())
    return out


def _q(annual: float, close: float) -> float:
    return -math.log1p(-annual / close)


def test_hand_case_quarterly_payer() -> None:
    ex = _third_fridays(range(2021, 2024), (3, 6, 9, 12))
    yields = DividendYields(
        "x", _history("2021-01-04", "2023-12-29", 400.0, dict.fromkeys(ex, 1.5))
    )
    got = yields.at(dt.date(2023, 11, 1))
    assert got.source == "measured"
    assert len(got.payments) == 4
    assert got.q == pytest.approx(_q(6.0, 400.0))


def test_quarterly_third_fridays_never_count_five_payments() -> None:
    # SPY's ex-dates are third Fridays, often 364 days apart: a 365-day window
    # holds five of them on the ex-date itself. N is a count, so q does not jump.
    ex = _third_fridays(range(2020, 2025), (3, 6, 9, 12))
    yields = DividendYields(
        "x", _history("2020-01-02", "2024-12-31", 400.0, dict.fromkeys(ex, 1.5))
    )
    for day in ex[8:]:
        d = dt.date.fromisoformat(day)
        for probe in (d - dt.timedelta(days=1), d, d + dt.timedelta(days=3)):
            got = yields.at(probe)
            assert len(got.payments) == 4, probe
            assert got.q == pytest.approx(_q(6.0, 400.0)), probe


def test_monthly_payer_with_a_double_december_stays_at_twelve() -> None:
    # iShares pays twice in December and not in January; unsnapped, the
    # median gap drifts and N swings between 11 and 13.
    ex = []
    for year in range(2020, 2025):
        for month in range(2, 13):
            ex.append(dt.date(year, month, 1))
        ex.append(dt.date(year, 12, 17))
    business = [d + dt.timedelta(days=(7 - d.weekday()) % 7 if d.weekday() > 4 else 0) for d in ex]
    divs = {d.isoformat(): 0.25 for d in business}
    yields = DividendYields("x", _history("2020-01-01", "2024-12-31", 80.0, divs))
    for probe in ("2023-03-15", "2023-12-20", "2024-01-25", "2024-02-20"):
        got = yields.at(dt.date.fromisoformat(probe))
        assert got.source == "measured", probe
        assert len(got.payments) == 12, probe
        assert got.q == pytest.approx(_q(3.0, 80.0)), probe


def test_a_suspended_payer_goes_to_zero_and_says_so() -> None:
    # Boeing: quarterly until 2020-02-13, then nothing.
    ex = [*_third_fridays(range(2017, 2020), (2, 5, 8, 11)), "2020-02-13"]
    yields = DividendYields(
        "x", _history("2017-01-02", "2026-10-09", 200.0, dict.fromkeys(ex, 2.0))
    )
    assert yields.at(dt.date(2020, 3, 1)).source == "measured"
    late = yields.at(dt.date(2026, 10, 9))
    assert late.source == "suspended"
    assert late.q == 0.0


def test_a_resumed_payer_starts_a_fresh_run() -> None:
    # AAPL: paid in the 1990s, suspended, resumed quarterly in 2012.
    old = _third_fridays(range(1993, 1996), (2, 5, 8, 11))
    new = ["2012-08-09", "2012-11-07", "2013-02-07", "2013-05-09"]
    divs = {**dict.fromkeys(old, 0.12), **dict.fromkeys(new, 2.65)}
    yields = DividendYields("x", _history("1993-01-04", "2013-08-30", 500.0, divs))
    first = yields.at(dt.date(2012, 9, 1))
    assert first.source == "unknown"  # one payment: no frequency to read yet
    second = yields.at(dt.date(2012, 12, 1))
    assert second.source == "short_history"
    # Two payments of the new run, scaled to a year -- never the 1995 cents.
    assert second.q == pytest.approx(_q(2.65 * 4, 500.0))
    assert all(p.ex_date.year == 2012 for p in second.payments)
    assert yields.at(dt.date(2013, 6, 1)).source == "measured"


def test_a_cut_lags_by_n_payments_and_a_restoration_is_not_dropped() -> None:
    # JPM: 0.38 a quarter, cut to 0.05 in 2009, restored to 0.25 in 2011.
    ex = _third_fridays(range(2008, 2012), (1, 4, 7, 10))
    cash = [0.38] * 5 + [0.05] * 9 + [0.25] * 2
    yields = DividendYields(
        "x", _history("2008-01-02", "2011-12-30", 40.0, dict(zip(ex, cash, strict=True)))
    )
    lagged = yields.at(dt.date(2009, 5, 1))  # one 0.05 and three 0.38 in D
    assert lagged.q == pytest.approx(_q(0.05 + 3 * 0.38, 40.0))
    restored = yields.at(dt.date(2011, 8, 1))
    # The first 0.25 enters D at once (three 0.05s and the 0.25) -- a
    # "special" filter would have dropped it as 5x the median payment.
    assert restored.q == pytest.approx(_q(0.25 + 3 * 0.05, 40.0))


def test_a_frequency_switch_biases_both_ways_as_disclosed() -> None:
    # TSM-like: annual 3.0 to mid-2019, then quarterly 0.9. Until the new
    # frequency fills the two-year count, q is off in BOTH directions -- the
    # disclosed cost of reading the frequency from the data -- and then settles.
    annual = ["2015-06-17", "2016-06-15", "2017-06-14", "2018-06-13", "2019-06-12"]
    quarterly = ["2019-09-17", "2019-12-17", "2020-03-17", "2020-06-16", "2020-09-16"]
    divs = {**dict.fromkeys(annual, 3.0), **dict.fromkeys(quarterly, 0.9)}
    yields = DividendYields("x", _history("2015-01-02", "2020-12-31", 100.0, divs))
    truth = _q(3.6, 100.0)
    assert yields.at(dt.date(2019, 10, 15)).q > truth  # the 3.0 and a 0.9 read as two halves
    assert yields.at(dt.date(2020, 1, 15)).q < truth  # two 0.9s read as a half-year
    assert yields.at(dt.date(2020, 3, 19)).q > truth  # four payments, the 3.0 still in D
    assert yields.at(dt.date(2020, 7, 1)).q == pytest.approx(truth)


def test_splits_put_every_payment_on_the_pricing_date_basis() -> None:
    # NVDA-like: 4:1 then 10:1, with the close dropping at each split.
    ex = _third_fridays(range(2020, 2025), (3, 6, 9, 12))
    cash = {}
    for day in ex:
        d = dt.date.fromisoformat(day)
        cash[day] = (
            0.16 if d < dt.date(2021, 7, 20) else 0.04 if d < dt.date(2024, 6, 10) else 0.004
        )
    frame = _history(
        "2020-01-02",
        "2024-12-31",
        2400.0,
        cash,
        splits={"2021-07-20": 4.0, "2024-06-10": 10.0},
        close_by_date={"2021-07-20": 600.0, "2024-06-10": 60.0},
    )
    yields = DividendYields("x", frame)
    # Across both splits the yield is the same 0.64/2400 = 0.16/600 = 0.04/60.
    for probe in ("2021-03-01", "2021-08-02", "2022-06-01", "2024-07-01", "2024-12-31"):
        got = yields.at(dt.date.fromisoformat(probe))
        assert got.q == pytest.approx(_q(0.64, 2400.0), rel=1e-9), probe


def test_a_non_payer_is_a_real_zero() -> None:
    yields = DividendYields("x", _history("2020-01-02", "2020-12-31", 180.0, {}))
    got = yields.at(dt.date(2020, 6, 1))
    assert (got.q, got.source) == (0.0, "non_payer")


def test_no_row_after_the_date_is_read() -> None:
    ex = _third_fridays(range(2021, 2023), (3, 6, 9, 12))
    frame = _history("2021-01-04", "2022-12-30", 400.0, dict.fromkeys(ex, 1.5))
    yields = DividendYields("x", frame)
    probe = dt.date(2022, 6, 1)
    before = yields.at(probe)
    # A huge future dividend and a crash in the close must not move it.
    frame.loc[frame["trade_date"] > pd.Timestamp(probe), ["div_cash", "close"]] = [50.0, 1.0]
    assert DividendYields("x", frame).at(probe) == before


def test_a_date_before_the_data_is_unknown() -> None:
    yields = DividendYields("x", _history("2021-01-04", "2021-12-31", 400.0, {}))
    got = yields.at(dt.date(2020, 1, 1))
    assert (got.source, got.close) == ("unknown", None)


def test_past_the_last_row_the_value_is_carried_then_flagged_stale() -> None:
    ex = _third_fridays(range(2021, 2023), (3, 6, 9, 12))
    yields = DividendYields(
        "x", _history("2021-01-04", "2022-12-30", 400.0, dict.fromkeys(ex, 1.5))
    )
    last = dt.date(2022, 12, 30)
    inside = yields.at(last)
    carried = yields.at(last + dt.timedelta(days=CARRY_STALE_DAYS))
    stale = yields.at(last + dt.timedelta(days=CARRY_STALE_DAYS + 1))
    assert (carried.source, carried.age_days, carried.q) == ("carried", CARRY_STALE_DAYS, inside.q)
    assert (stale.source, stale.q) == ("stale", inside.q)


def test_build_groups_by_symbol() -> None:
    a = _history("2021-01-04", "2021-03-31", 10.0, {}).assign(symbol="aaa")
    b = _history("2021-01-04", "2021-03-31", 20.0, {}).assign(symbol="bbb")
    built = build_dividend_yields(pd.concat([a, b]))
    assert set(built) == {"aaa", "bbb"}
    assert built["bbb"].last_date == dt.date(2021, 3, 31)


def test_a_real_zero_carried_past_the_data_keeps_its_label() -> None:
    # Only a measured yield turns into "carried"/"stale": a non-payer is still a
    # non-payer the day after the data ends.
    yields = DividendYields("x", _history("2021-01-04", "2021-12-31", 50.0, {}))
    got = yields.at(dt.date(2022, 3, 1))
    assert (got.q, got.source, got.age_days) == (0.0, "non_payer", 60)


def test_a_single_payment_ever_has_no_frequency_and_is_unknown() -> None:
    yields = DividendYields("x", _history("2021-01-04", "2021-12-31", 50.0, {"2021-06-15": 1.0}))
    got = yields.at(dt.date(2021, 9, 1))
    assert (got.q, got.source, got.close) == (0.0, "unknown", None)


def test_a_resumed_run_of_one_old_payment_is_suspended_not_unknown() -> None:
    # Quarterly, a four-year silence, one payment -- then silence again. The
    # lone payment has no gap of its own, so its staleness is judged on the
    # seed period: two years later it is a suspension, not an "unknown".
    old = _third_fridays(range(2012, 2015), (3, 6, 9, 12))
    divs = {**dict.fromkeys(old, 0.5), "2019-03-15": 0.5}
    yields = DividendYields("x", _history("2012-01-02", "2021-06-30", 40.0, divs))
    assert yields.at(dt.date(2019, 4, 1)).source == "unknown"
    late = yields.at(dt.date(2021, 6, 30))
    assert (late.q, late.source) == (0.0, "suspended")


def test_a_dividend_the_close_cannot_support_is_unknown_not_a_negative_log() -> None:
    # A feed error (cash >= price) would make -ln(1 - D/S) undefined; it is
    # refused as unknown rather than priced.
    ex = _third_fridays(range(2021, 2023), (3, 6, 9, 12))
    yields = DividendYields("x", _history("2021-01-04", "2022-12-30", 3.0, dict.fromkeys(ex, 1.0)))
    got = yields.at(dt.date(2022, 11, 1))
    assert (got.q, got.source) == (0.0, "unknown")


# --- regressions from adversarial review, round 1 -----------------------------


def test_two_extra_payments_do_not_flip_a_quarterly_payer_to_monthly() -> None:
    # A gap median halves to ~45 days and snaps to 12: D summed three years of
    # dividends (q ~2.6x), then a false "suspended" 61 days later. The count
    # over two years reads 10 payments = 5 a year, still quarterly.
    ex = _third_fridays(range(2016, 2021), (3, 6, 9, 12))
    divs = {**dict.fromkeys(ex, 1.0), "2019-06-03": 0.10, "2019-09-03": 0.10}
    yields = DividendYields("x", _history("2016-01-04", "2020-12-31", 100.0, divs))
    for probe in ("2019-10-25", "2019-12-31", "2020-01-06", "2020-02-14"):
        got = yields.at(dt.date.fromisoformat(probe))
        assert got.source == "measured", probe
        assert len(got.payments) == 4, probe
        assert got.q < 1.2 * _q(4.0, 100.0), probe


def test_a_payment_on_the_pricing_date_itself_counts() -> None:
    ex = _third_fridays(range(2021, 2023), (3, 6, 9, 12))
    yields = DividendYields(
        "x", _history("2021-01-04", "2022-12-30", 100.0, dict.fromkeys(ex, 1.0))
    )
    day = dt.date.fromisoformat(ex[-1])
    assert yields.at(day).payments[-1].ex_date == day


def test_a_split_on_a_payments_own_ex_date_does_not_divide_that_payment() -> None:
    # Tiingo states the dividend on the post-split basis when both fall on one
    # day; only splits AFTER the ex-date re-base it.
    ex = _third_fridays(range(2021, 2023), (3, 6, 9, 12))
    cash = {d: (1.0 if d < "2022-06-17" else 0.5) for d in ex}
    frame = _history(
        "2021-01-04",
        "2022-12-30",
        100.0,
        cash,
        splits={"2022-06-17": 2.0},
        close_by_date={"2022-06-17": 50.0},
    )
    got = DividendYields("x", frame).at(dt.date(2022, 6, 20))
    assert [p.adjusted for p in got.payments][-1] == pytest.approx(0.5)
    assert got.q == pytest.approx(_q(0.5 * 4, 50.0))


def test_a_split_on_the_pricing_date_rebases_every_payment_that_day() -> None:
    # A 4:1 split effective today: today's close is post-split, so every
    # payment in D must be too -- else D is 4x too large against S (q ~ 17%).
    ex = _third_fridays(range(2021, 2023), (3, 6, 9, 12))
    split_day = "2022-10-03"
    frame = _history(
        "2021-01-04",
        "2022-12-30",
        100.0,
        dict.fromkeys(ex, 1.0),
        splits={split_day: 4.0},
        close_by_date={split_day: 25.0},
    )
    got = DividendYields("x", frame).at(dt.date.fromisoformat(split_day))
    assert [p.adjusted for p in got.payments] == pytest.approx([0.25] * 4)
    assert got.q == pytest.approx(_q(1.0, 25.0))


def test_a_dividend_exactly_equal_to_the_close_is_unknown() -> None:
    ex = _third_fridays(range(2021, 2023), (3, 6, 9, 12))
    yields = DividendYields("x", _history("2021-01-04", "2022-12-30", 4.0, dict.fromkeys(ex, 1.0)))
    got = yields.at(dt.date(2022, 11, 1))  # D = 4.0 = close: -ln(0) is undefined
    assert (got.q, got.source, got.close) == (0.0, "unknown", None)


def test_a_monthly_payers_49_day_year_end_gap_is_not_a_suspension() -> None:
    # Why the suspension bar is twice the period, not 1.5x: iShares' December
    # double payment leaves a ~49-day gap to February (1.5 x 30.4 = 45.6).
    ex = [dt.date(2022, m, 2) for m in range(1, 13)] + [dt.date(2023, 2, 2), dt.date(2023, 3, 2)]
    ex += [dt.date(2022, 12, 12)]
    business = [d + dt.timedelta(days=(7 - d.weekday()) % 7 if d.weekday() > 4 else 0) for d in ex]
    divs = {d.isoformat(): 0.3 for d in business}
    yields = DividendYields("x", _history("2022-01-03", "2023-03-31", 80.0, divs))
    got = yields.at(dt.date(2023, 1, 30))  # 49 days after the 2022-12-12 payment
    assert got.source == "measured"


def test_a_one_year_silence_in_a_quarterly_payer_starts_a_fresh_run() -> None:
    # A gap of ~4 periods is a suspension and resumption (> 3 x P0): the old
    # payments must not be averaged into the new run.
    old = _third_fridays(range(2018, 2020), (3, 6, 9, 12))
    new = _third_fridays(range(2021, 2022), (3, 6))
    divs = {**dict.fromkeys(old, 2.0), **dict.fromkeys(new, 0.5)}
    yields = DividendYields("x", _history("2018-01-02", "2021-08-31", 100.0, divs))
    got = yields.at(dt.date(2021, 7, 1))
    assert got.source == "short_history"
    assert all(p.cash == 0.5 for p in got.payments)


def test_the_reported_d_always_reproduces_q_even_when_scaled() -> None:
    # The page redoes q = -ln(1 - D / S) from these fields; for short_history D
    # is the payments' sum scaled to a year, and the identity must still hold.
    yields = DividendYields(
        "x", _history("2021-01-04", "2021-12-31", 100.0, {"2021-03-19": 1.0, "2021-06-18": 1.0})
    )
    got = yields.at(dt.date(2021, 7, 1))
    assert (got.source, got.per_year) == ("short_history", 4)
    assert got.annual == pytest.approx(4.0)  # 2 payments x 4/2
    assert got.q == pytest.approx(-math.log1p(-got.annual / got.close))


# --- regressions from adversarial review, round 2 -----------------------------


def test_an_annual_payer_whose_ex_date_drifts_earlier_reads_once_a_year() -> None:
    # Third Monday of December (GDX-like): ex-dates 357-371 days apart, so a
    # 730-day window held three of them and doubled D. Every ex-date and the
    # day after must read N = 1 across twelve years.
    ex = []
    for year in range(2012, 2025):
        first = dt.date(year, 12, 1)
        ex.append((first + dt.timedelta(days=(0 - first.weekday()) % 7 + 14)).isoformat())
    yields = DividendYields("x", _history("2012-01-02", "2024-12-31", 50.0, dict.fromkeys(ex, 0.5)))
    for day in ex[2:]:
        d = dt.date.fromisoformat(day)
        for probe in (d, d + dt.timedelta(days=1)):
            got = yields.at(probe)
            assert (got.per_year, got.annual) == (1, pytest.approx(0.5)), probe


def test_a_young_run_with_two_specials_stays_quarterly() -> None:
    # Resumed 2019-01-15, specials 2020-03-02 and 2020-05-01: a median of the
    # last four gaps flipped this to 12 a year (q ~3x) and then "suspended".
    regular = [
        "2019-01-15",
        "2019-04-15",
        "2019-07-15",
        "2019-10-15",
        "2020-01-15",
        "2020-04-15",
        "2020-07-15",
    ]
    divs = {**dict.fromkeys(regular, 1.0), "2020-03-02": 0.1, "2020-05-01": 0.1}
    yields = DividendYields(
        "x",
        _history("2015-01-02", "2020-09-30", 100.0, {**divs, "2015-03-16": 1.0, "2015-06-15": 1.0}),
    )
    for probe in ("2020-05-01", "2020-07-01", "2020-08-03", "2020-09-30"):
        got = yields.at(dt.date.fromisoformat(probe))
        assert got.per_year == 4, probe
        assert got.source in {"measured", "short_history"}, probe


def test_a_lone_resumed_payment_is_judged_on_the_snapped_seed_period() -> None:
    # A quarterly payer (seed ~91 days), silent for years, pays once: 2 x 91 days
    # later it is suspended -- not 2 x 91 / 365 days, not two years later.
    old = _third_fridays(range(2012, 2015), (3, 6, 9, 12))
    divs = {**dict.fromkeys(old, 0.5), "2019-03-15": 0.5}
    yields = DividendYields("x", _history("2012-01-02", "2019-12-31", 40.0, divs))
    lone = yields.at(dt.date(2019, 7, 1))
    assert lone.source == "unknown"  # 108 days: under 2 x 91
    # Unknown carries no close: there is no D for it to be the S of.
    assert (lone.q, lone.close, lone.payments) == (0.0, None, ())
    assert yields.at(dt.date(2019, 9, 2)).source == "unknown"  # 171 days: still under 182
    assert yields.at(dt.date(2019, 10, 1)).source == "suspended"  # 200 days: over


def test_a_lone_resumed_payment_uses_the_snapped_period_not_the_raw_seed() -> None:
    # Old payments every 60 days (seed 60, snaps to 4 a year = 91.25 days). A
    # lone resumed payment is "unknown" at 150 days (under 2 x 91.25) -- it would
    # read "suspended" if the raw 60-day seed were used (2 x 60 = 120).
    old = [(dt.date(2013, 1, 7) + dt.timedelta(days=60 * i)) for i in range(10)]
    old = [d + dt.timedelta(days=(7 - d.weekday()) % 7 if d.weekday() > 4 else 0) for d in old]
    divs = {**{d.isoformat(): 0.5 for d in old}, "2019-03-15": 0.5}
    yields = DividendYields("x", _history("2013-01-02", "2019-12-31", 40.0, divs))
    assert yields.at(dt.date(2019, 8, 12)).source == "unknown"  # 150 days after


# --- pinned disclosures (adversarial review, round 3) -------------------------
# These are known, disclosed weaknesses. Pinned so a change to them is a
# deliberate edit to the docstring and DATA_CONTRACTS #14, not a silent drift.


def test_a_small_special_displaces_a_regular_payment_and_deflates_q() -> None:
    annual = ["2021-05-03", "2022-05-02", "2023-05-01"]
    divs = {**dict.fromkeys(annual, 2.0), "2024-03-01": 0.5}
    got = DividendYields("x", _history("2021-01-04", "2024-04-30", 100.0, divs)).at(
        dt.date(2024, 4, 1)
    )
    # Still annual, but the special IS the last payment: D = 0.50, not 2.00.
    assert (got.per_year, got.annual) == (1, pytest.approx(0.5))


def test_one_special_reads_a_semiannual_payer_as_quarterly() -> None:
    regular = ["2021-01-11", "2021-07-12", "2022-01-10", "2022-07-11", "2023-01-09", "2023-07-10"]
    divs = {**dict.fromkeys(regular, 1.0), "2023-06-14": 1.0}
    # On 2023-12-29 the window holds 2022-07, 2023-01, the special and 2023-07:
    # a ~121-day mean gap, read as quarterly.
    got = DividendYields("x", _history("2021-01-04", "2023-12-29", 100.0, divs)).at(
        dt.date(2023, 12, 29)
    )
    assert (got.per_year, got.annual) == (4, pytest.approx(4.0))


def test_a_quarterly_payer_skipping_two_quarters_keeps_its_run() -> None:
    # A ~273-day gap is under 3 x 91 (273.75): a skip, not a suspension.
    ex = _third_fridays(range(2019, 2021), (3, 6, 9, 12))
    ex = [d for d in ex if d not in ("2020-03-20", "2020-06-19")]
    got = DividendYields(
        "x", _history("2019-01-02", "2020-12-31", 100.0, dict.fromkeys(ex, 1.0))
    ).at(dt.date(2020, 10, 1))
    assert got.source == "measured"
    assert got.payments[0].ex_date.year == 2019  # the run did not restart


def test_a_payer_with_only_old_payments_reads_its_last_two_gaps() -> None:
    # Nothing in the 700-day window: the frequency comes from the last two
    # payments however old -- before the suspension check decides it is stale.
    ex = _third_fridays(range(2015, 2017), (3, 6, 9, 12))
    yields = DividendYields(
        "x", _history("2015-01-02", "2017-03-31", 100.0, dict.fromkeys(ex, 1.0))
    )
    assert yields.at(dt.date(2017, 3, 31)).per_year == 4


def test_the_session_cache_returns_each_sessions_own_answer() -> None:
    # A sweep asks the same DividendYields for ~2,000 dates. A cache keyed
    # wrongly (a neighbouring session, a bucket of sessions) would misprice
    # silently: every answer must equal a fresh instance's, in any order.
    import random

    ex = _third_fridays(range(2018, 2023), (3, 6, 9, 12))
    cash = {d: (1.0 if d < "2021-01-01" else 0.25) for d in ex}
    frame = _history(
        "2018-01-02",
        "2022-12-30",
        100.0,
        cash,
        splits={"2020-08-31": 4.0},
        close_by_date={"2020-08-31": 25.0},
    )
    days = [d.date() for d in pd.bdate_range("2018-06-01", "2022-12-30")]
    expected = {d: DividendYields("x", frame).at(d) for d in days[::7]}
    for order in (days, random.Random(7).sample(days, len(days))):
        cached = DividendYields("x", frame)
        for d in order:
            if d in expected:
                assert cached.at(d) == expected[d], d
            else:
                cached.at(d)


def test_the_session_cache_computes_each_session_once(monkeypatch: pytest.MonkeyPatch) -> None:
    ex = _third_fridays(range(2021, 2023), (3, 6, 9, 12))
    yields = DividendYields(
        "x", _history("2021-01-04", "2022-12-30", 100.0, dict.fromkeys(ex, 1.0))
    )
    calls = 0
    real = yields._on_session

    def counting(idx: int) -> object:
        nonlocal calls
        calls += 1
        return real(idx)

    monkeypatch.setattr(yields, "_on_session", counting)
    for _ in range(5):
        yields.at(dt.date(2022, 6, 1))
    assert calls == 1


def test_the_bounded_session_cache_clears_without_changing_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bounded so a long-lived process cannot grow it without limit; clearing
    # mid-sweep must never change what any session reads.
    import random

    from tail_lab.transforms import dividend_yield as module

    monkeypatch.setattr(module, "_SESSION_CACHE_MAX", 5)
    ex = _third_fridays(range(2019, 2023), (3, 6, 9, 12))
    frame = _history(
        "2019-01-02", "2022-12-30", 100.0, dict.fromkeys(ex, 1.0), splits={"2021-03-01": 2.0}
    )
    days = [d.date() for d in pd.bdate_range("2019-06-03", "2022-12-30")]
    expected = {d: DividendYields("x", frame).at(d) for d in days[::11]}
    bounded = DividendYields("x", frame)
    for d in random.Random(3).sample(days, len(days)):
        got = bounded.at(d)
        assert len(bounded._sessions) <= 5
        if d in expected:
            assert got == expected[d], d


def test_stale_means_three_weeks_as_every_doc_and_the_timer_promise() -> None:
    # docs/DATA_FLOW.md, docs/DATA_CONTRACTS.md #14 and the Tiingo timer all
    # say a carried yield turns "stale" after 21 days -- two missed weekly
    # runs (it flips at midnight on day 22, before that Saturday's run). The
    # constant is pinned by value so a change has to touch them too.
    assert CARRY_STALE_DAYS == 21
    for doc in (
        "docs/DATA_FLOW.md",
        "docs/DATA_CONTRACTS.md",
        "docs/systemd/tail-lab-tiingo.timer",
    ):
        text = (Path(__file__).parents[1] / doc).read_text()
        assert "21 days" in text or "three weeks" in text, doc


def test_an_annual_payer_is_suspended_only_after_two_full_years() -> None:
    # The cut-off is strict: at exactly 2 x 365 days since the last payment the
    # run still counts; one day later it is suspended. For an annual payer the
    # boundary lands on a whole day, so ">" and ">=" are different rules.
    ex = ["2016-05-04", "2017-05-04", "2018-05-04", "2019-05-03", "2020-05-04"]
    yields = DividendYields("x", _history("2015-01-02", "2022-12-30", 50.0, dict.fromkeys(ex, 2.0)))
    last = dt.date(2020, 5, 4)
    assert yields.at(last + dt.timedelta(days=730)).source == "measured"
    assert yields.at(last + dt.timedelta(days=731)).source == "suspended"


def test_the_frequency_snaps_on_a_log_scale() -> None:
    # A 125-day mean gap is 2.92 payments a year: nearer 4 than 2 by ratio
    # (2.92/2 = 1.46 vs 4/2.92 = 1.37), nearer 2 by plain distance. Payment
    # counts are multiplicative, so the ratio decides.
    first = dt.date(2015, 1, 5)
    ex = [(first + dt.timedelta(days=125 * i)) for i in range(12)]
    ex = [d + dt.timedelta(days=(7 - d.weekday()) % 7) if d.weekday() > 4 else d for d in ex]
    yields = DividendYields(
        "x", _history("2015-01-02", "2019-12-31", 100.0, {d.isoformat(): 1.0 for d in ex})
    )
    assert yields.at(ex[-1] + dt.timedelta(days=3)).per_year == 4


def test_a_lone_resumed_annual_payment_is_suspended_only_after_two_full_years() -> None:
    # The lone-payment path's cut-off is strict too: day 730 still counts.
    old = ["2010-05-03", "2011-05-03", "2012-05-03", "2013-05-03", "2014-05-05"]
    divs = {**dict.fromkeys(old, 2.0), "2018-04-30": 2.0}
    yields = DividendYields("x", _history("2010-01-04", "2021-12-31", 50.0, divs))
    last = dt.date(2018, 4, 30)
    assert yields.at(last + dt.timedelta(days=730)).source == "unknown"
    assert yields.at(last + dt.timedelta(days=731)).source == "suspended"


def test_a_pause_of_exactly_three_periods_continues_the_run() -> None:
    # Gaps of 91 days, then exactly 273 (3 x 91): a new run starts only on a
    # gap LONGER than 3 x P0, so this payer is still measured, not restarted.
    first = dt.date(2015, 1, 5)  # a Monday; 91 days is 13 weeks, so every ex-date is a Monday
    ex = [first + dt.timedelta(days=91 * i) for i in range(8)]
    ex.append(ex[-1] + dt.timedelta(days=273))
    yields = DividendYields(
        "x", _history("2015-01-02", "2018-12-31", 100.0, {d.isoformat(): 0.5 for d in ex})
    )
    got = yields.at(ex[-1] + dt.timedelta(days=1))
    assert got.source == "measured"


def test_a_change_of_frequency_is_read_over_a_700_day_window() -> None:
    # Quarterly until 2015-09-28, semiannual after. A year into the new
    # schedule the 700-day window still holds the quarterly tail and reads 4;
    # the docstring's window is the rule, so a 600-day one (reading 2) is not.
    start = dt.date(2012, 1, 2)  # a Monday; 91 and 182 days keep every ex-date a Monday
    ex = [start + dt.timedelta(days=91 * i) for i in range(16)]
    ex += [ex[-1] + dt.timedelta(days=182 * i) for i in range(1, 6)]
    yields = DividendYields(
        "x", _history("2012-01-02", "2018-12-31", 100.0, {d.isoformat(): 0.5 for d in ex})
    )
    assert yields.at(dt.date(2016, 10, 3)).per_year == 4


def test_a_run_with_one_payment_in_the_window_reads_its_last_two() -> None:
    # Semiannual (182 days) then a 329-day gap, read 372 days after the last
    # payment: only one payment sits in the window, so the frequency comes
    # from the last TWO (329 days -> 1 a year) -- measured, not suspended.
    first = dt.date(2012, 1, 2)
    ex = [first + dt.timedelta(days=182 * i) for i in range(6)]
    ex.append(ex[-1] + dt.timedelta(days=329))
    ex = [d + dt.timedelta(days=(7 - d.weekday()) % 7) if d.weekday() > 4 else d for d in ex]
    last = ex[-1]
    day = last + dt.timedelta(days=372)
    day = day + dt.timedelta(days=(7 - day.weekday()) % 7) if day.weekday() > 4 else day
    yields = DividendYields(
        "x", _history("2012-01-02", "2017-12-29", 100.0, {d.isoformat(): 0.5 for d in ex})
    )
    got = yields.at(day)
    assert (got.source, got.per_year) == ("measured", 1)


def test_three_of_four_quarterly_payments_are_scaled_to_a_year() -> None:
    # A quarterly payer with only 3 payments in its run (AAPL in early 2013):
    # D is those three scaled by 4/3, labelled short_history -- not the three
    # alone, which would read q a quarter low.
    old = ["2005-03-15", "2005-06-15", "2005-09-15", "2005-12-15"]
    new = ["2012-08-09", "2012-11-07", "2013-02-07"]
    divs = {**dict.fromkeys(old, 0.25), **dict.fromkeys(new, 2.65)}
    got = DividendYields("x", _history("2005-01-03", "2013-03-29", 450.0, divs)).at(
        dt.date(2013, 3, 1)
    )
    assert got.source == "short_history" and got.per_year == 4
    assert got.annual == pytest.approx(2.65 * 3 * 4 / 3)
    assert got.q == pytest.approx(_q(2.65 * 4, 450.0))


def test_a_carried_short_history_is_labelled_carried_then_stale() -> None:
    # The data ends after a resumed payer's second payment: past the last row
    # its scaled short_history yield is carried (and labelled so), then stale.
    old = _third_fridays(range(1993, 1996), (2, 5, 8, 11))
    new = ["2012-08-09", "2012-11-07"]
    divs = {**dict.fromkeys(old, 0.12), **dict.fromkeys(new, 2.65)}
    yields = DividendYields("x", _history("1993-01-04", "2012-12-31", 500.0, divs))
    inside = yields.at(dt.date(2012, 12, 31))
    assert inside.source == "short_history"
    carried = yields.at(dt.date(2013, 1, 10))
    assert (carried.source, carried.q, carried.annual) == ("carried", inside.q, inside.annual)
    assert yields.at(dt.date(2013, 2, 15)).source == "stale"


def test_the_gap_window_is_four_gaps_wide() -> None:
    # _GAP_WINDOW (4) is how many recent gaps seed the median a pause is
    # measured against. Gaps here, oldest first: 91, 365, 91, 91, 365 days.
    # The last four (365, 91, 91, 365) have median 228, so no gap exceeds
    # 3 x 228 and the whole run counts: the yield is measured. A window of
    # three (91, 91, 365) or five (91, 365, 91, 91, 365) has median 91, the
    # last 365-day gap reads as a pause, and a fresh run of one payment has no
    # frequency to read -- unknown. Either neighbour of 4 flips this.
    ex = ["2006-03-16", "2006-06-15", "2007-06-15", "2007-09-14", "2007-12-14", "2008-12-15"]
    yields = DividendYields(
        "x", _history("2006-01-03", "2009-01-15", 100.0, dict.fromkeys(ex, 1.0))
    )
    got = yields.at(dt.date(2009, 1, 15))
    assert got.source == "measured"
    # The 700-day window holds the last four payments (mean gap ~183 days):
    # semiannual, so the last two make D.
    assert got.per_year == 2
    assert got.q == pytest.approx(_q(2.0, 100.0))
