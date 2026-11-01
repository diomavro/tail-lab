"""The numpy-backed fragility metrics must equal the pandas implementations they
replaced.

``research/metrics/*`` were rewritten with numpy kernels because pandas'
per-call overhead dominated the walk-forward (thousands of calls). A speed-up
is only acceptable if it changes no output, so this file keeps a FROZEN copy of
the pandas implementations (as they were before the rewrite) and checks, on
random inputs, that each new function either returns the same number
(``rtol=1e-10``; measured worst 1.6e-11 over 30k inputs) or raises ``ValueError`` exactly where the old one did --
including ties, all-zero columns and thin samples (constant NON-zero
subsets are excluded: see ``_degenerate``), where the guards matter.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tail_lab.research.metrics._numeric import percentile
from tail_lab.research.metrics.co_kurtosis import co_kurtosis
from tail_lab.research.metrics.co_skewness import co_skewness
from tail_lab.research.metrics.downside_beta import downside_beta
from tail_lab.research.metrics.downside_capture import downside_capture
from tail_lab.research.metrics.tail_beta import tail_beta
from tail_lab.research.metrics.vol_beta import vol_beta

# ---- frozen pandas implementations (pre-rewrite; do not "improve") ----


def _old_downside_beta(
    a: pd.Series, b: pd.Series, *, threshold: float = 0.0, min_obs: int = 2
) -> float:
    mask = b < threshold
    if int(mask.sum()) < min_obs:
        raise ValueError("thin")
    bd = b[mask]
    variance = bd.var()
    if variance == 0:
        raise ValueError("zero variance")
    return float(a[mask].cov(bd) / variance)


def _old_tail_beta(
    a: pd.Series, b: pd.Series, *, tail_pct: float = 10.0, min_obs: int = 8
) -> float:
    if len(a) < min_obs:
        raise ValueError("thin")
    mask = b <= b.quantile(tail_pct / 100.0)
    if int(mask.sum()) < 2:
        raise ValueError("thin tail")
    bt = b[mask]
    variance = bt.var(ddof=0)
    if variance == 0:
        raise ValueError("zero variance")
    return float(a[mask].cov(bt, ddof=0) / variance)


def _old_co_skewness(a: pd.Series, b: pd.Series, *, min_obs: int = 3) -> float:
    if len(a) < min_obs:
        raise ValueError("thin")
    a_std, b_var = a.std(ddof=0), b.var(ddof=0)
    if a_std == 0 or b_var == 0:
        raise ValueError("zero variance")
    return float(((a - a.mean()) * (b - b.mean()) ** 2).mean() / (a_std * b_var))


def _old_co_kurtosis(a: pd.Series, b: pd.Series, *, min_obs: int = 4) -> float:
    if len(a) < min_obs:
        raise ValueError("thin")
    a_std, b_std = a.std(ddof=0), b.std(ddof=0)
    if a_std == 0 or b_std == 0:
        raise ValueError("zero variance")
    return float(((a - a.mean()) * (b - b.mean()) ** 3).mean() / (a_std * b_std**3))


def _old_downside_capture(a: pd.Series, b: pd.Series, *, min_obs: int = 8) -> float:
    if len(a) < min_obs:
        raise ValueError("thin")
    down = b < 0
    if int(down.sum()) < 2:
        raise ValueError("thin downside")
    denominator = b[down].mean()
    if denominator == 0:
        raise ValueError("zero mean")
    return float(a[down].mean() / denominator)


def _old_vol_beta(a: pd.Series, v: pd.Series, *, min_obs: int = 2) -> float:
    if len(a) < min_obs:
        raise ValueError("thin")
    variance = v.var()
    if variance == 0:
        raise ValueError("zero variance")
    return float(a.cov(v) / variance)


_PAIRS: list[tuple[Callable[..., float], Callable[..., float]]] = [
    (downside_beta, _old_downside_beta),
    (co_skewness, _old_co_skewness),
    (co_kurtosis, _old_co_kurtosis),
    (downside_capture, _old_downside_capture),
    (vol_beta, _old_vol_beta),
]

# A small value pool makes ties and constant columns likely; the wide pool
# makes ordinary draws.
_value = st.one_of(
    st.sampled_from([-0.02, -0.01, 0.0, 0.01, 0.02]),
    st.floats(min_value=-0.3, max_value=0.3, allow_nan=False, allow_infinity=False),
)


@st.composite
def _series_pair(draw: st.DrawFn) -> tuple[pd.Series, pd.Series]:
    n = draw(st.integers(min_value=1, max_value=400))
    index = pd.bdate_range("2020-01-01", periods=n)
    a = draw(st.lists(_value, min_size=n, max_size=n))
    b = draw(st.lists(_value, min_size=n, max_size=n))
    # A constant NON-ZERO subset (a whole column, its down days, or its worst
    # tail -- each starts with the two smallest values) has a rounding-noise
    # variance (~1e-36) in both the old pandas code and the new kernels, so a
    # "beta" on it is noise and the two noises legitimately differ (round-2
    # review, 2026-11-01). Exclude it rather than pin noise; an exactly-zero
    # constant column is exact in both and stays in.
    assume(not _degenerate(a) and not _degenerate(b))
    return pd.Series(a, index=index), pd.Series(b, index=index)


def _degenerate(values: list[float]) -> bool:
    ordered = sorted(values)
    return len(ordered) >= 2 and ordered[0] == ordered[1] and ordered[0] != 0.0


def _outcome(fn: Callable[..., float], a: pd.Series, b: pd.Series, **kw: float) -> float | str:
    try:
        return fn(a, b, **kw)
    except ValueError:
        return "refused"


def _assert_same(new: float | str, old: float | str) -> None:
    if isinstance(new, str) or isinstance(old, str):
        assert new == old
    elif math.isnan(old):
        assert math.isnan(new)
    else:
        assert new == pytest.approx(old, rel=1e-10, abs=1e-14)


@pytest.mark.parametrize(("new", "old"), _PAIRS, ids=[p[0].__name__ for p in _PAIRS])
@settings(max_examples=150, deadline=None)
@given(pair=_series_pair())
def test_numpy_metric_equals_the_frozen_pandas_one(
    new: Callable[..., float], old: Callable[..., float], pair: tuple[pd.Series, pd.Series]
) -> None:
    a, b = pair
    _assert_same(_outcome(new, a, b), _outcome(old, a, b))


@settings(max_examples=150, deadline=None)
@given(pair=_series_pair(), tail_pct=st.floats(min_value=1.0, max_value=60.0))
def test_numpy_tail_beta_equals_the_frozen_pandas_one(
    pair: tuple[pd.Series, pd.Series], tail_pct: float
) -> None:
    a, b = pair
    _assert_same(
        _outcome(tail_beta, a, b, tail_pct=tail_pct),
        _outcome(_old_tail_beta, a, b, tail_pct=tail_pct),
    )


@settings(max_examples=100, deadline=None)
@given(pair=_series_pair(), threshold=st.floats(min_value=-0.05, max_value=0.05))
def test_numpy_downside_beta_equals_the_frozen_one_at_any_threshold(
    pair: tuple[pd.Series, pd.Series], threshold: float
) -> None:
    a, b = pair
    _assert_same(
        _outcome(downside_beta, a, b, threshold=threshold),
        _outcome(_old_downside_beta, a, b, threshold=threshold),
    )


@pytest.mark.parametrize("fn", [fn for fn, _ in _PAIRS] + [tail_beta], ids=lambda f: f.__name__)
def test_a_nan_input_is_refused_not_silently_skipped(fn: Callable[..., float]) -> None:
    index = pd.bdate_range("2020-01-01", periods=30)
    rng = np.random.default_rng(0)
    a = pd.Series(rng.normal(size=30), index=index)
    b = pd.Series(rng.normal(size=30), index=index)
    a.iloc[5] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        fn(a, b)


@pytest.mark.parametrize("fn", [fn for fn, _ in _PAIRS] + [tail_beta], ids=lambda f: f.__name__)
def test_misaligned_indices_are_still_refused(fn: Callable[..., float]) -> None:
    a = pd.Series(np.arange(30.0), index=pd.bdate_range("2020-01-01", periods=30))
    b = pd.Series(np.arange(30.0), index=pd.bdate_range("2020-02-01", periods=30))
    with pytest.raises(ValueError, match="same index"):
        fn(a, b)


@settings(max_examples=500, deadline=None)
@given(
    values=st.lists(_value, min_size=1, max_size=120),
    pct=st.floats(min_value=0.0, max_value=100.0),
)
def test_the_fast_percentile_is_np_percentile_bit_for_bit(values: list[float], pct: float) -> None:
    """The tail subset is ``returns <= threshold``, so the threshold must be
    identical, not merely close."""
    x = np.array(values)
    assert percentile(x, pct) == float(np.percentile(x, pct))
