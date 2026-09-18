"""Hill estimator for the tail index ``alpha`` -- the platform's first fat-tail
measurement (``docs/END_STATE.md`` §4 Q6; ``docs/adr/0026``).

The Paretan pricing heuristic in ``paretan.py`` takes ``alpha`` as its sole
parameter. This module is where ``alpha`` comes from when it is measured on
realised moves rather than fitted to quotes. Taleb et al. calibrate "beyond the
Karamata constant ... using such standard techniques as the Hill estimator",
and assert ``alpha`` fluctuates minimally over time -- an assertion this module
makes testable rather than assumed.

**A Hill point estimate on its own is not a measurement.** The estimator is
notoriously sensitive to ``k``, the number of order statistics it reads.
Measured 2026-09-18 on the SPY down-moves this repo actually has (bronze OHLCV's
rolling five-year window, 2021-08..2026-08, 577 of them), alpha runs from
**1.69 to 3.57** over ``k`` in [20, 200] -- a spread of 1.87, wider than most
effects anyone would want to detect. (``make tail-alpha`` prints the plateau and
the onset, not this sweep; reproduce the sweep with ``hill_plot(losses,
k_min=20, k_max=200)`` on the same series.)

So the useful object is ``hill_plot`` (alpha as a function of k) plus
``stable_k``, which reports a plateau **or refuses**. An alpha read off a plot
with no stable region is a choice dressed as a measurement.

``stable_k`` also takes ``min_threshold``, and callers should pass it. A Hill
plot flattens wherever the log-log slope is locally constant, and for a
lognormal-ish *body* that happens too. At the default settings on real SPY
down-moves the plateau lands at alpha 2.75 at a **1.71%** daily move, fitted on
55 order statistics (56 observations lie at or beyond that threshold, 9.7% of
down-days). Whether that is tail or body is exactly what a percentile cannot
settle, and the Karamata test is what does: ``L`` is not flat there -- relative
spread **0.624** over that prefix, against a 0.05 tolerance, and no prefix of
this sample does better than 0.609 -- so the strong Pareto law is not
established at that threshold and the plateau is not a tail index. It is worth knowing that 2.75 is also the figure Taleb et al. use for
SPX, which makes it the most temptingly publishable wrong answer available.
``docs/DISCOVERIES.md`` §12 has the measurement.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

#: Smallest ``k`` worth reporting. Below roughly ten order statistics the
#: estimator's own standard error (``alpha / sqrt(k)``) exceeds a third of the
#: estimate, so the number carries no information a reader can act on.
MIN_K = 10


@dataclass(frozen=True)
class HillEstimate:
    """One ``(k, alpha)`` point of a Hill plot.

    ``alpha`` is the tail index: ``P(X > x) ~ L(x) x^-alpha``. ``threshold`` is
    ``X_(k+1)``, the order statistic the fit starts from, **in the sample's own
    units** -- for a loss series that is a return magnitude, and reporting it is
    what lets a reader see whether the fit sits in the tail or in the body.
    ``standard_error`` is Hill's asymptotic ``alpha / sqrt(k)``. It assumes iid
    draws, which daily returns are not -- volatility clusters, so the effective
    sample is smaller than ``k`` and this understates. Treat it as a floor on
    the uncertainty, never as the whole of it; nothing here computes the
    clustering-adjusted version.
    """

    alpha: float
    k: int
    threshold: float
    standard_error: float
    n: int


def hill_alpha(sample: Sequence[float], *, k: int) -> HillEstimate:
    """Hill estimate of the tail index from the top ``k`` order statistics.

    ``alpha_hat = 1 / mean(log(X_(i) / X_(k+1)))`` over ``i = 1..k``, where
    ``X_(1) >= ... >= X_(n)`` are the sample sorted descending.

    ``sample`` must be strictly positive -- the estimator is defined on the
    right tail of a positive variable. For price moves, pass **magnitudes of
    arithmetic down-moves** (``returns.loss_magnitudes``), never log returns:
    the paper's own theorem shows log returns are not in the regular-variation
    class, so a Hill fit on them is estimating the tail of a different object.

    Raises ``ValueError`` if ``k < 1``, if ``k + 1 > len(sample)`` (there is no
    ``X_(k+1)`` to divide by), if any value is non-positive, or if the top
    ``k + 1`` values are all equal (the logs are then all zero and ``alpha`` is
    infinite, not large -- a tie-degenerate sample, typically a tick-pinned
    price series).
    """
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")

    ordered = sorted((float(x) for x in sample), reverse=True)
    n = len(ordered)
    if k + 1 > n:
        raise ValueError(
            f"k={k} needs at least {k + 1} observations to have an X_(k+1) threshold, got {n}"
        )
    # Finiteness FIRST: `nan <= 0.0` is False, so a NaN would sail through the
    # positivity check below and come back as `alpha=nan` in a float field.
    if not all(math.isfinite(x) for x in ordered):
        raise ValueError("hill_alpha needs finite observations; the sample contains nan or inf")
    if ordered[-1] <= 0.0:
        raise ValueError(
            "hill_alpha is defined on strictly positive samples; "
            "pass loss magnitudes, not signed returns"
        )

    return _hill_from_ordered(ordered, k=k)


def _hill_from_ordered(ordered: Sequence[float], *, k: int) -> HillEstimate:
    """``hill_alpha``'s core, on an already-sorted, already-validated sample.

    Split out so ``hill_plot`` validates the sample once instead of once per
    ``k``: re-scanning 3,520 observations on every one of 3,510 calls cost
    +48-79% of the plot's runtime and risked the two checks diverging.
    """
    threshold = ordered[k]
    log_excess_mean = sum(math.log(x / threshold) for x in ordered[:k]) / k
    if log_excess_mean <= 0.0:
        raise ValueError(
            f"the top {k + 1} observations are all equal ({threshold}); "
            "the tail index is undefined on a tie-degenerate sample"
        )
    if not math.isfinite(log_excess_mean):
        # `x / threshold` can overflow to inf across a dynamic range above
        # ~1.8e308, and `1 / inf` is 0.0 -- an alpha of zero, below MIN_ALPHA,
        # reported with a standard error of zero, i.e. perfect precision.
        raise ValueError(
            f"the sample spans too wide a dynamic range at k={k} for the excess "
            "ratios to be finite; the tail index cannot be estimated"
        )

    alpha = 1.0 / log_excess_mean
    return HillEstimate(
        alpha=alpha,
        k=k,
        threshold=threshold,
        standard_error=alpha / math.sqrt(k),
        n=len(ordered),
    )


def hill_plot(
    sample: Sequence[float],
    *,
    k_min: int = MIN_K,
    k_max: int | None = None,
    step: int = 1,
) -> list[HillEstimate]:
    """``hill_alpha`` across a range of ``k`` -- the Hill plot, in ascending k.

    ``k_max`` defaults to ``len(sample) - 1``, the largest ``k`` with a
    threshold. Points whose sample is tie-degenerate at that ``k`` are skipped
    rather than raising, so one flat stretch does not destroy the whole plot.

    Raises ``ValueError`` for a non-positive ``step`` or a ``k_min`` past the
    end of the sample.
    """
    if step < 1:
        raise ValueError(f"step must be at least 1, got {step}")
    if k_min < 1:
        # `hill_plot` used to inherit this from `hill_alpha`; delegating to
        # `_hill_from_ordered` dropped it, and a negative k_min then produced a
        # message asserting something false about the sample.
        raise ValueError(f"k_min must be at least 1, got {k_min}")

    n = len(sample)
    upper = n - 1 if k_max is None else min(k_max, n - 1)
    if k_min > upper:
        raise ValueError(
            f"k_min={k_min} exceeds the largest usable k ({upper}) for a sample of {n}"
        )

    # Validate positivity ONCE, up front, rather than letting the per-k except
    # swallow it. A blanket `except ValueError: continue` turns bad input into a
    # verdict about the data: a series with one non-positive value produced an
    # empty plot, and `scripts/tail_alpha.py` then reported "this data supports
    # no tail index" for a sample that was never fitted at all.
    if not all(math.isfinite(float(x)) for x in sample):
        raise ValueError("hill_plot needs finite observations; the sample contains nan or inf")
    if any(float(x) <= 0.0 for x in sample):
        raise ValueError(
            "hill_plot is defined on strictly positive samples; "
            "pass loss magnitudes, not signed returns"
        )

    # Skip a tie-degenerate k by TESTING for it, rather than by calling
    # `hill_alpha` and catching. Descending order means `ordered[0] ==
    # ordered[k]` is exactly "the top k+1 observations are equal", where alpha
    # is infinite rather than large. Catching instead would need either a
    # blanket `except ValueError` -- which previously swallowed "not strictly
    # positive" and turned bad input into a verdict about the data -- or a
    # match on the message text, plus an unreachable re-raise behind it.
    ordered = sorted((float(x) for x in sample), reverse=True)
    return [
        _hill_from_ordered(ordered, k=k)
        for k in range(k_min, upper + 1, step)
        if ordered[0] != ordered[k]
    ]


def stable_k(
    plot: Sequence[HillEstimate],
    *,
    window: int = 20,
    tolerance: float = 0.05,
    min_threshold: float | None = None,
) -> HillEstimate | None:
    """The first plateau in a Hill plot, or ``None`` if there isn't one.

    A plateau is ``window`` consecutive points whose ``alpha`` all sit within
    ``tolerance`` (relative) of the window's mean. The returned estimate is the
    window's midpoint. Scanning from the left returns the plateau at the
    highest threshold -- deepest in the tail -- which is the one to prefer when
    several exist.

    ``min_threshold`` rejects any window whose midpoint threshold falls below
    it, and callers that can compute it **should pass the Karamata onset**. A
    Hill plot flattens wherever the log-log slope is locally constant, which
    includes the body of a lognormal-ish distribution; without this gate the
    function happily returns a body slope with every appearance of stability.

    Returning ``None`` is a real answer and the caller must render it as one:
    there is no tail index here that this data supports.
    """
    if window < 1:
        raise ValueError(f"window must be at least 1, got {window}")
    if tolerance <= 0.0:
        raise ValueError(f"tolerance must be positive, got {tolerance}")

    # Refuse the WHOLE plot on one non-finite alpha rather than skipping the
    # window that holds it. `hill_plot` refuses an entire sample for one bad
    # observation, on the reasoning that a per-point skip turns bad input into a
    # verdict about the data; skipping a window here would be the same mistake
    # one layer up, and the plateau returned would carry k, threshold and n from
    # a run that produced a NaN somewhere else.
    if not all(math.isfinite(p.alpha) for p in plot):
        raise ValueError("stable_k needs finite alphas; the Hill plot contains nan or inf")

    for start in range(len(plot) - window + 1):
        window_points = plot[start : start + window]
        alphas = [p.alpha for p in window_points]
        mean_alpha = sum(alphas) / window
        # `mean_alpha` can be inf while every alpha is finite (the sum
        # overflows), and `abs(a - inf)/inf` is nan, which is never > tolerance
        # -- so the window would certify at any tolerance.
        if not math.isfinite(mean_alpha) or mean_alpha <= 0.0:
            continue
        if any(abs(a - mean_alpha) / mean_alpha > tolerance for a in alphas):
            continue
        midpoint = window_points[window // 2]
        if min_threshold is not None and midpoint.threshold < min_threshold:
            continue
        return midpoint
    return None
