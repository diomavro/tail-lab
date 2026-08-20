"""Tests for co-kurtosis (``research/metrics/co_kurtosis.py``)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.metrics.co_kurtosis import co_kurtosis


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=pd.RangeIndex(len(vals)))


def test_self_case_is_biased_kurtosis() -> None:
    """co_kurtosis(x, x) == x's own biased (non-excess) kurtosis --
    E[(x-mean)^4] / std^4 (ddof=0). Recomputed independently from numpy."""
    x = _series([0.01, -0.03, 0.02, -0.05, 0.04, -0.01, 0.03, -0.02])
    arr = x.to_numpy()
    dev = arr - arr.mean()
    expected = (dev**4).mean() / (arr.std(ddof=0) ** 4)
    assert co_kurtosis(x, x) == pytest.approx(expected)


def test_fragile_name_scores_higher_than_defensive() -> None:
    """A name that plunges on the benchmark's big down-days co-kurts higher
    (more fragile) than one that's flat then."""
    rng = np.random.default_rng(0)
    bench = _series([*rng.normal(0, 0.02, size=250), -0.15, -0.12])  # two tail shocks at the end
    fragile = _series([2.0 * b for b in bench])  # amplifies the benchmark, incl. the tails
    defensive = _series(list(rng.normal(0, 0.01, size=252)))  # unrelated, calm
    assert co_kurtosis(fragile, bench) > co_kurtosis(defensive, bench)


def test_validation() -> None:
    x = _series([0.01, -0.02, 0.03, -0.01, 0.02])
    with pytest.raises(ValueError, match="same"):
        co_kurtosis(x, x.iloc[:-1])
    with pytest.raises(ValueError, match="at least"):
        co_kurtosis(_series([0.1, 0.2]), _series([0.1, 0.2]))
    flat = _series([0.0, 0.0, 0.0, 0.0, 0.0])
    with pytest.raises(ValueError, match="zero variance"):
        co_kurtosis(flat, x)
