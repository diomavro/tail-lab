from __future__ import annotations

import pandas as pd
import pytest

from tail_lab.research.metrics.vol_beta import vol_beta


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.RangeIndex(len(values)))


def test_uniform_linear_relationship_recovers_beta_exactly() -> None:
    """asset = beta * vol_changes for every observation (no noise) -- vol
    beta must recover ``beta`` exactly, since cov(k*x, x) / var(x) == k
    identically."""
    beta = -1.3
    vol_changes = _series([0.10, -0.05, 0.20, -0.10, 0.03, -0.02, 0.15])
    asset = beta * vol_changes
    assert vol_beta(asset, vol_changes) == pytest.approx(beta, rel=1e-12)


def test_differs_from_beta_against_the_benchmark_itself() -> None:
    """The whole point of the metric: two names can share a downside/tail
    beta against the benchmark yet react differently to a pure vol shock --
    a day VIX moves without the benchmark moving in lockstep. Constructed so
    vol beta (against VIX changes) and ordinary beta (against the benchmark's
    own returns, on the same asset series) provably disagree."""
    bench_returns = _series([-0.02, 0.01, -0.03, 0.02, -0.01, 0.015, -0.025])
    # VIX spikes hard on day 3 (index 2) independent of how large the
    # benchmark's own move was that day -- decouples the two factors.
    vol_changes = _series([0.05, -0.02, 0.40, -0.10, 0.03, -0.04, 0.06])
    asset = _series([-0.01, 0.005, -0.005, 0.01, -0.008, 0.007, -0.012])

    beta_vs_vol = vol_beta(asset, vol_changes)
    beta_vs_bench = asset.cov(bench_returns) / bench_returns.var()
    assert beta_vs_vol != pytest.approx(beta_vs_bench, rel=1e-6)


def test_mismatched_index_raises() -> None:
    asset = pd.Series([0.01, -0.02], index=[0, 1])
    vol_changes = pd.Series([0.05, -0.03], index=[1, 2])
    with pytest.raises(ValueError, match="same index"):
        vol_beta(asset, vol_changes)


def test_too_few_observations_raises() -> None:
    asset = _series([0.01])
    vol_changes = _series([0.05])
    with pytest.raises(ValueError, match="at least"):
        vol_beta(asset, vol_changes, min_observations=2)


def test_zero_vol_variance_raises() -> None:
    asset = _series([0.01, 0.02, -0.03])
    vol_changes = _series([-0.02, -0.02, -0.02])  # constant -> zero variance
    with pytest.raises(ValueError, match="zero variance"):
        vol_beta(asset, vol_changes)
