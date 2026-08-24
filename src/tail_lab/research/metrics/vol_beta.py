"""Vol beta -- a factor-sensitivity metric (README "The two strategies":
sensitivity is computed with "downside/tail beta, co-skewness, co-kurtosis,
**factor sensitivities**, cheapness-adjusted variants, ..."; `docs/END_STATE.md`
§4 research question 1).

Every metric in this package so far measures an asset's co-movement with the
*benchmark's own returns* (downside beta, tail beta) or the *shape* of that
co-movement (co-skewness, co-kurtosis). Vol beta measures something distinct:
co-movement with the **volatility factor** itself -- day-over-day changes in
VIX, the market's fear gauge -- independent of what the benchmark's price did
that day. Two names can share an identical downside beta yet react very
differently to a pure vol-of-vol shock (a VIX spike with no accompanying SPY
move); vol beta is what tells them apart, and VIX is already a first-class
dataset in this platform (`ingestion/vix.py`), so no new data source is
needed to compute it.

Equities fall as VIX rises, so vol beta is typically negative; more negative
means the name reacts harder to a fear spike, i.e. more fragile -- the
opposite sign convention from downside/tail beta (where more positive means
more fragile). A caller folding this into a fragility composite should
negate it first, exactly as `research/backtest/ranking.py` already negates
co-skewness for the same reason (see that module's ``COMPOSITE_METRICS``
docstring).

Pure numeric function, like ``downside_beta.py`` / ``tail_beta.py``: return
series in, float out. No lake/transforms dependency -- the caller aligns
``asset_returns`` with a VIX pct-change series before calling this.
"""

from __future__ import annotations

import pandas as pd


def vol_beta(
    asset_returns: pd.Series,
    vol_changes: pd.Series,
    *,
    min_observations: int = 2,
) -> float:
    """Beta of ``asset_returns`` against ``vol_changes`` (e.g. VIX pct
    changes), over the whole sample -- ordinary covariance/variance beta,
    with the volatility factor as the regressor instead of the benchmark's
    own returns.

    ``asset_returns`` and ``vol_changes`` must share the same index (the
    same trading calendar) -- the caller aligns them before calling this
    function; a silent positional pairing of misaligned series would be a
    correctness bug, not a convenience, so this raises instead.

    Raises ``ValueError`` if the indices don't match, if fewer than
    ``min_observations`` paired observations are available (the
    mathematical floor is 2, needed for a variance estimate at all -- that
    floor is not a recommendation for a trustworthy estimate), or if
    ``vol_changes`` has zero variance (beta is undefined, not zero, when
    the denominator vanishes).
    """
    if not asset_returns.index.equals(vol_changes.index):
        raise ValueError(
            "asset_returns and vol_changes must share the same index "
            "(same trading calendar) -- align them before calling vol_beta"
        )

    n = len(asset_returns)
    if n < min_observations:
        raise ValueError(
            f"only {n} paired observations; need at least {min_observations} to estimate vol beta"
        )

    variance = vol_changes.var()  # sample variance, ddof=1 (pandas default)
    if variance == 0:
        raise ValueError("vol_changes has zero variance; vol beta is undefined")

    covariance = asset_returns.cov(vol_changes)  # ddof=1, matches variance's convention
    return float(covariance / variance)
