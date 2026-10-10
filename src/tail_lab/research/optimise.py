"""One golden-section search, shared by the layers that need a 1-D optimum."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Final

_INVERSE_PHI: Final = (math.sqrt(5.0) - 1.0) / 2.0


def golden_section(
    f: Callable[[float], float],
    lo: float,
    hi: float,
    tol: float,
    *,
    maximise: bool = False,
) -> float:
    """The extremum of a unimodal ``f`` on ``[lo, hi]`` to ``tol`` (the minimum
    unless ``maximise``). Deterministic: the same inputs give bit-identical
    output. Ends are not checked; callers that need an exact bound do so."""
    sign = -1.0 if maximise else 1.0
    c = hi - _INVERSE_PHI * (hi - lo)
    d = lo + _INVERSE_PHI * (hi - lo)
    fc, fd = sign * f(c), sign * f(d)
    while hi - lo > tol:
        if fc <= fd:
            hi, d, fd = d, c, fc
            c = hi - _INVERSE_PHI * (hi - lo)
            fc = sign * f(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + _INVERSE_PHI * (hi - lo)
            fd = sign * f(d)
    return 0.5 * (lo + hi)
