"""Hypothesis property tests for co-skewness (STANDARDS.md "Property tests
for numeric code"). Complements the pinned known-answer cases in
``test_research_metrics_co_skewness.py`` with invariants that hold for
*any* valid input, not just the hand-picked ones.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from tail_lab.research.metrics.co_skewness import co_skewness

# Realistic daily returns: bounded, and either exactly zero or at least 1bp
# in magnitude, mirroring downside_beta's property-test strategy -- avoids
# denormal-tiny Hypothesis floats whose variance underflows.
_return = st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False).filter(
    lambda x: x == 0.0 or abs(x) >= 1e-4
)
_panel = st.lists(st.tuples(_return, _return), min_size=5, max_size=40)


def _has_estimable_variance(values: list[float]) -> bool:
    return len(set(values)) >= 2


@given(
    panel=_panel,
    scale=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False).filter(lambda x: abs(x) > 1e-3),
)
def test_scaling_the_asset_series_only_flips_the_sign(
    panel: list[tuple[float, float]], scale: float
) -> None:
    """co_skewness(c * asset, benchmark) == sign(c) * co_skewness(asset,
    benchmark) for any c != 0 -- the numerator is linear in the asset
    series but std(asset) in the denominator only ever scales by |c|."""
    asset_vals, bench_vals = zip(*panel, strict=True)
    assume(_has_estimable_variance(list(asset_vals)))
    assume(_has_estimable_variance(list(bench_vals)))

    idx = pd.RangeIndex(len(panel))
    asset = pd.Series(asset_vals, index=idx)
    benchmark = pd.Series(bench_vals, index=idx)

    base = co_skewness(asset, benchmark)
    scaled = co_skewness(scale * asset, benchmark)

    assert scaled == pytest.approx(np.sign(scale) * base, rel=1e-6, abs=1e-9)


@given(panel=_panel)
def test_self_co_skewness_matches_population_skewness_formula(
    panel: list[tuple[float, float]],
) -> None:
    """co_skewness(x, x) always equals x's own population skewness
    (mean(dev**3) / std(ddof=0)**3), computed independently of the
    production module for any series with estimable variance."""
    _, bench_vals = zip(*panel, strict=True)
    assume(_has_estimable_variance(list(bench_vals)))

    idx = pd.RangeIndex(len(panel))
    x = pd.Series(bench_vals, index=idx)

    arr = np.array(bench_vals, dtype=float)
    dev = arr - arr.mean()
    expected = float(np.mean(dev**3) / arr.std(ddof=0) ** 3)

    assert co_skewness(x, x) == pytest.approx(expected, rel=1e-6, abs=1e-9)
