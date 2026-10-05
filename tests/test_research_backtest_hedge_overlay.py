"""Tests for the hedge-ratio blend that tests Rodman's Paradox.

The pinned case is volatility pumping with a closed form: a program that
doubles then halves every two months, against a flat equity leg. Each asset
ends where it started, yet a monthly-rebalanced blend at weight ``w`` grows by
``(1 + w)(1 - w/2)`` per two months -- maximised at exactly ``w = 0.5``, where
it is 1.125. That is the letter's "sum greater than the parts" in a form a
wrong rebalancing rule cannot pass.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.hedge_overlay import (
    COLE_WINDOW,
    MIN_MARGIN,
    OVERLAY_PROGRAMS,
    WEIGHTS,
    OverlayWindow,
    _verdict,
    blend_nav,
    compute_hedge_overlay,
    evaluate_window,
    outcome,
    run_hedge_overlay,
    run_overlay_window,
    total_return_levels,
)
from tail_lab.research.backtest.index_replication import (
    DAYS_PER_YEAR,
    DEFAULT_DIVIDEND_YIELD,
    SENSITIVITY_YIELDS,
)

#: Odd, so the pump takes a whole number of up/down pairs and ends flat.
N_MONTHS = 73


def _monthly(n: int = N_MONTHS, start: str = "2005-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="MS")


def _pump(index: pd.DatetimeIndex) -> pd.Series:
    """Doubles, then halves, forever: ends flat after every even step."""
    return pd.Series([2.0 if i % 2 else 1.0 for i in range(len(index))], index=index)


def _flat(index: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(np.ones(len(index)), index=index)


# ------------------------------------------------------------ total return


def test_a_flat_price_accrues_exactly_the_assumed_yield() -> None:
    index = pd.DatetimeIndex(["2020-01-01", "2021-01-01"])  # 366 days
    levels = total_return_levels(pd.Series([100.0, 100.0], index=index), dividend_yield=0.02)
    assert levels.iloc[0] == 1.0
    # Compounds to exactly the stated rate: 2% a year, pro rata for 366 days.
    assert levels.iloc[-1] == pytest.approx(1.02 ** (366 / DAYS_PER_YEAR))


def test_daily_accrual_compounds_to_the_stated_rate_not_above_it() -> None:
    """Adding y * days / 365.25 every business day compounds to ~1.92% on a
    1.9% assumption -- 2bp/yr, twice the verdict threshold. Over ten flat
    years the index must grow at exactly the stated rate."""
    index = pd.bdate_range("2010-01-04", "2019-12-31")
    levels = total_return_levels(pd.Series(100.0, index=index), dividend_yield=0.019)
    years = (index[-1] - index[0]).days / DAYS_PER_YEAR
    assert levels.iloc[-1] ** (1 / years) - 1 == pytest.approx(0.019, abs=1e-12)


def test_a_zero_yield_is_the_price_return() -> None:
    index = pd.DatetimeIndex(["2020-01-01", "2020-01-02", "2020-01-03"])
    levels = total_return_levels(pd.Series([100.0, 110.0, 99.0], index=index), dividend_yield=0)
    assert levels.to_list() == pytest.approx([1.0, 1.1, 0.99])


# ------------------------------------------------------------ blending


def test_monthly_rebalancing_harvests_the_closed_form_pump() -> None:
    index = _monthly(3)
    nav = blend_nav(_pump(index), _flat(index), weight=0.5)
    # Month 1: 0.5*2 + 0.5*1 = 1.5. Rebalanced, then 1.5*(0.5*0.5 + 0.5) = 1.125.
    assert nav.to_list() == pytest.approx([1.0, 1.5, 1.125])


def test_the_book_drifts_inside_a_month_and_only_resets_on_the_first_day() -> None:
    """A daily rebalance would turn 1 -> 2 -> 1 into 1.5 then 1.125; a monthly
    one, holding the drifted weights, comes back to exactly 1.0."""
    index = pd.DatetimeIndex(["2020-01-02", "2020-01-03", "2020-01-06"])
    program = pd.Series([1.0, 2.0, 1.0], index=index)
    nav = blend_nav(program, _flat(index), weight=0.5)
    assert nav.to_list() == pytest.approx([1.0, 1.5, 1.0])


@pytest.mark.parametrize(("weight", "expected"), [(0.0, "equity"), (1.0, "program")])
def test_the_ends_of_the_blend_are_the_pure_legs(weight: float, expected: str) -> None:
    index = pd.bdate_range("2020-01-01", periods=80)
    rng = np.random.default_rng(7)
    legs = {
        "program": pd.Series(np.cumprod(1 + rng.normal(0, 0.01, 80)), index=index),
        "equity": pd.Series(np.cumprod(1 + rng.normal(0, 0.01, 80)), index=index),
    }
    nav = blend_nav(legs["program"], legs["equity"], weight=weight)
    pure = legs[expected]
    assert nav.to_numpy() == pytest.approx((pure / pure.iloc[0]).to_numpy())


def test_the_rebalance_is_on_the_first_trading_day_of_the_month_not_the_last() -> None:
    """The two rules differ only when the price moves on the first day of the
    new month. Rebalancing at the 31 Jan close would give 2.25 on 3 Feb; the
    documented rule carries January's drift through 3 Feb's close (2.5)."""
    index = pd.DatetimeIndex(["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-04"])
    program = pd.Series([1.0, 2.0, 4.0, 2.0], index=index)
    nav = blend_nav(program, _flat(index), weight=0.5)
    assert nav.to_list() == pytest.approx([1.0, 1.5, 2.5, 1.875])


def test_blending_misaligned_legs_is_a_caller_bug() -> None:
    a = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(["2020-01-02", "2020-01-03"]))
    b = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(["2020-01-02", "2020-01-06"]))
    with pytest.raises(ValueError, match="share one date index"):
        blend_nav(a, b, weight=0.5)


# ------------------------------------------------------------ the verdict


def _window(program: pd.Series, equity: pd.Series, *, dividend_yield: float = 0.0) -> OverlayWindow:
    return run_overlay_window(
        program,
        equity,
        key="t",
        label="t",
        start=program.index[0].date(),
        end=program.index[-1].date(),
        dividend_yield=dividend_yield,
    )


def test_the_paradox_holds_at_exactly_the_analytic_optimum() -> None:
    index = _monthly()
    window = _window(_pump(index), _flat(index))
    by_weight = {p.weight: p for p in window.points}
    assert window.best_weight == 0.5
    assert window.outcome == "holds"
    years = (index[-1] - index[0]).days / DAYS_PER_YEAR
    pairs = (N_MONTHS - 1) // 2
    assert by_weight[0.5].cagr == pytest.approx(1.125 ** (pairs / years) - 1)
    # Each leg alone ends exactly where it started.
    assert by_weight[0.0].cagr == pytest.approx(0.0)
    assert by_weight[1.0].cagr == pytest.approx(0.0)
    assert window.margin == pytest.approx(by_weight[0.5].cagr)


def test_a_riskless_leg_leaves_the_risk_adjusted_test_undefined_not_zero() -> None:
    """The flat equity leg has zero volatility, so CAGR / vol does not exist
    at w = 0. Scoring it as 0 (or skipping it) would let any mix "win"."""
    index = _monthly()
    window = _window(_pump(index), _flat(index))
    assert window.points[0].volatility == 0.0
    assert window.points[0].cagr_per_vol is None
    assert window.best_weight_risk_adjusted is None
    assert window.outcome_risk_adjusted is None


def _drifting_pair(bleed: float) -> tuple[pd.Series, pd.Series]:
    """An equity path and the same path minus a steady ``bleed`` per day."""
    index = pd.bdate_range("2005-01-03", periods=600)
    rng = np.random.default_rng(11)
    equity = pd.Series(np.cumprod(1 + rng.normal(0.0004, 0.01, 600)), index=index)
    return equity * np.exp(-bleed * np.arange(600)), equity


def test_a_hedge_that_only_costs_never_beats_the_unhedged_end() -> None:
    """A program that is the equity minus a steady bleed adds no convexity, so
    growth must fall monotonically with the hedge ratio and the verdict fail."""
    program, equity = _drifting_pair(0.0002)
    window = _window(program, equity)
    cagrs = [p.cagr for p in window.points]
    assert cagrs == sorted(cagrs, reverse=True)
    assert window.best_weight == 0.0
    assert window.outcome == "fails"
    assert window.margin < 0


def test_the_verdict_is_against_the_better_end_whichever_end_that_is() -> None:
    """Swap the legs so the fully hedged end is the best: every interior mix
    beats w = 0, so a verdict that only checked the unhedged end would call
    this a win. It is not -- nothing beats w = 1."""
    bled, clean = _drifting_pair(0.0002)
    window = _window(clean, bled)
    assert window.best_weight == 1.0
    assert window.outcome == "fails"
    assert window.margin < 0
    assert window.points[5].cagr > window.points[0].cagr


@pytest.mark.parametrize(
    ("margin", "expected"),
    [
        (2 * MIN_MARGIN, "holds"),
        (MIN_MARGIN, "inconclusive"),
        (MIN_MARGIN / 2, "inconclusive"),
        (0.0, "inconclusive"),
        (-MIN_MARGIN / 2, "inconclusive"),
        (-MIN_MARGIN, "inconclusive"),
        (-2 * MIN_MARGIN, "fails"),
        (-0.01, "fails"),
    ],
)
def test_a_margin_within_one_basis_point_either_way_is_not_called(
    margin: float, expected: str
) -> None:
    """VXTH's full history beats both ends by 0.005pp/yr. "Holds" would oversell
    it and "no mix beats the ends" would be false, hence the third reading --
    and a sliver of a loss is no more a verdict than a sliver of a win."""
    assert MIN_MARGIN == 1e-4
    assert outcome(margin) == expected


def test_risk_and_drawdown_are_reported_in_annual_units_and_as_losses() -> None:
    """A program that runs up 1% a day to a peak, halves, then recovers part
    way: the drawdown is exactly -50%, and volatility is the daily stdev
    annualised by sqrt(252) -- not left daily, not by sqrt(365)."""
    up = np.full(30, 1.01)
    down = np.full(20, 0.5 ** (1 / 20))
    back = np.full(30, 1.005)
    steps = np.concatenate([[1.0], up, down, back])
    index = pd.bdate_range("2010-01-04", periods=len(steps))
    program = pd.Series(np.cumprod(steps), index=index)
    window = _window(program, _flat(index))
    pure = window.points[-1]
    assert pure.max_drawdown == pytest.approx(-0.5)
    assert pure.volatility == pytest.approx(np.std(steps[1:] - 1, ddof=1) * np.sqrt(252))
    assert pure.cagr_per_vol == pytest.approx(pure.cagr / pure.volatility)
    assert window.points[0].max_drawdown == 0.0


def test_the_risk_adjusted_verdict_is_scored_on_its_own_ratio() -> None:
    program, equity = _drifting_pair(0.0002)
    noisy = equity * (1 + 0.02 * np.sin(np.arange(len(equity))))
    window = _window(program, noisy)
    ratios = [p.cagr_per_vol for p in window.points]
    assert all(r is not None for r in ratios)
    best = max(range(len(ratios)), key=lambda i: ratios[i] or 0.0)
    assert window.best_weight_risk_adjusted == WEIGHTS[best]
    margin = max(ratios[1:-1]) - max(ratios[0], ratios[-1])  # type: ignore[type-var]
    assert window.outcome_risk_adjusted == outcome(margin)


def test_each_sensitivity_row_is_the_verdict_at_its_own_yield() -> None:
    """A gentler pump (x1.08) whose optimum moves with the yield: 30% hedged
    at 1.4%, 20% at 1.9% and 2.4%. A row that echoed the headline's best
    weight, or ignored its own yield, would disagree somewhere."""
    index = _monthly()
    program = pd.Series([1.08 if i % 2 else 1.0 for i in range(len(index))], index=index)
    window = _window(program, _flat(index), dividend_yield=0.019)
    assert [s.dividend_yield for s in window.sensitivity] == list(SENSITIVITY_YIELDS)
    assert [s.best_weight for s in window.sensitivity] == [0.3, 0.2, 0.2]
    for row in window.sensitivity:
        cagrs = [
            p.cagr
            for p in evaluate_window(
                program,
                _flat(index),
                start=index[0].date(),
                end=index[-1].date(),
                dividend_yield=row.dividend_yield,
            ).points
        ]
        assert row.margin == pytest.approx(max(cagrs[1:-1]) - max(cagrs[0], cagrs[-1]))
    assert len({round(r.margin, 9) for r in window.sensitivity}) == len(SENSITIVITY_YIELDS)


def test_each_sensitivity_row_reads_its_own_margin_not_the_headline() -> None:
    """A x1.06 pump has VXTH's real shape: holds at 1.4%, too close at 1.9%,
    fails at 2.4%. A row that borrowed the headline's outcome would show the
    middle reading three times."""
    index = _monthly()
    program = pd.Series([1.06 if i % 2 else 1.0 for i in range(len(index))], index=index)
    window = _window(program, _flat(index), dividend_yield=0.019)
    assert [s.outcome for s in window.sensitivity] == ["holds", "inconclusive", "fails"]
    assert all(s.outcome == outcome(s.margin) for s in window.sensitivity)


def test_only_the_hedged_programs_can_be_blended() -> None:
    """LTV is a quoted level, not a NAV, so blending it would be meaningless;
    an unknown ticker is a caller bug. Both are refused up front."""
    index = pd.bdate_range("2010-01-04", periods=100)
    level = pd.Series(np.linspace(100, 110, 100), index=index)
    for symbol in ("LTV", "FOO"):
        with pytest.raises(ValueError, match="not a hedged S&P 500 program"):
            run_hedge_overlay(level, {symbol: level}, as_of=index[-1].date())


def test_a_tie_goes_to_the_end_and_a_verdict_needs_an_interior() -> None:
    assert _verdict([1.0, 1.0, 1.0]) == (0, 0.0)
    # A tie with the fully hedged end goes to that end, not the interior row.
    assert _verdict([0.04, 0.06, 0.05, 0.06]) == (3, 0.0)
    assert _verdict([0.06, 0.06, 0.05, 0.04]) == (0, 0.0)
    with pytest.raises(ValueError, match="interior"):
        _verdict([1.0, 2.0])


def test_the_assumed_yield_is_credited_to_the_unhedged_leg_only() -> None:
    """The programs already reinvest real dividends; the assumed yield exists
    only to put SPX on the same total-return footing. Crediting it to the
    program too would double-count it and flatter every hedged blend."""
    index = _monthly()
    years = (index[-1] - index[0]).days / DAYS_PER_YEAR
    by_yield = {
        q: {
            p.weight: p.cagr
            for p in evaluate_window(
                _pump(index),
                _flat(index),
                start=index[0].date(),
                end=index[-1].date(),
                dividend_yield=q,
            ).points
        }
        for q in (0.0, 0.03)
    }
    assert by_yield[0.03][1.0] == pytest.approx(by_yield[0.0][1.0])
    accrued = float(total_return_levels(_flat(index), dividend_yield=0.03).iloc[-1])
    assert by_yield[0.03][0.0] == pytest.approx(accrued ** (1 / years) - 1)


def test_a_window_fully_covered_is_not_clipped() -> None:
    index = _monthly()
    assert not _window(_pump(index), _flat(index)).clipped


@pytest.mark.parametrize(
    ("program_start", "program_n", "spx_start", "spx_n"),
    [
        ("2006-04-01", N_MONTHS, "2004-01-01", 160),  # program starts late
        ("2004-01-01", N_MONTHS, "2004-01-01", 160),  # program stops early (stale feed)
        ("2004-01-01", 160, "2008-01-01", 120),  # SPX starts late
    ],
)
def test_any_shortfall_against_the_window_is_flagged_as_clipped(
    program_start: str, program_n: int, spx_start: str, spx_n: int
) -> None:
    window = run_overlay_window(
        _pump(_monthly(program_n, program_start)),
        _flat(_monthly(spx_n, spx_start)),
        key="cole",
        label="t",
        start=COLE_WINDOW[0],
        end=COLE_WINDOW[1],
        dividend_yield=0.0,
    )
    assert window.clipped


@pytest.mark.parametrize(
    ("start_short", "end_short", "clipped"),
    # 7 is the last tolerated day at each end, 8 the first that clips.
    [(5, 0, False), (0, 5, False), (7, 0, False), (0, 7, False), (8, 0, True), (0, 8, True)],
)
def test_clipping_tolerates_a_long_weekend_at_either_end_but_not_more(
    start_short: int, end_short: int, clipped: bool
) -> None:
    """CLIP_TOLERANCE_DAYS is 7: a holiday weekend short is the same window,
    more than a week short is a different test -- checked at each end alone."""
    start, end = dt.date(2010, 1, 4), dt.date(2014, 12, 31)
    index = pd.bdate_range(
        start + dt.timedelta(days=start_short), end - dt.timedelta(days=end_short)
    )
    level = pd.Series(np.linspace(100, 150, len(index)), index=index)
    window = run_overlay_window(
        level, level, key="t", label="t", start=start, end=end, dividend_yield=0.0
    )
    assert window.clipped is clipped
    # Both spans travel with the result, so the page can say how short it is.
    assert (window.requested_start, window.requested_end) == (start, end)
    assert window.start == index[0].date() and window.end == index[-1].date()


def test_a_stale_feed_clips_the_full_history_window() -> None:
    """The full window runs to as_of, so a program that stopped updating a
    month ago (the 2026-09-23 Cboe freeze shape) must say so."""
    index = pd.bdate_range("2004-01-02", "2017-12-29")
    level = pd.Series(np.linspace(100, 200, len(index)), index=index)
    stale = level[: pd.Timestamp("2017-11-30")]
    result = run_hedge_overlay(level, {"PPUT": stale}, as_of=dt.date(2017, 12, 29))
    full = next(w for w in result.programs[0].windows if w.key == "full")
    assert full.clipped


def test_a_store_bug_is_not_reported_as_an_absent_snapshot() -> None:
    class BrokenStore:
        def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
            raise KeyError("typo in the store")

    with pytest.raises(KeyError, match="typo in the store"):
        compute_hedge_overlay(BrokenStore(), as_of=dt.date(2020, 1, 2))  # type: ignore[arg-type]


def test_an_as_of_inside_the_letters_window_clips_it_and_reads_nothing_later() -> None:
    """A historical as_of must not pass a 5-year span off as the letter's 11,
    and must not read rows dated after itself even if a snapshot carries them."""
    index = pd.bdate_range("2004-01-02", "2017-12-29")
    rng = np.random.default_rng(1)
    level = pd.Series(np.cumprod(1 + rng.normal(0.0003, 0.01, len(index))), index=index)
    as_of = dt.date(2010, 6, 30)
    result = run_hedge_overlay(level, {"PPUT": level * 0.9}, as_of=as_of)
    cole, full = result.programs[0].windows
    assert cole.clipped
    assert cole.end <= as_of and full.end <= as_of


def test_too_short_an_overlap_raises_with_the_reason() -> None:
    index = _monthly(10)
    with pytest.raises(LookupError, match="common trading days"):
        evaluate_window(
            _pump(index),
            _flat(index),
            start=index[0].date(),
            end=index[-1].date(),
            dividend_yield=0.0,
        )


# ------------------------------------------------------------ lake orchestration


def _seed(store: DeltaLakeStore, ingest: dt.date, symbols: tuple[str, ...]) -> None:
    index = pd.bdate_range("2004-01-02", ingest, freq="B")
    rng = np.random.default_rng(3)
    level = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(index)))
    frames = [
        pd.DataFrame({"index_symbol": s, "trade_date": index, "close": level * (1 + 0.1 * k)})
        for k, s in enumerate(symbols)
    ]
    store.write_bronze("cboe_strategy", ingest, pd.concat(frames, ignore_index=True))


def test_compute_reads_every_program_over_both_windows(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2017, 6, 30)
    _seed(store, ingest, ("SPX", *OVERLAY_PROGRAMS))
    result = compute_hedge_overlay(store, as_of=ingest)
    assert [p.index_symbol for p in result.programs] == list(OVERLAY_PROGRAMS)
    assert result.weights == list(WEIGHTS)
    assert result.dividend_yield == DEFAULT_DIVIDEND_YIELD
    for program in result.programs:
        assert [w.key for w in program.windows] == ["cole", "full"]
        assert all(len(w.points) == len(WEIGHTS) for w in program.windows)


def test_compute_is_point_in_time(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2017, 6, 30)
    _seed(store, ingest, ("SPX", "PPUT"))
    with pytest.raises(LookupError, match="no Cboe strategy indices"):
        compute_hedge_overlay(store, as_of=ingest - dt.timedelta(days=1))


def test_compute_needs_spx(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2017, 6, 30)
    _seed(store, ingest, ("PPUT",))
    with pytest.raises(LookupError, match="no SPX rows"):
        compute_hedge_overlay(store, as_of=ingest)


def test_a_program_missing_from_the_snapshot_is_named_not_dropped(tmp_path: Path) -> None:
    """The ingested ticker set is configurable, so a program can be absent.
    The others still run, and the absent ones are listed with the reason."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2017, 6, 30)
    _seed(store, ingest, ("SPX", "PPUT"))
    result = compute_hedge_overlay(store, as_of=ingest)
    assert [p.index_symbol for p in result.programs] == ["PPUT"]
    assert result.missing == {
        "PPUT3M": "not in the Cboe snapshot",
        "VXTH": "not in the Cboe snapshot",
    }


