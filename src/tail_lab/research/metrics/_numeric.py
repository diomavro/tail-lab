"""Shared numpy kernels for the pure fragility metrics.

The metrics run thousands of times per walk-forward (one call per name, per
rebalance date, per metric), where pandas' per-call overhead -- boolean-mask
``Series`` slicing, ``cov`` / ``var`` / ``quantile`` dispatch -- dominated the
run (~10 ms for the six metrics on a 4-year window). These helpers do the same
arithmetic on plain arrays. ``var`` and ``cov`` use the formulas pandas'
``nanops`` uses (mean first, then the sum of squared deviations over
``n - ddof``), so results agree with the pandas versions to float rounding (measured: within
1.6e-11 relative over 30k random inputs). A column of exact zeros has exactly
zero variance; any other constant column has a rounding-noise variance (e.g.
3e-36) in BOTH this and the old pandas code, so a beta on it is meaningless
noise either way and the two noises differ. Callers never see such a column
from real returns except all-zero stretches, which are exact.

Inputs must be NaN-free: every caller inner-joins and ``dropna``s first, and
NaN is refused loudly rather than silently skipped as pandas would.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatArray = npt.NDArray[np.float64]


def paired_arrays(
    x: pd.Series, y: pd.Series, *, names: tuple[str, str], caller: str
) -> tuple[FloatArray, FloatArray]:
    """``x`` and ``y`` as float arrays, after checking they share one index
    (a silent positional pairing of misaligned series would be a correctness
    bug) and hold no NaN."""
    # Identity first: two columns of one frame share their Index object, and
    # `Index.equals` is the single most expensive step of a call on a window.
    if x.index is not y.index and not x.index.equals(y.index):
        raise ValueError(
            f"{names[0]} and {names[1]} must share the same index "
            f"(same trading calendar) -- align them before calling {caller}"
        )
    xa, ya = x.to_numpy(dtype=np.float64), y.to_numpy(dtype=np.float64)
    if np.isnan(xa).any() or np.isnan(ya).any():
        raise ValueError(
            f"{names[0]} and {names[1]} must not contain NaN -- dropna before {caller}"
        )
    return xa, ya


def var(x: FloatArray, ddof: int) -> float:
    """Variance with divisor ``n - ddof``; NaN when ``n <= ddof`` (as pandas)."""
    n = x.size
    if n <= ddof:
        return math.nan
    return float(((x - x.sum() / n) ** 2).sum() / (n - ddof))


def cov(x: FloatArray, y: FloatArray, ddof: int) -> float:
    """Covariance with divisor ``n - ddof``; NaN when ``n <= ddof``."""
    n = x.size
    if n <= ddof:
        return math.nan
    return float(((x - x.sum() / n) * (y - y.sum() / n)).sum() / (n - ddof))


def percentile(x: FloatArray, pct: float) -> float:
    """``np.percentile(x, pct)`` (linear interpolation) bit for bit, without
    its ~100 us of argument handling: numpy's own lerp on the two order
    statistics around the virtual index, found by partial sort."""
    n = x.size
    virtual = (n - 1) * (pct / 100.0)
    prev = min(max(math.floor(virtual), 0), n - 1)
    nxt = min(prev + 1, n - 1)
    gamma = virtual - prev
    part = np.partition(x, [prev, nxt])
    low, high = float(part[prev]), float(part[nxt])
    diff = high - low
    return high - diff * (1.0 - gamma) if gamma >= 0.5 else low + diff * gamma
