"""Hypothesis property tests for ``research.backtest.growth.time_average_growth``
(STANDARDS.md "Property tests for numeric code"). Complements the pinned
known-answer cases in ``test_research_backtest_growth.py`` with invariants that
hold for *any* valid input, not just the hand-picked ones.
"""

from __future__ import annotations

import datetime as dt
import math

from hypothesis import given
from hypothesis import strategies as st

from tail_lab.research.backtest.growth import time_average_growth

_positive_price = st.floats(min_value=1e-2, max_value=1e6, allow_nan=False, allow_infinity=False)
_wealth = st.floats(min_value=1.0, max_value=1e9, allow_nan=False, allow_infinity=False)
_days = st.integers(min_value=1, max_value=365 * 30)
# Bounded well away from wiping out `wealth` (see the ruin case in the pinned
# tests) so this strategy only ever generates the well-defined branch.
_hedge_pnl = st.floats(min_value=-1e2, max_value=1e6, allow_nan=False, allow_infinity=False)


def _path(start_price: float, end_price: float, days: int) -> list[tuple[dt.date, float]]:
    start = dt.date(2000, 1, 1)
    return [(start, start_price), (start + dt.timedelta(days=days), end_price)]


@given(start_price=_positive_price, end_price=_positive_price, days=_days, wealth=_wealth)
def test_zero_hedge_equals_benchmark_only_growth(
    start_price: float, end_price: float, days: int, wealth: float
) -> None:
    """With no hedge cash flow at all, the combined growth is exactly the
    benchmark's own geometric growth, ln(S_T / S_0) / T -- the hedge term
    must drop out cleanly, not merely approximately."""
    path = _path(start_price, end_price, days)
    g = time_average_growth(path, wealth=wealth)
    years = days / 365.25
    expected = math.log(end_price / start_price) / years
    assert g is not None
    assert math.isclose(g, expected, rel_tol=1e-9, abs_tol=1e-12)


@given(
    start_price=_positive_price,
    end_price=_positive_price,
    days=_days,
    wealth=_wealth,
    hedge_a=_hedge_pnl,
    hedge_b=_hedge_pnl,
)
def test_growth_is_monotone_increasing_in_hedge_pnl(
    start_price: float,
    end_price: float,
    days: int,
    wealth: float,
    hedge_a: float,
    hedge_b: float,
) -> None:
    """log is monotone increasing, so a strictly larger realized hedge P&L at
    the end date can never produce a strictly smaller combined growth rate,
    holding the benchmark path and wealth fixed."""
    path = _path(start_price, end_price, days)
    lo, hi = sorted((hedge_a, hedge_b))
    g_lo = time_average_growth(path, lo, wealth=wealth)
    g_hi = time_average_growth(path, hi, wealth=wealth)
    if g_lo is None or g_hi is None:
        return  # combined wealth was wiped out at this corner (extreme wealth/price draw)
    assert g_hi >= g_lo


@given(
    start_price=_positive_price,
    end_price=_positive_price,
    days=_days,
    wealth=_wealth,
    hedge=_hedge_pnl,
    scale=st.floats(min_value=1e-3, max_value=1e3, allow_nan=False, allow_infinity=False),
)
def test_growth_is_invariant_to_uniform_scaling_of_wealth_and_hedge(
    start_price: float, end_price: float, days: int, wealth: float, hedge: float, scale: float
) -> None:
    """Scaling both `wealth` and the hedge's realized cum_pnl by the same
    positive constant scales W_0 and W_1 by that constant too, so their ratio
    -- and therefore `g` -- is unchanged. This is what makes `g` a rate
    rather than a size-dependent figure."""
    path = _path(start_price, end_price, days)
    g = time_average_growth(path, hedge, wealth=wealth)
    g_scaled = time_average_growth(path, hedge * scale, wealth=wealth * scale)
    if g is None or g_scaled is None:
        return  # combined wealth was wiped out at this corner (extreme wealth/price draw)
    assert math.isclose(g, g_scaled, rel_tol=1e-6, abs_tol=1e-9)
