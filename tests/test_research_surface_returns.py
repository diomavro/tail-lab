"""Pinned tests for the return basis -- the paper's log-return theorem, turned
into an assertion rather than a citation.

The construction gives exact control: a price path that alternates down by
``r_i`` and straight back up means ``loss_magnitudes`` returns precisely the
``r_i`` that were put in, so the arithmetic-basis fit can be checked against the
same closed form used in ``test_research_surface_hill.py``.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from tail_lab.research.surface.returns import (
    compare_return_bases,
    log_loss_magnitudes,
    loss_magnitudes,
)
from tests.test_research_surface_hill import expected_alpha, pareto_quantile_sample


def price_path_with_losses(losses: list[float], *, base: float = 100.0) -> pd.Series:
    """A path whose arithmetic down-moves are exactly ``losses``.

    Down by ``r``, back to ``base``, repeat. Up-moves are discarded by
    ``loss_magnitudes``, so the surviving sample is the input exactly.
    """
    values = [base]
    for r in losses:
        values.extend([base * (1.0 - r), base])
    return pd.Series(values, index=pd.RangeIndex(len(values)))


def test_loss_magnitudes_returns_the_arithmetic_down_moves_exactly() -> None:
    losses = [0.10, 0.25, 0.03]
    got = loss_magnitudes(price_path_with_losses(losses))
    assert list(got) == pytest.approx(losses, rel=1e-12)


def test_loss_magnitudes_drops_up_moves() -> None:
    """The left tail of an equity is a different object from its right tail,
    and a put is bought against the left one."""
    prices = pd.Series([100.0, 110.0, 99.0, 120.0])
    assert len(loss_magnitudes(prices)) == 1
    assert float(loss_magnitudes(prices).iloc[0]) == pytest.approx(0.10, rel=1e-12)


def test_log_loss_magnitudes_is_the_log_transform_of_the_same_moves() -> None:
    """``-log(S_t/S_{t-1}) = -log(1 - r)``, which exceeds ``r`` and increasingly
    so as ``r`` grows -- the compression that changes the tail class."""
    losses = [0.10, 0.50, 0.90]
    got = log_loss_magnitudes(price_path_with_losses(losses))
    assert list(got) == pytest.approx([-math.log(1.0 - r) for r in losses], rel=1e-12)


def test_arithmetic_basis_recovers_the_closed_form() -> None:
    """Because the path was built to give back exactly the Pareto sample, the
    arithmetic fit must equal the Hill closed form for that sample."""
    losses = pareto_quantile_sample(n=500, alpha=4.0, karamata_l=0.2)
    comparison = compare_return_bases(price_path_with_losses(losses), k=100)
    assert comparison.alpha_arithmetic.alpha == pytest.approx(
        expected_alpha(alpha=4.0, k=100), rel=1e-9
    )


def test_the_log_basis_measures_a_different_tail() -> None:
    """The theorem's observable consequence, with the sign the data actually
    shows.

    For *down-moves* the log basis reads a **lower** alpha -- a fatter tail --
    because ``-log(1-r)`` is unbounded while the arithmetic loss ``r`` cannot
    exceed 1. The transform stretches the far tail rather than compressing it.
    (On the upside it compresses and the sign flips; the constant thing is that
    the two bases are estimating different objects, not which is larger.)

    The arithmetic side is pinned to the Hill closed form, which is an
    independent derivation. The log side deliberately is **not** pinned to a
    number: it has no closed form on this construction, so a numeric pin would
    be a snapshot of the function's own output, which ``docs/STANDARDS.md``
    rules out. The checkable content is the relation.
    """
    losses = pareto_quantile_sample(n=500, alpha=4.0, karamata_l=0.2)
    comparison = compare_return_bases(price_path_with_losses(losses), k=100)
    assert comparison.alpha_log.alpha < comparison.alpha_arithmetic.alpha
    assert comparison.divergence == pytest.approx(
        comparison.alpha_log.alpha - comparison.alpha_arithmetic.alpha, rel=1e-15
    )
    assert comparison.alpha_arithmetic.alpha == pytest.approx(
        expected_alpha(alpha=4.0, k=100), rel=1e-9
    )
    assert comparison.divergence < 0.0


def test_the_arithmetic_basis_is_the_hill_closed_form_at_every_k() -> None:
    """The arithmetic basis is a genuine power law by construction, so its
    estimate equals the closed form at every ``k`` -- an independent derivation
    rather than a snapshot.

    Worth stating explicitly because that closed form,
    ``alpha * k / (k log(k+1) - lgamma(k+1))``, is **monotone in k**. An earlier
    version of this module reported a "does the estimate drift?" flag as
    evidence of the paper's log-return theorem; the flag fired here too, on the
    textbook regularly-varying case it was meant to rule out, so it had no
    discriminating power and was removed.
    """
    losses = pareto_quantile_sample(n=800, alpha=4.0, karamata_l=0.15)
    sample = [float(x) for x in loss_magnitudes(price_path_with_losses(losses))]
    from tail_lab.research.surface.hill import hill_alpha

    alphas = [hill_alpha(sample, k=k).alpha for k in (200, 100, 50)]
    assert alphas == pytest.approx(
        [expected_alpha(alpha=4.0, k=k) for k in (200, 100, 50)], rel=1e-9
    )


@pytest.mark.parametrize(
    ("prices", "match"),
    [
        (pd.Series([100.0]), "at least two prices"),
        (pd.Series([100.0, -5.0]), "strictly positive"),
        (pd.Series([100.0, 110.0, 120.0]), "no down-moves"),
    ],
)
def test_refusals(prices: pd.Series, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        loss_magnitudes(prices)
    with pytest.raises(ValueError, match=match):
        log_loss_magnitudes(prices)
