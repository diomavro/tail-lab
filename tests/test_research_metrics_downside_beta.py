from __future__ import annotations

import pandas as pd
import pytest

from tail_lab.research.metrics.downside_beta import downside_beta


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.RangeIndex(len(values)))


def test_uniform_linear_relationship_recovers_beta_exactly() -> None:
    """asset = beta * benchmark for every observation (no noise, mixed up-
    and down-days) -- downside beta must recover ``beta`` exactly,
    regardless of threshold, since cov(k*x, x) / var(x) == k identically."""
    beta = 1.7
    benchmark = _series([-0.05, -0.02, -0.01, 0.01, 0.03, 0.04, -0.03])
    asset = beta * benchmark
    assert downside_beta(asset, benchmark) == pytest.approx(beta, rel=1e-12)


def test_downside_only_relationship_differs_from_full_sample_beta() -> None:
    """The whole point of the metric: an asset that amplifies benchmark
    down-days more than up-days has a downside beta the full-sample
    covariance/variance ratio would understate. Constructed so the known
    answer (2.5) is analytically exact and provably different from what
    ordinary (whole-sample) beta on the same data would give."""
    down_bench = [-0.05, -0.03, -0.02]
    up_bench = [0.01, 0.02, 0.04]
    down_beta_true = 2.5
    up_beta_true = 0.5

    benchmark = _series(down_bench + up_bench)
    asset = _series([down_beta_true * b for b in down_bench] + [up_beta_true * b for b in up_bench])

    assert downside_beta(asset, benchmark) == pytest.approx(down_beta_true, rel=1e-9)

    # Sanity: the whole-sample beta on the same data is NOT 2.5 -- proves
    # the downside conditioning is doing real work, not a no-op.
    whole_sample_beta = asset.cov(benchmark) / benchmark.var()
    assert whole_sample_beta != pytest.approx(down_beta_true, rel=1e-9)


def test_custom_threshold_selects_a_different_subset() -> None:
    """Raising the threshold above 0 pulls in additional near-zero days,
    changing which observations the ratio is computed over -- pinned
    against a hand-computed value for a small, explicit panel."""
    benchmark = _series([-0.04, -0.01, 0.005, 0.02])
    asset = _series([-0.08, -0.03, 0.02, 0.01])

    # threshold=0.0 -> only the first two (both negative) observations.
    only_negative = downside_beta(asset, benchmark, threshold=0.0)
    expected_negative = (
        pd.Series([-0.08, -0.03]).cov(pd.Series([-0.04, -0.01])) / pd.Series([-0.04, -0.01]).var()
    )
    assert only_negative == pytest.approx(expected_negative, rel=1e-12)

    # threshold=0.01 -> pulls in the third observation (0.005 < 0.01) too.
    with_near_zero = downside_beta(asset, benchmark, threshold=0.01)
    expected_with_near_zero = (
        pd.Series([-0.08, -0.03, 0.02]).cov(pd.Series([-0.04, -0.01, 0.005]))
        / pd.Series([-0.04, -0.01, 0.005]).var()
    )
    assert with_near_zero == pytest.approx(expected_with_near_zero, rel=1e-12)
    assert with_near_zero != pytest.approx(only_negative, rel=1e-9)


def test_mismatched_index_raises() -> None:
    asset = pd.Series([0.01, -0.02], index=[0, 1])
    benchmark = pd.Series([0.01, -0.02], index=[1, 2])
    with pytest.raises(ValueError, match="same index"):
        downside_beta(asset, benchmark)


def test_too_few_downside_observations_raises() -> None:
    asset = _series([0.01, 0.02, 0.03])
    benchmark = _series([0.01, 0.02, -0.01])  # only 1 observation below 0.0
    with pytest.raises(ValueError, match="need at least"):
        downside_beta(asset, benchmark)


def test_zero_downside_variance_raises() -> None:
    asset = _series([0.01, 0.02, -0.03])
    benchmark = _series([0.01, -0.02, -0.02])  # both downside days identical -> var == 0
    with pytest.raises(ValueError, match="zero variance"):
        downside_beta(asset, benchmark)
