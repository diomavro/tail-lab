"""The Karamata constant -- where a tail stops being a body and starts being a
power law (Taleb et al., arXiv 1908.02347v3, Fig. 1).

A power-law tail is ``P(X > x) ~ L(x) x^-alpha`` with ``L`` slowly varying.
``L`` is not constant everywhere: it settles to a constant only past some point,
and beyond that point the "strong Pareto law" holds and extrapolation across
strikes is licensed. Below it, the same extrapolation is unfounded. That point
is what the paper calls the Karamata constant, and on a log-log survival plot it
is simply where the curve straightens into a line of slope ``-alpha``.

Measuring it matters here for two reasons. It is the precondition on every
Paretan anchor (``paretan.anchor_l_put`` refuses an anchor inside the body), and
it is the gate that stops ``hill.stable_k`` reporting a plateau found in the
body of the distribution as though it were a tail index.

**The estimate is circular and says so.** ``L`` is defined using ``alpha``, and
``alpha`` is estimated from the observations beyond the onset. With
``alpha=None`` this module iterates the two to a fixed point and reports
``converged``; it does not hide the circularity behind a default.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from tail_lab.research.surface.hill import MIN_K, hill_alpha

#: Iterations allowed when solving ``alpha`` and the onset jointly. The map is
#: a contraction in practice and settles in three or four; this is the guard
#: against the case where it oscillates instead.
MAX_FIXED_POINT_ITERATIONS = 12

#: Relative change in ``alpha`` below which the fixed point is called settled.
ALPHA_CONVERGENCE_TOL = 1e-4


@dataclass(frozen=True)
class KaramataFit:
    """Where the strong Pareto law starts, and how well it holds beyond there.

    ``onset`` is in the sample's own units (a return magnitude for a loss
    series). ``karamata_l`` is ``l`` itself, not ``L``: the paper writes the
    scale as ``l^alpha``, so ``l = L^(1/alpha)``, which is what the pricing
    formulas take. ``flatness`` is the relative spread of ``L`` beyond the
    onset -- zero for an exact Pareto sample. ``n_beyond`` is the number of
    observations the whole claim rests on, and is the first thing to read:
    a confident onset resting on eleven points is not a measurement.

    **``is_flat`` is the second thing to read, and a consumer must not use the
    onset without it.** When no stretch of the sample is flat to within the
    requested tolerance -- the common case on real daily returns -- the search
    has nothing to return, and what comes back is the ``min_beyond`` floor
    rather than a measured onset. Reporting that as an onset would be a number
    pretending to be a measurement, so the flag says which it is.
    """

    alpha: float
    onset: float
    karamata_l: float
    flatness: float
    n_beyond: int
    converged: bool
    is_flat: bool


def slowly_varying(sample: Sequence[float], *, alpha: float) -> list[tuple[float, float]]:
    """``(x, L(x))`` for each observation, descending in ``x``.

    ``L(x) = P_hat(X > x) * x^alpha`` with the empirical survival function taken
    at plotting position ``i / (n + 1)`` for the ``i``-th largest value -- the
    convention under which an exact Pareto sample returns ``L`` exactly
    constant rather than merely nearly so.

    Raises ``ValueError`` for a non-positive ``alpha`` or a non-positive
    observation.
    """
    if not math.isfinite(alpha) or alpha <= 0.0:
        raise ValueError(f"alpha must be a finite positive number, got {alpha}")

    ordered = sorted((float(x) for x in sample), reverse=True)
    if not ordered:
        raise ValueError("slowly_varying needs a non-empty sample")
    if ordered[-1] <= 0.0:
        raise ValueError("slowly_varying is defined on strictly positive samples")

    if not all(math.isfinite(x) for x in ordered):
        raise ValueError("slowly_varying needs finite observations; the sample has nan or inf")

    n = len(ordered)
    try:
        return [(x, (i / (n + 1.0)) * x**alpha) for i, x in enumerate(ordered, start=1)]
    except OverflowError as exc:
        # `float.__pow__` RAISES on overflow rather than returning inf, so this
        # is the only way a level can fail to be finite -- and the module's
        # documented contract is ValueError. (An earlier version guarded
        # `math.isfinite(level)` instead and asserted in a comment that `x**alpha`
        # "overflows"; it does not, it raises, so that half of the guard was
        # dead and the comment was wrong.)
        raise ValueError(
            f"L = P(X>x) * x^{alpha} overflows on this sample; "
            "the tail index is too large for the observations' scale"
        ) from exc


def _flatness(values: Sequence[float], *, total: float) -> float:
    """Relative spread ``(max - min) / mean`` of ``L`` over a stretch.

    Computed as ``(max - min) / total * len`` rather than by forming the mean,
    because the two are identical in exact arithmetic and only the first is safe
    in IEEE754. A prefix of subnormal levels can have ``total = 7.4e-323 > 0``
    while ``total / 30`` underflows to **exactly zero** -- so a guard on the sum
    (which is what a caller can meaningfully check) does not protect a division
    by the mean. An earlier version divided by the mean and was guarded on the
    sum; the guard was arithmetically true and protected nothing.

    ``total`` is passed in rather than recomputed so the caller can validate the
    one quantity this divides by.
    """
    return (max(values) - min(values)) / total * len(values)


def _onset_for_alpha(
    sample: Sequence[float], *, alpha: float, tolerance: float, min_beyond: int
) -> tuple[float, float, float, int, bool]:
    """``(onset, karamata_l, flatness, n_beyond, is_flat)`` at fixed ``alpha``.

    Walks inward from the largest observation and keeps the deepest stretch
    whose ``L`` stays within ``tolerance``. If even the top ``min_beyond``
    points fail the tolerance there is no Karamata region at this ``alpha``:
    the floor is returned with ``is_flat=False`` so the caller cannot mistake
    it for one.
    """
    curve = slowly_varying(sample, alpha=alpha)
    levels = [level for _, level in curve]

    # `_flatness` divides by a prefix SUM, and levels are non-negative, so a
    # prefix sum is monotone non-decreasing in the prefix length. Two checks on
    # the endpoints therefore cover every prefix the scan below will form:
    # positive at `min_beyond` (the shortest) and finite over the whole curve
    # (the longest).
    #
    # Scoped to what is consumed, deliberately. Validating every level inside
    # `slowly_varying` refused perfectly fittable samples -- one plausible deep
    # loss (1e-6 against a 3% top) underflows at a large alpha and is never
    # selected, yet the whole sample was rejected.
    prefix_total = sum(levels[:min_beyond])
    if prefix_total <= 0.0:
        raise ValueError(
            f"L = P(X>x) * x^{alpha} underflows to zero across the top {min_beyond} "
            "observations; the tail index is too large for their scale"
        )
    if not math.isfinite(sum(levels)):
        raise ValueError(
            f"L = P(X>x) * x^{alpha} sums to infinity over this sample; "
            "the tail index is too large for the observations' scale"
        )

    # Scan every prefix rather than stopping at the first violation.
    # `_flatness` is (max - min) / mean, and while (max - min) is non-decreasing
    # in `count`, the MEAN can rise faster -- so flatness is NOT monotone and can
    # dip back under tolerance after exceeding it. An earlier version used a
    # `break` and its docstring claimed to keep "the deepest stretch"; on
    # levels [0.92]*3 + [1.00]*17 at tolerance 0.085 it stopped at 3 points and
    # put the onset at x=6.44 where the answer is x=1.05 -- 6.1x too FAR OUT,
    # resting on 3 observations instead of 20, and flagged is_flat=True. (Too
    # far out, not too shallow: a larger threshold is deeper in the tail.)
    best = min_beyond
    best_flatness = _flatness(levels[:min_beyond], total=prefix_total)
    running = prefix_total
    for count in range(min_beyond + 1, len(levels) + 1):
        running += levels[count - 1]
        spread = _flatness(levels[:count], total=running)
        if spread <= tolerance:
            best, best_flatness = count, spread

    # Same underflow trap as `_flatness`: a prefix of subnormal levels has a
    # positive sum whose mean rounds to zero, and `karamata_l` would then be
    # reported as 0.0 -- a scale, in a field a consumer reads. Take the root of
    # the sum and divide by the root of the count instead, which is identical in
    # exact arithmetic and stays representable.
    total_level = sum(levels[:best])
    try:
        karamata_l = total_level ** (1.0 / alpha) / best ** (1.0 / alpha)
    except OverflowError as exc:
        # Splitting the root across numerator and denominator avoids the mean's
        # underflow, at the cost of exposing an overflow the combined form did
        # not have: for alpha below about 0.005, `best ** (1/alpha)` exceeds
        # DBL_MAX on its own. Unreachable through `alpha=None` (a Hill estimate
        # on bounded losses cannot go that low) but reachable from an explicit
        # alpha, and the contract here is ValueError.
        raise ValueError(
            f"the Karamata scale overflows at alpha={alpha} over {best} observations; "
            "alpha is too small for this sample"
        ) from exc
    return (
        curve[best - 1][0],
        karamata_l,
        best_flatness,
        best,
        best_flatness <= tolerance,
    )


def karamata_onset(
    sample: Sequence[float],
    *,
    alpha: float | None = None,
    tolerance: float = 0.05,
    min_beyond: int = 30,
) -> KaramataFit:
    """Smallest ``x`` beyond which ``L`` is flat to within ``tolerance``.

    With ``alpha`` given, one pass and ``converged`` is ``True`` by
    construction -- nothing was solved. With ``alpha=None``, ``alpha`` and the
    onset are iterated to a fixed point: estimate ``alpha`` by Hill on the
    observations beyond the current onset, re-fit the onset at that ``alpha``,
    repeat. ``converged`` is ``False`` if it was still moving at the iteration
    cap, and a caller must not report the onset as a measurement in that case.

    ``min_beyond`` is the floor on how many observations may support the claim.
    Raises ``ValueError`` if the sample is smaller than that, or for a
    non-positive ``tolerance``.
    """
    if tolerance <= 0.0:
        raise ValueError(f"tolerance must be positive, got {tolerance}")
    if min_beyond < 1:
        # A non-positive floor makes `levels[:min_beyond]` empty or a negative
        # slice, which divides by zero or -- worse -- produces a negative mean
        # whose fractional power is COMPLEX, silently, in a float-typed field.
        raise ValueError(f"min_beyond must be at least 1, got {min_beyond}")
    if len(sample) < min_beyond + 1:
        raise ValueError(
            f"need at least {min_beyond + 1} observations to claim an onset "
            f"supported by {min_beyond}, got {len(sample)}"
        )

    if alpha is not None:
        onset, karamata_l, flatness, n_beyond, is_flat = _onset_for_alpha(
            sample, alpha=alpha, tolerance=tolerance, min_beyond=min_beyond
        )
        return KaramataFit(alpha, onset, karamata_l, flatness, n_beyond, True, is_flat)

    current = hill_alpha(sample, k=max(MIN_K, len(sample) // 10)).alpha
    onset, karamata_l, flatness, n_beyond, is_flat = _onset_for_alpha(
        sample, alpha=current, tolerance=tolerance, min_beyond=min_beyond
    )
    converged = False
    for _ in range(MAX_FIXED_POINT_ITERATIONS):
        nxt = hill_alpha(sample, k=min(n_beyond, len(sample) - 1)).alpha
        onset, karamata_l, flatness, n_beyond, is_flat = _onset_for_alpha(
            sample, alpha=nxt, tolerance=tolerance, min_beyond=min_beyond
        )
        if abs(nxt - current) / current < ALPHA_CONVERGENCE_TOL:
            current = nxt
            converged = True
            break
        current = nxt

    return KaramataFit(current, onset, karamata_l, flatness, n_beyond, converged, is_flat)
