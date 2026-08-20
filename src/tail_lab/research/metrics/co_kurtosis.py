"""Co-kurtosis -- the third sensitivity/fragility-screen metric
(``docs/END_STATE.md`` §1.1 names co-kurtosis; §4 research question 1:
"which sensitivity metric backtests best" as an OOM-put screen).

Where downside beta measures *how far* an asset falls with the market and
co-skewness measures crash-direction co-movement, co-kurtosis
(Harvey & Siddique 2000) measures **tail amplification** -- how strongly the
asset moves in the same periods the benchmark has its most *extreme* moves.
The benchmark deviation enters cubed, which preserves sign, so a big negative
benchmark shock (whose cube is large and negative) paired with the asset also
falling yields a large *positive* co-kurtosis: exactly the "this name detonates
in a market tail event" fragility an OOM-put screen wants. Higher = more
fragile, consistent with downside beta's direction.

Pure numeric function, like ``downside_beta.py`` / ``co_skewness.py``: return
series in, float out. No lake/transforms dependency.
"""

from __future__ import annotations

import pandas as pd


def co_kurtosis(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    min_observations: int = 4,
) -> float:
    """Standardized co-kurtosis of ``asset_returns`` with ``benchmark_returns``:
    ``E[(r_a - mean_a)(r_b - mean_b)^3] / (std(r_a) * std(r_b)^3)``.

    Population moments (``ddof=0``) throughout, so the self-case
    ``co_kurtosis(x, x)`` reduces exactly to ``x``'s own (biased, non-excess)
    kurtosis -- an independently-checkable identity.

    ``asset_returns`` and ``benchmark_returns`` must share the same index; a
    silent positional pairing of misaligned series would be a correctness bug,
    so this raises instead.

    Raises ``ValueError`` if the indices don't match, if fewer than
    ``min_observations`` paired observations are available, or if either series
    has zero variance (co-kurtosis is undefined, not zero, when a denominator
    term vanishes).
    """
    if not asset_returns.index.equals(benchmark_returns.index):
        raise ValueError(
            "asset_returns and benchmark_returns must share the same index "
            "(same trading calendar) -- align them before calling co_kurtosis"
        )

    n = len(asset_returns)
    if n < min_observations:
        raise ValueError(
            f"only {n} paired observations; need at least {min_observations} "
            "to estimate co-kurtosis"
        )

    asset_std = asset_returns.std(ddof=0)
    bench_std = benchmark_returns.std(ddof=0)
    if asset_std == 0 or bench_std == 0:
        raise ValueError(
            "asset_returns or benchmark_returns has zero variance; co-kurtosis is undefined"
        )

    asset_demeaned = asset_returns - asset_returns.mean()
    bench_demeaned = benchmark_returns - benchmark_returns.mean()
    numerator = (asset_demeaned * bench_demeaned**3).mean()
    return float(numerator / (asset_std * bench_std**3))
