"""Tail beta -- the fourth sensitivity/fragility-screen metric
(``docs/END_STATE.md`` §1.1; §4 research question 1: "which sensitivity
metric backtests best" as an OOM-put screen).

Downside beta restricts ordinary beta to the benchmark's down-days; tail
beta sharpens that further, restricting it to only the benchmark's worst
``tail_pct`` percent of days -- its extreme left tail, not merely its
below-average days. This answers a narrower question than downside beta:
not "how much does this name move when the market is down" but "how much
does this name amplify the market's *worst* moves specifically" -- exactly
the regime an OOM put is bought to pay off in. Higher = more fragile,
consistent with downside beta's direction.

Pure numeric function, like ``downside_beta.py`` / ``co_kurtosis.py``: return
series in, float out. No lake/transforms dependency.
"""

from __future__ import annotations

import pandas as pd


def tail_beta(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    tail_pct: float = 10.0,
    min_observations: int = 8,
) -> float:
    """Beta of ``asset_returns`` on ``benchmark_returns``, restricted to the
    benchmark's worst ``tail_pct`` percent of days.

    The tail threshold is the ``tail_pct``-th percentile of
    ``benchmark_returns`` (e.g. ``tail_pct=10`` -> the 10th percentile); days
    with ``benchmark_returns <= threshold`` are the selected tail subset.
    Beta is then the population covariance/variance ratio
    (``ddof=0``) of asset vs. benchmark, computed only over that subset.

    ``asset_returns`` and ``benchmark_returns`` must share the same index
    (the same trading calendar) -- the caller aligns them before calling
    this function; a silent positional pairing of misaligned series would
    be a correctness bug, not a convenience, so this raises instead.

    Raises ``ValueError`` if the indices don't match, if fewer than
    ``min_observations`` paired observations are available in total, if
    fewer than 2 days fall in the selected tail subset (the mathematical
    floor for a variance estimate at all), or if the benchmark's tail-subset
    returns have zero variance (beta is undefined, not zero, when the
    denominator vanishes).
    """
    if not asset_returns.index.equals(benchmark_returns.index):
        raise ValueError(
            "asset_returns and benchmark_returns must share the same index "
            "(same trading calendar) -- align them before calling tail_beta"
        )

    n = len(asset_returns)
    if n < min_observations:
        raise ValueError(
            f"only {n} paired observations; need at least {min_observations} to estimate tail beta"
        )

    threshold = benchmark_returns.quantile(tail_pct / 100.0)
    tail_mask = benchmark_returns <= threshold
    n_tail = int(tail_mask.sum())
    if n_tail < 2:
        raise ValueError(
            f"only {n_tail} benchmark observations in the worst {tail_pct}% tail; "
            "need at least 2 to estimate tail beta"
        )

    asset_tail = asset_returns[tail_mask]
    bench_tail = benchmark_returns[tail_mask]

    bench_variance = bench_tail.var(ddof=0)
    if bench_variance == 0:
        raise ValueError("benchmark tail-subset returns have zero variance; tail beta is undefined")

    covariance = asset_tail.cov(bench_tail, ddof=0)
    return float(covariance / bench_variance)
