"""Downside capture -- the fifth sensitivity/fragility-screen metric
(``docs/END_STATE.md`` §1.1; §4 research question 1: "which sensitivity
metric backtests best" as an OOM-put screen).

The downside capture ratio is a standard, intuitive fund-management
statistic: on days the benchmark is down, how much of that decline does the
asset "capture," expressed as a ratio of mean returns
(``mean(asset | benchmark < 0) / mean(benchmark | benchmark < 0)``). A ratio
above 1 means the name falls *more* than the market on down days -- fragile,
in exactly the sense an OOM-put screen cares about; below 1 means it
cushions the market's declines. Higher = more fragile, consistent with
downside beta's direction.

Pure numeric function, like ``downside_beta.py`` / ``co_kurtosis.py``: return
series in, float out. No lake/transforms dependency.
"""

from __future__ import annotations

import pandas as pd


def downside_capture(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    min_observations: int = 8,
) -> float:
    """Downside capture ratio of ``asset_returns`` against
    ``benchmark_returns``: ``mean(asset[bench < 0]) / mean(bench[bench < 0])``.

    ``asset_returns`` and ``benchmark_returns`` must share the same index
    (the same trading calendar) -- the caller aligns them before calling
    this function; a silent positional pairing of misaligned series would
    be a correctness bug, not a convenience, so this raises instead.

    Raises ``ValueError`` if the indices don't match, if fewer than
    ``min_observations`` paired observations are available in total, if
    fewer than 2 benchmark down-days (``benchmark_returns < 0``) are
    available, or if the benchmark's mean down-day return is zero (the
    ratio is undefined, not zero, when the denominator vanishes).
    """
    if not asset_returns.index.equals(benchmark_returns.index):
        raise ValueError(
            "asset_returns and benchmark_returns must share the same index "
            "(same trading calendar) -- align them before calling downside_capture"
        )

    n = len(asset_returns)
    if n < min_observations:
        raise ValueError(
            f"only {n} paired observations; need at least {min_observations} "
            "to estimate downside capture"
        )

    down_mask = benchmark_returns < 0
    n_down = int(down_mask.sum())
    if n_down < 2:
        raise ValueError(
            f"only {n_down} benchmark down-days; need at least 2 to estimate downside capture"
        )

    bench_down_mean = benchmark_returns[down_mask].mean()
    if bench_down_mean == 0:  # pragma: no cover -- unreachable with real float64 data:
        # every selected value is strictly negative, so their mean can only
        # be exactly 0.0 via subnormal-float underflow; kept as a defensive
        # guard against the same "denominator vanishes" failure mode as the
        # other metrics, not something a synthetic test can trigger normally.
        raise ValueError("benchmark down-day returns have zero mean; downside capture is undefined")

    asset_down_mean = asset_returns[down_mask].mean()
    return float(asset_down_mean / bench_down_mean)
