"""Hypothesis property tests for downside beta (STANDARDS.md "Property
tests for numeric code"). Complements the pinned known-answer cases in
``test_research_metrics_downside_beta.py`` with invariants that hold for
*any* valid input, not just the hand-picked ones.
"""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from tail_lab.research.metrics.downside_beta import downside_beta

# Realistic daily returns: bounded, and either exactly zero or at least 1bp in
# magnitude. Without the magnitude floor, Hypothesis generates denormal-tiny
# values (~1e-277) whose downside variance underflows, so cov/var stops being
# numerically 1.0 — a floating-point artifact of impossible inputs, not a bug
# in the metric (real returns are never that small).
_return = st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False).filter(
    lambda x: x == 0.0 or abs(x) >= 1e-4
)
_panel = st.lists(st.tuples(_return, _return), min_size=8, max_size=40)


def _has_estimable_downside(benchmark: list[float]) -> bool:
    downside = [b for b in benchmark if b < 0.0]
    return len(downside) >= 2 and len(set(downside)) >= 2


@given(
    panel=_panel,
    scale=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False).filter(lambda x: abs(x) > 1e-6),
)
def test_scaling_the_asset_series_scales_downside_beta_linearly(
    panel: list[tuple[float, float]], scale: float
) -> None:
    """downside_beta(c * asset, benchmark) == c * downside_beta(asset, benchmark)
    -- a direct consequence of covariance's linearity in its first argument,
    true for any valid asset/benchmark pair regardless of what relationship
    (if any) actually holds between them."""
    asset_vals, bench_vals = zip(*panel, strict=True)
    assume(_has_estimable_downside(list(bench_vals)))

    idx = pd.RangeIndex(len(panel))
    asset = pd.Series(asset_vals, index=idx)
    benchmark = pd.Series(bench_vals, index=idx)

    base = downside_beta(asset, benchmark)
    scaled = downside_beta(scale * asset, benchmark)

    assert scaled == pytest.approx(scale * base, rel=1e-8, abs=1e-8)


@given(panel=_panel)
def test_self_downside_beta_is_one(panel: list[tuple[float, float]]) -> None:
    """An asset regressed against itself always has downside beta exactly
    1.0 (cov(x, x) == var(x)) -- true for any series with an estimable
    downside subset, independent of its distribution."""
    _, bench_vals = zip(*panel, strict=True)
    assume(_has_estimable_downside(list(bench_vals)))

    idx = pd.RangeIndex(len(panel))
    benchmark = pd.Series(bench_vals, index=idx)

    assert downside_beta(benchmark, benchmark) == pytest.approx(1.0, rel=1e-9, abs=1e-9)
