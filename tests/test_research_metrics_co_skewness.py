from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.metrics.co_skewness import co_skewness


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.RangeIndex(len(values)))


def test_self_co_skewness_equals_hand_computed_population_skewness() -> None:
    """co_skewness(x, x) must equal x's own (biased/population) skewness --
    mean(dev**3) / std(ddof=0)**3 -- a value hand-computable independently
    of the production formula: x = [1, 2, 3, 4, 10], mean=4,
    deviations=[-3,-2,-1,0,6], var=50/5=10, M3=180/5=36,
    skew = 36 / 10**1.5 = 1.1384199576606164."""
    x = _series([1.0, 2.0, 3.0, 4.0, 10.0])
    assert co_skewness(x, x) == pytest.approx(1.1384199576606164, rel=1e-12)


def test_self_co_skewness_is_scale_and_shift_invariant() -> None:
    """Skewness is dimensionless: scaling to realistic daily-return
    magnitudes and shifting by a constant must not change the answer."""
    x = _series([1.0, 2.0, 3.0, 4.0, 10.0])
    scaled_shifted = _series([v * 0.01 + 0.5 for v in [1.0, 2.0, 3.0, 4.0, 10.0]])
    assert co_skewness(scaled_shifted, scaled_shifted) == pytest.approx(co_skewness(x, x), rel=1e-9)


def test_scaling_asset_by_a_negative_constant_flips_the_sign() -> None:
    """asset = c * benchmark: the numerator scales by c (linear in the
    first argument) but the denominator's std(asset) scales by |c|, so
    co_skewness(c*b, b) == sign(c) * co_skewness(b, b) exactly. Pinned
    against the same hand-computable benchmark as the self-skewness case
    above, cross-checked with an independent numpy computation (not the
    production module's code path)."""
    benchmark = _series([1.0, 2.0, 3.0, 4.0, 10.0])
    c = -2.5
    asset = c * benchmark

    b = np.array([1.0, 2.0, 3.0, 4.0, 10.0])
    b_dev = b - b.mean()
    a = c * b
    a_dev = a - a.mean()
    expected = float(np.mean(a_dev * b_dev**2) / (a.std(ddof=0) * np.mean(b_dev**2)))

    result = co_skewness(asset, benchmark)
    assert result == pytest.approx(expected, rel=1e-12)
    assert result == pytest.approx(-co_skewness(benchmark, benchmark), rel=1e-9)


def test_mismatched_index_raises() -> None:
    asset = pd.Series([0.01, -0.02, 0.03], index=[0, 1, 2])
    benchmark = pd.Series([0.01, -0.02, 0.03], index=[1, 2, 3])
    with pytest.raises(ValueError, match="same index"):
        co_skewness(asset, benchmark)


def test_too_few_observations_raises() -> None:
    asset = _series([0.01, 0.02])
    benchmark = _series([0.01, -0.01])
    with pytest.raises(ValueError, match="need at least"):
        co_skewness(asset, benchmark)


def test_zero_variance_asset_raises() -> None:
    asset = _series([0.01, 0.01, 0.01])
    benchmark = _series([0.01, -0.02, 0.03])
    with pytest.raises(ValueError, match="zero variance"):
        co_skewness(asset, benchmark)


def test_zero_variance_benchmark_raises() -> None:
    asset = _series([0.01, -0.02, 0.03])
    benchmark = _series([0.01, 0.01, 0.01])
    with pytest.raises(ValueError, match="zero variance"):
        co_skewness(asset, benchmark)
