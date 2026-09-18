"""Which return basis a tail fit is allowed to use, and why it is not a detail.

Taleb et al. prove it as a theorem: if ``S`` has a survival function in the
regular-variation class ``RV_alpha``, the **log** return ``log(S/S_0)`` is not
in ``RV_alpha``. The arithmetic return ``(S - S_0)/S_0`` is, and carries the
*same* ``alpha`` -- the tail index is scale-free, so only the slowly varying
function ``L`` differs, reaching its constant at a different rate.

So a Hill fit on log returns estimates the tail of a different object. This
module makes that the *displayed* consequence rather than a footnote:
``loss_magnitudes`` is the only sanctioned input to a tail fit on this platform,
and ``compare_return_bases`` puts both fits side by side so a reader can see the
divergence instead of being told about it.

The distinction does not touch ``put_roll.trailing_realized_vol``, which uses
log returns and is correct to: that is a volatility estimate, not a tail index,
and it is not a precedent (``docs/adr/0026``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tail_lab.research.surface.hill import HillEstimate, hill_alpha

# The sign of ``ReturnBasisComparison.divergence`` **depends on which tail is
# being transformed**, which is worth stating because it is easy to get
# backwards. For downside losses ``-log(1 - r)`` is *unbounded* while the
# arithmetic loss ``r`` cannot exceed 1 -- you cannot lose more than everything
# -- so the log basis stretches the far tail and reads a **lower** alpha
# (fatter). On the upside the transform compresses instead and alpha rises.
# Measured on the exact Pareto(4) loss construction pinned in
# ``tests/test_research_surface_returns.py``: arithmetic 4.05, log 3.41.


@dataclass(frozen=True)
class ReturnBasisComparison:
    """The same price series fitted in both bases, with the verdict attached.

    ``alpha_log`` is reported so it can be shown and dismissed. It is never an
    input to anything: ``divergence`` being large is the expected result, not a
    problem to reconcile, and its sign is not fixed -- see the module note
    above on which tail is being transformed.
    """

    alpha_arithmetic: HillEstimate
    alpha_log: HillEstimate
    divergence: float


def loss_magnitudes(prices: pd.Series) -> pd.Series:
    """Magnitudes of arithmetic down-moves -- the ``r`` in the paper's
    ``S = (1 - r) S_0``, and the only sanctioned input to a tail fit here.

    ``r_t = (S_{t-1} - S_t) / S_{t-1}``, keeping only the strictly positive
    values. Up-moves are dropped rather than reflected: the left tail of an
    equity is a different object from its right tail, and a put is bought
    against the left one.

    Raises ``ValueError`` if the series has fewer than two points, is not
    strictly positive, or contains no down-move at all.
    """
    if len(prices) < 2:
        raise ValueError(f"need at least two prices to form a return, got {len(prices)}")
    if not (prices > 0).all():
        raise ValueError("prices must be strictly positive to form arithmetic returns")

    losses = -prices.pct_change().dropna()
    down = losses[losses > 0.0]
    if down.empty:
        raise ValueError("price series contains no down-moves; there is no left tail to fit")
    return down


def log_loss_magnitudes(prices: pd.Series) -> pd.Series:
    """The same down-moves in the log basis -- ``-log(S_t / S_{t-1})``.

    Provided **only** so ``compare_return_bases`` can show what the wrong basis
    does. Nothing else in this package may consume it.
    """
    if len(prices) < 2:
        raise ValueError(f"need at least two prices to form a return, got {len(prices)}")
    if not (prices > 0).all():
        raise ValueError("prices must be strictly positive to form log returns")

    log_losses = (
        -pd.Series(np.log(prices.to_numpy(dtype=float)), index=prices.index).diff().dropna()
    )
    down = log_losses[log_losses > 0.0]
    if down.empty:
        raise ValueError("price series contains no down-moves; there is no left tail to fit")
    return down


def _as_floats(series: pd.Series) -> list[float]:
    """``Series`` -> ``list[float]``, the shape the Hill estimator accepts."""
    return [float(x) for x in series]


def compare_return_bases(prices: pd.Series, *, k: int) -> ReturnBasisComparison:
    """Fit the tail index in both bases and report the divergence.

    **There is deliberately no "and here is which one is right" flag.** An
    earlier version reported whether the log-basis Hill estimate drifted
    monotonically as ``k`` fell, on the theory that a regularly varying tail
    settles and a non-regularly-varying one drifts. It had no discriminating
    power: the Hill estimate on an exact Pareto quantile sample is
    ``alpha * k / (k log(k+1) - lgamma(k+1))``, which is monotone in ``k`` by
    construction, so the criterion fired on the textbook regularly-varying case
    it was supposed to rule out. The theorem is proved in the paper; this
    function reports the two estimates and their difference, and does not
    pretend to re-derive the proof from 800 observations.
    """
    arithmetic = hill_alpha(_as_floats(loss_magnitudes(prices)), k=k)
    log_sample = _as_floats(log_loss_magnitudes(prices))
    log_fit = hill_alpha(log_sample, k=k)

    return ReturnBasisComparison(
        alpha_arithmetic=arithmetic,
        alpha_log=log_fit,
        divergence=log_fit.alpha - arithmetic.alpha,
    )
