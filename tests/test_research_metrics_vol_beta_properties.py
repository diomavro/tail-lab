"""Hypothesis property tests for vol beta (STANDARDS.md "Property tests for
numeric code"). Complements the pinned known-answer cases in
``test_research_metrics_vol_beta.py`` with invariants that hold for *any*
valid input, not just the hand-picked ones.
"""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from tail_lab.research.metrics.vol_beta import vol_beta

# Realistic daily returns: bounded, and either exactly zero or at least 1bp in
# magnitude. Without the magnitude floor, Hypothesis generates denormal-tiny
# values whose variance underflows, so cov/var stops being numerically 1.0 --
# a floating-point artifact of impossible inputs, not a bug in the metric.
_return = st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False).filter(
    lambda x: x == 0.0 or abs(x) >= 1e-4
)
_panel = st.lists(st.tuples(_return, _return), min_size=2, max_size=40)


def _has_estimable_variance(vol_changes: list[float]) -> bool:
    return len(set(vol_changes)) >= 2


@given(
    panel=_panel,
    scale=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False).filter(lambda x: abs(x) > 1e-6),
)
def test_scaling_the_asset_series_scales_vol_beta_linearly(
    panel: list[tuple[float, float]], scale: float
) -> None:
    """vol_beta(c * asset, vol_changes) == c * vol_beta(asset, vol_changes)
    -- a direct consequence of covariance's linearity in its first argument,
    true for any valid asset/vol_changes pair regardless of what relationship
    (if any) actually holds between them."""
    asset_vals, vol_vals = zip(*panel, strict=True)
    assume(_has_estimable_variance(list(vol_vals)))

    idx = pd.RangeIndex(len(panel))
    asset = pd.Series(asset_vals, index=idx)
    vol_changes = pd.Series(vol_vals, index=idx)

    base = vol_beta(asset, vol_changes)
    scaled = vol_beta(scale * asset, vol_changes)

    assert scaled == pytest.approx(scale * base, rel=1e-8, abs=1e-8)


@given(panel=_panel)
def test_self_vol_beta_is_one(panel: list[tuple[float, float]]) -> None:
    """A series regressed against itself always has vol beta exactly 1.0
    (cov(x, x) == var(x)) -- true for any series with estimable variance,
    independent of its distribution."""
    _, vol_vals = zip(*panel, strict=True)
    assume(_has_estimable_variance(list(vol_vals)))

    idx = pd.RangeIndex(len(panel))
    vol_changes = pd.Series(vol_vals, index=idx)

    assert vol_beta(vol_changes, vol_changes) == pytest.approx(1.0, rel=1e-9, abs=1e-9)
