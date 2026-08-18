"""Downside beta -- the first sensitivity-screen metric (README "screen the
universe for the most **sensitive** assets"; `docs/END_STATE.md` §4
research question 1: "which sensitivity metric backtests best").

Ordinary market beta (cov(asset, benchmark) / var(benchmark)) is computed
over the *whole* sample, so a name that only moves with the market on the
way up looks just as "sensitive" as one that moves with it on the way
down. Downside beta (Bawa & Lindenberg 1977) restricts the same
covariance/variance ratio to the subset of days the benchmark itself fell
below a threshold -- exactly the regime an OOM put is bought to pay off in
-- so it screens for "how much does this asset move when things are
already going wrong," which ordinary beta cannot distinguish.

This module is a pure numeric function: it takes return series in, returns
a float out. No lake/transforms dependency -- the caller is responsible
for turning OHLCV gold-mart prices into aligned return series before
calling this (same "pure function, orchestration lives elsewhere" split as
``transforms/vix.py``).
"""

from __future__ import annotations

import pandas as pd


def downside_beta(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    threshold: float = 0.0,
    min_observations: int = 2,
) -> float:
    """Downside beta of ``asset_returns`` against ``benchmark_returns``.

    Restricts the standard covariance/variance beta ratio to the
    observations where ``benchmark_returns < threshold`` (default ``0.0``
    -- benchmark down-days).

    ``asset_returns`` and ``benchmark_returns`` must share the same index
    (the same trading calendar) -- the caller aligns them before calling
    this function; a silent positional pairing of misaligned series would
    be a correctness bug, not a convenience, so this raises instead.

    Raises ``ValueError`` if the indices don't match, if fewer than
    ``min_observations`` benchmark observations fall below ``threshold``
    (the mathematical floor is 2, needed for a variance estimate at all --
    that floor is not a recommendation for a trustworthy estimate), or if
    the downside-day benchmark returns have zero variance (beta is
    undefined, not zero, when the denominator vanishes).
    """
    if not asset_returns.index.equals(benchmark_returns.index):
        raise ValueError(
            "asset_returns and benchmark_returns must share the same index "
            "(same trading calendar) -- align them before calling downside_beta"
        )

    downside_mask = benchmark_returns < threshold
    n_downside = int(downside_mask.sum())
    if n_downside < min_observations:
        raise ValueError(
            f"only {n_downside} benchmark observations below threshold={threshold}; "
            f"need at least {min_observations} to estimate downside beta"
        )

    asset_down = asset_returns[downside_mask]
    bench_down = benchmark_returns[downside_mask]

    bench_variance = bench_down.var()  # sample variance, ddof=1 (pandas default)
    if bench_variance == 0:
        raise ValueError(
            "benchmark downside-day returns have zero variance; downside beta is undefined"
        )

    covariance = asset_down.cov(bench_down)  # ddof=1, matches bench_variance's convention
    return float(covariance / bench_variance)
