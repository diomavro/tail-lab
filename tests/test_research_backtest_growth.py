"""Pinned cases for ``research.backtest.growth.time_average_growth``
(``docs/END_STATE.md`` §4 Q8) -- see ``docs/STANDARDS.md``'s "numeric results
pinned against known cases": every value here is hand-computable independently
of the function under test."""

from __future__ import annotations

import datetime as dt

import pytest

from tail_lab.research.backtest.growth import time_average_growth


def test_time_average_growth_pinned_case() -> None:
    """wealth=100,000 held in a benchmark that rises 21% (100 -> 121) over a
    ~1.002-year window, with a hedge that bled 5,000 net over the same span:

        W_0 = 100,000
        W_1 = 100,000 * (121/100) - 5,000 = 116,000
        g   = ln(116,000 / 100,000) / 1.002053... = 0.14811586576352267
    """
    price_path = [(dt.date(2024, 1, 1), 100.0), (dt.date(2025, 1, 1), 121.0)]
    hedge_curve = [(dt.date(2024, 6, 1), -3000.0), (dt.date(2025, 1, 1), -5000.0)]
    g = time_average_growth(price_path, hedge_curve, wealth=100_000.0)
    assert g is not None
    assert g == pytest.approx(0.14811586576352267)


def test_time_average_growth_zero_hedge_matches_benchmark_only_growth() -> None:
    """An empty hedge curve is the same as a hedge that never moved cum_pnl
    off zero: the combined growth collapses to the benchmark's own geometric
    growth, ln(121/100) / years = 0.19022974411764837."""
    price_path = [(dt.date(2024, 1, 1), 100.0), (dt.date(2025, 1, 1), 121.0)]
    g = time_average_growth(price_path, [], wealth=100_000.0)
    assert g is not None
    assert g == pytest.approx(0.19022974411764837)


def test_time_average_growth_only_endpoints_of_price_path_matter() -> None:
    """Interior price points are ignored -- a time-average growth rate is
    defined by a trajectory's start and end, not its path (docstring)."""
    endpoints_only = [(dt.date(2024, 1, 1), 100.0), (dt.date(2025, 1, 1), 121.0)]
    with_interior = [
        (dt.date(2024, 1, 1), 100.0),
        (dt.date(2024, 6, 1), 500.0),  # wild interior swing, must not matter
        (dt.date(2025, 1, 1), 121.0),
    ]
    hedge = [(dt.date(2025, 1, 1), -5000.0)]
    assert time_average_growth(endpoints_only, hedge, wealth=100_000.0) == time_average_growth(
        with_interior, hedge, wealth=100_000.0
    )


def test_time_average_growth_undefined_cases_return_none() -> None:
    good_path = [(dt.date(2024, 1, 1), 100.0), (dt.date(2025, 1, 1), 121.0)]
    same_day = [(dt.date(2024, 1, 1), 100.0), (dt.date(2024, 1, 1), 121.0)]
    single_point = [(dt.date(2024, 1, 1), 100.0)]

    zero_start_price = [(dt.date(2024, 1, 1), 0.0), (dt.date(2025, 1, 1), 121.0)]

    assert time_average_growth(single_point, [], wealth=100_000.0) is None
    assert time_average_growth([], [], wealth=100_000.0) is None
    assert time_average_growth(good_path, [], wealth=0.0) is None
    assert time_average_growth(good_path, [], wealth=-1.0) is None
    assert time_average_growth(same_day, [], wealth=100_000.0) is None  # zero-year window
    assert time_average_growth(zero_start_price, [], wealth=100_000.0) is None
    # combined wealth wiped out (benchmark flat, hedge lost more than wealth)
    flat_path = [(dt.date(2024, 1, 1), 100.0), (dt.date(2025, 1, 1), 100.0)]
    ruin_hedge = [(dt.date(2025, 1, 1), -200_000.0)]
    assert time_average_growth(flat_path, ruin_hedge, wealth=100_000.0) is None
