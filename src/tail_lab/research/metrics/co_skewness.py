"""Co-skewness -- the second sensitivity-screen metric (README "the
platform computes **many**" sensitivity metrics; `docs/END_STATE.md` §1.1
names co-skewness explicitly as one of them, and §4 research question 1:
"which sensitivity metric backtests best").

Co-skewness (Harvey & Siddique 2000) measures how an asset's returns
co-move with the *squared* deviations of the benchmark -- i.e. whether the
asset swings hardest in the same periods the benchmark itself swings
hardest, regardless of the benchmark's own direction. A *negative*
co-skewness means the asset tends to fall precisely when the benchmark is
already whipping around -- crash-prone in exactly the sense a downside
put-buying screen cares about. A consumer that ranks "higher score = more
sensitive" (e.g. `research/backtest/ranking.py`) is responsible for
flipping the sign into that convention; this module returns the raw
(signed) statistic.

Pure numeric function, like `downside_beta.py`: return series in, float
out. No lake/transforms dependency.
"""

from __future__ import annotations

import math

import pandas as pd

from tail_lab.research.metrics._numeric import paired_arrays, var


def co_skewness(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    min_observations: int = 3,
) -> float:
    """Co-skewness of ``asset_returns`` with ``benchmark_returns``:
    ``E[(r_a - mean_a)(r_b - mean_b)^2] / (std(r_a) * var(r_b))``.

    All moments use the population convention (``n`` divisor / ``ddof=0``)
    so the numerator and denominator are on a consistent footing and the
    self-case ``co_skewness(x, x)`` reduces exactly to ``x``'s own (biased)
    skewness -- a useful, independently-checkable identity.

    ``asset_returns`` and ``benchmark_returns`` must share the same index
    (the same trading calendar) -- the caller aligns them before calling
    this function; a silent positional pairing of misaligned series would
    be a correctness bug, not a convenience, so this raises instead.

    Raises ``ValueError`` if the indices don't match, if fewer than
    ``min_observations`` paired observations are available, or if either
    series has zero variance (co-skewness is undefined, not zero, when a
    denominator term vanishes).
    """
    asset, bench = paired_arrays(
        asset_returns,
        benchmark_returns,
        names=("asset_returns", "benchmark_returns"),
        caller="co_skewness",
    )

    n = len(asset)
    if n < min_observations:
        raise ValueError(
            f"only {n} paired observations; need at least {min_observations} "
            "to estimate co-skewness"
        )

    asset_std = math.sqrt(var(asset, ddof=0))
    bench_var = var(bench, ddof=0)
    if asset_std == 0 or bench_var == 0:
        raise ValueError(
            "asset_returns or benchmark_returns has zero variance; co-skewness is undefined"
        )

    asset_demeaned = asset - asset.mean()
    bench_demeaned = bench - bench.mean()
    numerator = (asset_demeaned * bench_demeaned**2).mean()
    return float(numerator / (asset_std * bench_var))
