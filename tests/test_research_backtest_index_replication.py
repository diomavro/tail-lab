"""Tests for the published-index replication harness.

The pinned cases hand-compute Black-Scholes-Merton with :func:`math.erf`
rather than calling the project's own pricer, so a bug in the pricer cannot
hide inside a green replication test.
"""

from __future__ import annotations

import datetime as dt
import math
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.index_replication import (
    DEFAULT_DIVIDEND_YIELD,
    PROGRAMS,
    ReplicationProgram,
    compute_index_replication,
    format_replication_report,
    roll_schedule,
    run_index_replication,
    third_friday,
)

RATE = 0.04
SIGMA = 0.20
MONEYNESS = 5.0


def _norm_cdf(x: float) -> float:
    """Standard normal CDF from the error function — deliberately not scipy,
    and deliberately not the code under test."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bsm_put(*, spot: float, strike: float, t: float, r: float, sigma: float, q: float) -> float:
    """Black-Scholes-Merton European put, written out longhand."""
    vol_t = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / vol_t
    d2 = d1 - vol_t
    return strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * math.exp(-q * t) * _norm_cdf(-d1)


def _price_path(n: int = 320, start: str = "2019-01-02") -> pd.Series:
    """A deterministic index path with a real drawdown in it, so some rolls
    expire worthless and others pay off — a path that only ever drifts up
    would leave the payoff branch untested."""
    dates = pd.bdate_range(start=start, periods=n)
    i = np.arange(n, dtype=float)
    level = 1000.0 * np.exp(0.0004 * i) * (1.0 - 0.22 * np.exp(-(((i - 150.0) / 28.0) ** 2)))
    return pd.Series(level, index=dates)


def _flat(index: pd.DatetimeIndex, value: float) -> pd.Series:
    return pd.Series(np.full(len(index), value, dtype=float), index=index)


def _expected_rolls(
    level: pd.Series,
    schedule: list[pd.Timestamp],
    *,
    q: float = DEFAULT_DIVIDEND_YIELD,
    sigma: float = SIGMA,
) -> list[float]:
    """Roll-by-roll replication returns, computed independently of the module."""
    out: list[float] = []
    for start, end in pairwise(schedule):
        spot = float(level.loc[start])
        spot_next = float(level.loc[end])
        t = (end - start).days / 365.25
        strike = spot * (1.0 - MONEYNESS / 100.0)
        premium = _bsm_put(spot=spot, strike=strike, t=t, r=RATE, sigma=sigma, q=q)
        payoff = max(strike - spot_next, 0.0)
        out.append((spot_next * math.exp(q * t) + payoff) / (spot + premium) - 1.0)
    return out


def _benchmark_from(
    level: pd.Series, schedule: list[pd.Timestamp], returns: list[float]
) -> pd.Series:
    """A published-index series whose roll-to-roll returns are exactly
    ``returns``, forward-filled onto every date of ``level``."""
    nav = [100.0]
    for r in returns:
        nav.append(nav[-1] * (1.0 + r))
    sparse = pd.Series(nav, index=pd.DatetimeIndex(schedule))
    return sparse.reindex(level.index).ffill().bfill()


# --------------------------------------------------------------- schedule


def test_third_friday_matches_known_expirations() -> None:
    # Three dates this project has already reasoned about elsewhere.
    assert third_friday(2010, 5) == dt.date(2010, 5, 21)
    assert third_friday(2020, 3) == dt.date(2020, 3, 20)
    assert third_friday(2026, 8) == dt.date(2026, 8, 21)


def test_third_friday_when_the_first_of_the_month_is_itself_a_friday() -> None:
    """The off-by-one trap: if the 1st is a Friday the third Friday is the
    15th, not the 22nd."""
    assert dt.date(2021, 1, 1).weekday() == 4
    assert third_friday(2021, 1) == dt.date(2021, 1, 15)


def test_monthly_schedule_has_one_roll_per_month() -> None:
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    months = {(d.year, d.month) for d in schedule}
    assert len(schedule) == len(months)
    assert schedule == sorted(schedule)


def test_quarterly_schedule_only_lands_in_quarter_end_months() -> None:
    level = _price_path(n=800)
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=4)
    assert {d.month for d in schedule} <= {3, 6, 9, 12}
    assert len(schedule) >= 8


def test_a_holiday_expiration_snaps_back_to_the_prior_trading_day() -> None:
    """Good Friday closes the market on an April third Friday regularly. The
    roll has to land on the last day that actually traded, not vanish."""
    level = _price_path()
    holiday = pd.Timestamp(third_friday(2019, 4))
    assert holiday in level.index
    open_days = pd.DatetimeIndex([d for d in level.index if d != holiday])

    schedule = roll_schedule(open_days, rolls_per_year=12)

    assert holiday not in schedule
    prior = open_days[open_days < holiday][-1]
    assert prior in schedule


def test_schedule_of_an_empty_calendar_is_empty() -> None:
    assert roll_schedule(pd.DatetimeIndex([]), rolls_per_year=12) == []


def test_schedule_rejects_an_unsupported_cadence() -> None:
    level = _price_path()
    with pytest.raises(ValueError, match=r"12 \(monthly\) or 4"):
        roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=6)


# ------------------------------------------------------- the analytic pin


def test_replicating_a_benchmark_built_by_the_same_rule_leaves_zero_residual() -> None:
    """The pin. Build the published index *as* the strategy, hand-computing
    every premium with :func:`_bsm_put`, then ask the harness to replicate it.
    The residual is analytically zero, so any drift in the engine's arithmetic
    — sizing, dividend accrual, tenor, chaining — shows up immediately."""
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    expected = _expected_rolls(level, schedule)
    benchmark = _benchmark_from(level, schedule, expected)

    result = run_index_replication(
        level,
        _flat(pd.DatetimeIndex(level.index), SIGMA),
        benchmark,
        program=PROGRAMS["PPUT"],
        schedule=schedule,
        rate=RATE,
        strike_increment=0.0,
        sensitivity_yields=(),
    )

    assert result.n_rolls == len(schedule) - 1
    for roll, want in zip(result.rolls, expected, strict=True):
        assert roll.replicated_return == pytest.approx(want, abs=1e-12)
        assert roll.residual == pytest.approx(0.0, abs=1e-12)
    assert result.annualized_drag == pytest.approx(0.0, abs=1e-10)
    assert result.tracking_error == pytest.approx(0.0, abs=1e-10)
    assert result.replicated_cagr == pytest.approx(result.actual_cagr, abs=1e-10)


def test_at_least_one_roll_pays_off_and_at_least_one_expires_worthless() -> None:
    """Guards the pin above: a path where every put expired worthless would
    let a broken payoff branch pass."""
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    result = run_index_replication(
        level,
        _flat(pd.DatetimeIndex(level.index), SIGMA),
        _benchmark_from(level, schedule, _expected_rolls(level, schedule)),
        program=PROGRAMS["PPUT"],
        schedule=schedule,
        rate=RATE,
        strike_increment=0.0,
        sensitivity_yields=(),
    )
    payoffs = [r.payoff for r in result.rolls]
    assert max(payoffs) > 0.0
    assert min(payoffs) == 0.0


def test_a_known_constant_wedge_is_reported_as_exactly_that_drag() -> None:
    """Second pin, independent of the pricer: hold the replication fixed and
    move the benchmark by a known constant per roll. The residual must be that
    constant, with zero dispersion around it."""
    wedge = 0.002
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    expected = _expected_rolls(level, schedule)
    benchmark = _benchmark_from(level, schedule, [r - wedge for r in expected])

    result = run_index_replication(
        level,
        _flat(pd.DatetimeIndex(level.index), SIGMA),
        benchmark,
        program=PROGRAMS["PPUT"],
        schedule=schedule,
        rate=RATE,
        strike_increment=0.0,
        sensitivity_yields=(),
    )

    assert all(r.residual == pytest.approx(wedge, abs=1e-12) for r in result.rolls)
    assert result.tracking_error == pytest.approx(0.0, abs=1e-12)
    assert result.correlation == pytest.approx(1.0, abs=1e-9)
    assert result.annualized_drag > 0.0


# ------------------------------------------------------------- behaviour


def test_a_richer_dividend_assumption_raises_the_reported_drag() -> None:
    """The dividend yield is an assumption, not a measurement, so the harness
    has to show what it is worth. A higher assumed yield credits the equity leg
    with more cash, so the replication looks better against a fixed benchmark."""
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    benchmark = _benchmark_from(level, schedule, _expected_rolls(level, schedule))
    common = dict(
        program=PROGRAMS["PPUT"],
        schedule=schedule,
        rate=RATE,
        strike_increment=0.0,
        sensitivity_yields=(0.010, 0.030),
    )
    sigma = _flat(pd.DatetimeIndex(level.index), SIGMA)

    result = run_index_replication(level, sigma, benchmark, dividend_yield=0.010, **common)

    lean, rich = result.dividend_sensitivity
    assert lean.dividend_yield == 0.010
    assert rich.dividend_yield == 0.030
    assert rich.annualized_drag > lean.annualized_drag
    # The headline run used the lean yield, so it must agree with that point.
    assert result.annualized_drag == pytest.approx(lean.annualized_drag, abs=1e-12)


def test_strikes_snap_to_the_listed_increment() -> None:
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    result = run_index_replication(
        level,
        _flat(pd.DatetimeIndex(level.index), SIGMA),
        _benchmark_from(level, schedule, _expected_rolls(level, schedule)),
        program=PROGRAMS["PPUT"],
        schedule=schedule,
        rate=RATE,
        strike_increment=5.0,
        sensitivity_yields=(),
    )
    for roll in result.rolls:
        assert roll.strike % 5.0 == pytest.approx(0.0, abs=1e-9)
        assert abs(roll.strike - roll.spot * 0.95) <= 2.5 + 1e-9


def test_regime_labels_come_from_the_roll_date_and_never_from_the_future() -> None:
    """A crisis that starts the day *after* a roll must not colour that roll —
    the per-regime table would otherwise be quietly look-ahead."""
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    first_roll = schedule[0]
    regimes = pd.Series(
        ["calm" if d <= first_roll else "crisis" for d in level.index],
        index=level.index,
    )

    result = run_index_replication(
        level,
        _flat(pd.DatetimeIndex(level.index), SIGMA),
        _benchmark_from(level, schedule, _expected_rolls(level, schedule)),
        program=PROGRAMS["PPUT"],
        schedule=schedule,
        rate=RATE,
        strike_increment=0.0,
        regimes=regimes,
        sensitivity_yields=(),
    )

    assert result.rolls[0].regime == "calm"
    assert result.rolls[1].regime == "crisis"
    assert {b.key for b in result.by_regime} == {"calm", "crisis"}
    assert sum(b.n_rolls for b in result.by_regime) == result.n_rolls


def test_unlabelled_rolls_are_bucketed_rather_than_dropped() -> None:
    """A regime timeline that starts after the first roll leaves early rolls
    with no label. They must still appear in the per-regime totals."""
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    late = level.index[level.index > schedule[1]]
    regimes = pd.Series(["calm"] * len(late), index=late)

    result = run_index_replication(
        level,
        _flat(pd.DatetimeIndex(level.index), SIGMA),
        _benchmark_from(level, schedule, _expected_rolls(level, schedule)),
        program=PROGRAMS["PPUT"],
        schedule=schedule,
        rate=RATE,
        strike_increment=0.0,
        regimes=regimes,
        sensitivity_yields=(),
    )

    assert result.rolls[0].regime is None
    assert "unlabelled" in {b.key for b in result.by_regime}
    assert sum(b.n_rolls for b in result.by_regime) == result.n_rolls


def test_yearly_buckets_partition_the_rolls_in_date_order() -> None:
    level = _price_path(n=800)
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    result = run_index_replication(
        level,
        _flat(pd.DatetimeIndex(level.index), SIGMA),
        _benchmark_from(level, schedule, _expected_rolls(level, schedule)),
        program=PROGRAMS["PPUT"],
        schedule=schedule,
        rate=RATE,
        strike_increment=0.0,
        sensitivity_yields=(),
    )
    keys = [b.key for b in result.by_year]
    assert keys == sorted(keys)
    assert sum(b.n_rolls for b in result.by_year) == result.n_rolls
    assert sum(b.years for b in result.by_year) == pytest.approx(result.years, abs=1e-9)


# ---------------------------------------------------------------- guards


def test_a_missing_date_is_an_error_not_a_silently_skipped_roll() -> None:
    """Skipping a roll would break the chained NAV without breaking the run —
    the exact failure mode this project keeps finding. It must be loud."""
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    benchmark = _benchmark_from(level, schedule, _expected_rolls(level, schedule))
    holed = level.drop(index=[schedule[2]])

    with pytest.raises(ValueError, match="index series has no value"):
        run_index_replication(
            holed,
            _flat(pd.DatetimeIndex(level.index), SIGMA),
            benchmark,
            program=PROGRAMS["PPUT"],
            schedule=schedule,
            rate=RATE,
            sensitivity_yields=(),
        )


def test_a_non_positive_volatility_is_rejected() -> None:
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    sigma = _flat(pd.DatetimeIndex(level.index), SIGMA)
    sigma.loc[schedule[0]] = 0.0

    with pytest.raises(ValueError, match="not a usable volatility"):
        run_index_replication(
            level,
            sigma,
            _benchmark_from(level, schedule, _expected_rolls(level, schedule)),
            program=PROGRAMS["PPUT"],
            schedule=schedule,
            rate=RATE,
            sensitivity_yields=(),
        )


def test_a_schedule_too_short_for_one_roll_raises() -> None:
    level = _price_path()
    with pytest.raises(LookupError, match="at least two roll dates"):
        run_index_replication(
            level,
            _flat(pd.DatetimeIndex(level.index), SIGMA),
            level,
            program=PROGRAMS["PPUT"],
            schedule=[pd.Timestamp(level.index[0])],
            rate=RATE,
        )


# --------------------------------------------------- lake orchestration


def _seed_lake(store: DeltaLakeStore, *, ingest: dt.date, level: pd.Series) -> None:
    strategy = pd.concat(
        [
            pd.DataFrame(
                {
                    "index_symbol": symbol,
                    "trade_date": level.index,
                    "close": level.to_numpy() * scale,
                }
            )
            for symbol, scale in (("SPX", 1.0), ("PPUT", 0.4), ("PPUT3M", 0.4))
        ],
        ignore_index=True,
    )
    store.write_bronze("cboe_strategy", ingest, strategy)
    store.write_bronze(
        "vix",
        ingest,
        pd.DataFrame({"date": level.index, "close": np.full(len(level), SIGMA * 100.0)}),
    )


def test_compute_index_replication_reads_the_lake_and_reports_a_residual(
    tmp_path: Path,
) -> None:
    level = _price_path()
    store = DeltaLakeStore(tmp_path)
    ingest = level.index[-1].date()
    _seed_lake(store, ingest=ingest, level=level)

    result = compute_index_replication(store, index_symbol="PPUT", as_of=ingest)

    assert result.index_symbol == "PPUT"
    assert result.n_rolls >= 10
    assert result.dividend_yield == DEFAULT_DIVIDEND_YIELD
    assert len(result.dividend_sensitivity) == 3
    # The benchmark is a fixed multiple of SPX, so it carries no put at all —
    # the replication must differ from it, and visibly.
    assert result.correlation is not None
    assert abs(result.annualized_drag) > 0.0
    assert all(r.regime is not None for r in result.rolls)


def test_compute_index_replication_is_point_in_time(tmp_path: Path) -> None:
    """An as-of date before the only snapshot must find nothing, not the
    snapshot."""
    level = _price_path()
    store = DeltaLakeStore(tmp_path)
    ingest = level.index[-1].date()
    _seed_lake(store, ingest=ingest, level=level)

    with pytest.raises(LookupError):
        compute_index_replication(store, index_symbol="PPUT", as_of=ingest - dt.timedelta(days=1))


def test_compute_index_replication_rejects_a_program_it_has_no_rule_for(
    tmp_path: Path,
) -> None:
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(KeyError, match="no replication rule"):
        compute_index_replication(store, index_symbol="CLL", as_of=dt.date(2026, 1, 2))


def test_compute_index_replication_needs_the_underlying_in_the_snapshot(
    tmp_path: Path,
) -> None:
    level = _price_path()
    store = DeltaLakeStore(tmp_path)
    ingest = level.index[-1].date()
    store.write_bronze(
        "cboe_strategy",
        ingest,
        pd.DataFrame(
            {"index_symbol": "PPUT", "trade_date": level.index, "close": level.to_numpy()}
        ),
    )
    store.write_bronze(
        "vix",
        ingest,
        pd.DataFrame({"date": level.index, "close": np.full(len(level), SIGMA * 100.0)}),
    )

    with pytest.raises(LookupError, match="no SPX rows"):
        compute_index_replication(store, index_symbol="PPUT", as_of=ingest)


def test_compute_index_replication_needs_enough_overlap(tmp_path: Path) -> None:
    """SPX, VIX and the published index rarely start on the same day. Too
    little common history has to fail with the reason, not an IndexError."""
    level = _price_path(n=20)
    store = DeltaLakeStore(tmp_path)
    ingest = level.index[-1].date()
    _seed_lake(store, ingest=ingest, level=level)

    with pytest.raises(LookupError, match="roll date"):
        compute_index_replication(store, index_symbol="PPUT", as_of=ingest)


def test_every_catalogued_program_is_self_consistent() -> None:
    for symbol, program in PROGRAMS.items():
        assert isinstance(program, ReplicationProgram)
        assert program.index_symbol == symbol
        assert program.rolls_per_year in (4, 12)
        assert 0.0 < program.moneyness_pct < 100.0


def test_the_report_never_quotes_a_residual_without_its_sensitivity() -> None:
    """A drag figure alone reads as a measurement; it is partly an assumption.
    The renderer must carry the yield sensitivity next to the headline."""
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)
    result = run_index_replication(
        level,
        _flat(pd.DatetimeIndex(level.index), SIGMA),
        _benchmark_from(level, schedule, _expected_rolls(level, schedule)),
        program=PROGRAMS["PPUT"],
        schedule=schedule,
        rate=RATE,
        strike_increment=0.0,
        sensitivity_yields=(0.014, 0.024),
    )

    report = format_replication_report(result)

    assert "RESIDUAL" in report
    assert "dividend sensitivity" in report
    assert "q=0.014" in report and "q=0.024" in report
    assert "by regime:" in report and "by year:" in report


def test_the_report_says_n_a_rather_than_crashing_on_a_single_roll() -> None:
    """Correlation is undefined for one roll. The report still has to render —
    a formatter that raises would take the whole `make residual` run down."""
    level = _price_path()
    schedule = roll_schedule(pd.DatetimeIndex(level.index), rolls_per_year=12)[:2]
    result = run_index_replication(
        level,
        _flat(pd.DatetimeIndex(level.index), SIGMA),
        _benchmark_from(level, schedule, _expected_rolls(level, schedule)),
        program=PROGRAMS["PPUT"],
        schedule=schedule,
        rate=RATE,
        strike_increment=0.0,
        sensitivity_yields=(),
    )

    assert result.correlation is None
    assert result.tracking_error == 0.0
    assert "correlation   n/a" in format_replication_report(result)
