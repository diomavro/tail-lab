"""The smile's slope at a put anchor, for the P3 ceiling (``AGENT_TODO.md`` P4).

``alpha_upper_bound`` needs ``sigma'(K)`` -- implied vol's slope per dollar of
strike at the anchor -- and nothing else computes it. Differencing the two
nearest strikes divides quote noise by the smallest gap there is (a half-tick
error moves the ceiling by ~0.27 at $1 spacing), and a one-sided line measures
the slope half a window away, biasing the ceiling UP on a convex skew. So:
a least-squares quadratic through the anchor and its nearest listed strikes on
both sides, reading the LINEAR coefficient, which is the slope AT the anchor.

Refusal is a normal return value (``slope is None`` with a ``refusal``): a
missing slope must never be substituted by 0.0, which is a flat smile and moves
the ceiling materially. Callers carry ``ceiling: null`` instead.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

# The anchor's own row is extra: this many OTHER strikes are fitted around it.
NEIGHBOUR_STRIKES = 8
MIN_PER_SIDE = 3
# Below this many usable rows the ceiling's noise (sd 0.29-0.34 at a $1 grid)
# is worse than the nearest-neighbour slope this fit replaces.
MIN_ROWS = 7
_QUADRATIC_PARAMETERS = 3


@dataclass(frozen=True)
class SmileSlope:
    """``slope`` is dsigma/dK (vol per $) at the anchor, ``std_error`` its
    least-squares standard error, ``strikes`` the rows fitted. All three are
    empty/``None`` when ``refusal`` is set."""

    slope: float | None
    std_error: float | None
    strikes: tuple[float, ...]
    refusal: str | None


def _refused(reason: str) -> SmileSlope:
    return SmileSlope(slope=None, std_error=None, strikes=(), refusal=reason)


def select_strikes(strikes: Sequence[float], anchor: float) -> list[float] | None:
    """The anchor (if listed) plus its ``NEIGHBOUR_STRIKES`` nearest listed
    strikes, filled by distance but with at least ``MIN_PER_SIDE`` strictly
    below and strictly above. ``None`` when a side has too few."""
    below = sorted((k for k in strikes if k < anchor), key=lambda k: anchor - k)
    above = sorted((k for k in strikes if k > anchor), key=lambda k: k - anchor)
    if len(below) < MIN_PER_SIDE or len(above) < MIN_PER_SIDE:
        return None
    chosen = below[:MIN_PER_SIDE] + above[:MIN_PER_SIDE]
    rest = sorted(below[MIN_PER_SIDE:] + above[MIN_PER_SIDE:], key=lambda k: abs(k - anchor))
    chosen += rest[: NEIGHBOUR_STRIKES - len(chosen)]
    return sorted(chosen + [k for k in strikes if k == anchor][:1])


def smile_slope(strikes: Sequence[float], ivs: Sequence[float | None], anchor: float) -> SmileSlope:
    """Fit ``iv ~ a + b (K - anchor) + c (K - anchor)^2`` and report ``b``.

    ``strikes``/``ivs`` are one expiry's listed puts, one row per strike. Rows
    with a null or non-positive ``iv`` are dropped first (the Cboe adapter maps
    its zero-fill to null), so the neighbours are chosen among strikes that
    actually carry a vol.
    """
    usable = {
        float(k): float(v)
        for k, v in zip(strikes, ivs, strict=True)
        if v is not None and math.isfinite(v) and v > 0.0
    }
    chosen = select_strikes(list(usable), anchor)
    if chosen is None:
        return _refused(
            f"fewer than {MIN_PER_SIDE} strikes with an iv strictly on each side of {anchor:g}"
        )
    if len(chosen) < MIN_ROWS:
        return _refused(f"only {len(chosen)} usable rows (< {MIN_ROWS}) around {anchor:g}")
    x = np.array([k - anchor for k in chosen])
    y = np.array([usable[k] for k in chosen])
    design = np.column_stack([np.ones_like(x), x, x * x])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    residual = y - design @ coef
    dof = len(x) - _QUADRATIC_PARAMETERS
    sigma2 = float(residual @ residual) / dof
    cov = sigma2 * np.linalg.inv(design.T @ design)
    return SmileSlope(
        slope=float(coef[1]),
        std_error=math.sqrt(float(cov[1, 1])),
        strikes=tuple(chosen),
        refusal=None,
    )
