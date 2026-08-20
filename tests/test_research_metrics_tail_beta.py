"""Tests for tail beta (``research/metrics/tail_beta.py``)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.metrics.tail_beta import tail_beta


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=pd.RangeIndex(len(vals)))


def test_amplifying_the_benchmark_doubles_tail_beta() -> None:
    """tail_beta(2*bench, bench) == 2.0: amplifying the benchmark exactly
    doubles the beta, in the tail subset as everywhere else."""
    rng = np.random.default_rng(0)
    bench = _series([*rng.normal(0, 0.02, size=200), -0.15, -0.12, -0.10])
    fragile = _series([2.0 * b for b in bench])
    assert tail_beta(fragile, bench) == pytest.approx(2.0)


def test_fragile_name_scores_higher_than_defensive() -> None:
    """A name that amplifies the benchmark's worst days tail-betas higher
    (more fragile) than one that's unrelated and calm in the same tail."""
    rng = np.random.default_rng(1)
    bench = _series([*rng.normal(0, 0.02, size=250), -0.15, -0.12])
    fragile = _series([2.0 * b for b in bench])
    defensive = _series(list(rng.normal(0, 0.01, size=252)))
    assert tail_beta(fragile, bench) > tail_beta(defensive, bench)


def test_validation() -> None:
    x = _series([0.01, -0.02, 0.03, -0.01, 0.02, 0.01, -0.03, 0.02])
    with pytest.raises(ValueError, match="same"):
        tail_beta(x, x.iloc[:-1])
    with pytest.raises(ValueError, match="at least"):
        tail_beta(_series([0.1, 0.2]), _series([0.1, 0.2]))

    # 10 obs, tail_pct=10 -> only 1 day at/below the 10th percentile.
    flat_tail = _series([0.05, 0.04, 0.03, 0.02, 0.01, 0.0, -0.01, -0.02, -0.03, -0.04])
    with pytest.raises(ValueError, match="tail"):
        tail_beta(flat_tail, flat_tail, tail_pct=10.0, min_observations=8)

    # Wide tail_pct so the tail subset has >=2 days, but the benchmark is
    # flat within that subset -> zero tail variance.
    bench_flat_in_tail = _series([0.05, 0.04, 0.03, 0.02, -0.02, -0.02, -0.02, -0.02])
    asset = _series([0.05, 0.04, 0.03, 0.02, 0.01, -0.01, 0.02, -0.03])
    with pytest.raises(ValueError, match="zero variance"):
        tail_beta(asset, bench_flat_in_tail, tail_pct=50.0, min_observations=8)