def test_a_program_with_nothing_before_as_of_is_named_not_dropped() -> None:
    index = pd.bdate_range("2004-01-02", "2017-12-29")
    level = pd.Series(np.linspace(100, 200, len(index)), index=index)
    late = level[pd.Timestamp("2012-01-03") :]
    result = run_hedge_overlay(level, {"PPUT": level, "VXTH": late}, as_of=dt.date(2011, 12, 30))
    assert [p.index_symbol for p in result.programs] == ["PPUT"]
    assert result.missing["VXTH"] == "no rows on or before 2011-12-30"


def test_a_window_the_lake_cannot_cover_is_reported_not_fatal(tmp_path: Path) -> None:
    """History that starts after 2016 cannot answer the letter's window, but
    it can still answer the full-history one; the refusal is carried as a
    value rather than taking the whole page down."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2019, 6, 28)
    index = pd.bdate_range("2017-01-02", ingest)
    frames = [
        pd.DataFrame(
            {"index_symbol": s, "trade_date": index, "close": np.linspace(100, 120, len(index))}
        )
        for s in ("SPX", "PPUT")
    ]
    store.write_bronze("cboe_strategy", ingest, pd.concat(frames, ignore_index=True))
    (program,) = compute_hedge_overlay(store, as_of=ingest).programs
    assert [w.key for w in program.windows] == ["full"]
    assert "common trading days" in program.unavailable["cole"]


def test_a_bug_is_not_dressed_up_as_missing_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """``KeyError`` is a ``LookupError``. Only a real data shortfall may become
    an ``unavailable`` window; a bug has to fail the request."""
    from tail_lab.research.backtest import hedge_overlay

    def broken(*_: object, **__: object) -> pd.Series:
        raise KeyError("a programming bug")

    monkeypatch.setattr(hedge_overlay, "blend_nav", broken)
    index = pd.bdate_range("2004-01-02", "2017-12-29")
    level = pd.Series(np.linspace(100, 200, len(index)), index=index)
    with pytest.raises(KeyError, match="a programming bug"):
        run_hedge_overlay(level, {"PPUT": level}, as_of=dt.date(2017, 12, 29))
