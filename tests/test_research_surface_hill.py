"""Pinned tests for the Hill estimator.

The independent reference is a **closed form**, not a snapshot. On the exact
Pareto quantile sample ``Y_i = l (i/(n+1))^(-1/alpha)`` the ratios telescope::

    log(Y_i / Y_(k+1)) = (1/alpha) log((k+1)/i)

so summing over ``i = 1..k`` and inverting gives

    alpha_hat = alpha * k / (k log(k+1) - lgamma(k+1))

``lgamma(k+1) = log(k!)``. Both ``n`` and ``l`` cancel **identically**, not
asymptotically, which is itself one of the assertions below -- the formula
carries no ``n``, and that is correct rather than an omission.
"""

from __future__ import annotations

import math

import pytest

from tail_lab.research.surface.hill import HillEstimate, hill_alpha, hill_plot, stable_k


def pareto_quantile_sample(*, n: int, alpha: float, karamata_l: float = 1.0) -> list[float]:
    """Exact Pareto order statistics -- no randomness, so the test is a
    statement about the estimator and not about a seed."""
    return [karamata_l * (i / (n + 1.0)) ** (-1.0 / alpha) for i in range(1, n + 1)]


def expected_alpha(*, alpha: float, k: int) -> float:
    """The closed form derived in the module docstring."""
    return alpha * k / (k * math.log(k + 1) - math.lgamma(k + 1))


@pytest.mark.parametrize("n", [200, 1_000, 100_000])
@pytest.mark.parametrize("k", [10, 100])
@pytest.mark.parametrize("alpha", [2.0, 3.0, 5.0])
def test_hill_matches_the_closed_form_and_does_not_depend_on_n(
    n: int, k: int, alpha: float
) -> None:
    sample = pareto_quantile_sample(n=n, alpha=alpha)
    assert hill_alpha(sample, k=k).alpha == pytest.approx(
        expected_alpha(alpha=alpha, k=k), rel=1e-12
    )


def test_closed_form_worked_by_hand() -> None:
    """alpha=3, k=100: ``100 log 101 = 461.512051684126``,
    ``lgamma(101) = 363.73937555556347``, difference ``97.77267612856252``,
    so ``alpha_hat = 300 / 97.77267612856252 = 3.068341911860183``."""
    assert 100 * math.log(101) == pytest.approx(461.512051684126, rel=1e-12)
    assert math.lgamma(101) == pytest.approx(363.73937555556347, rel=1e-12)
    assert expected_alpha(alpha=3.0, k=100) == pytest.approx(3.068341911860183, rel=1e-12)


@pytest.mark.parametrize("c", [0.01, 0.1, 0.5])
@pytest.mark.parametrize("k", [5, 10, 100])
def test_geometric_block_has_its_own_closed_form(c: float, k: int) -> None:
    """A purely geometric top block ``Y_i = Y_(k+1) e^(c(k+1-i))`` gives
    ``sum log(Y_i/Y_(k+1)) = c * k(k+1)/2``, hence ``alpha_hat = 2/(c(k+1))``.

    A second, structurally different reference for the same estimator.
    """
    sample = [math.exp(c * (k + 1 - i)) for i in range(1, k + 2)]
    assert hill_alpha(sample, k=k).alpha == pytest.approx(2.0 / (c * (k + 1)), rel=1e-10)


def test_standard_error_is_alpha_hat_over_root_k() -> None:
    """Hill's asymptotic SE uses the **estimate**, not the true alpha, because
    the estimator does not have the true alpha. At alpha=3, k=100 the sample
    returns 3.0683419..., so the SE is 0.30683419..., NOT 0.3 -- pinning the
    latter would contradict this relation on the very same sample.
    """
    estimate = hill_alpha(pareto_quantile_sample(n=1_000, alpha=3.0), k=100)
    assert estimate.standard_error == pytest.approx(estimate.alpha / 10.0, rel=1e-15)
    assert estimate.standard_error == pytest.approx(0.3068341911860183, rel=1e-10)


@pytest.mark.parametrize("scale", [1e-4, 7.3, 1_000.0])
def test_tail_index_is_scale_free(scale: float) -> None:
    """Multiplying every observation by a constant leaves alpha unchanged.

    This is the half of the paper's remark that transfers. It is NOT a licence
    to compare a daily alpha with a 30-day one: temporal aggregation is a
    different operation and does not preserve the estimate at reachable k.
    """
    sample = pareto_quantile_sample(n=500, alpha=2.75)
    scaled = [x * scale for x in sample]
    assert hill_alpha(scaled, k=100).alpha == pytest.approx(
        hill_alpha(sample, k=100).alpha, rel=1e-12
    )


def test_shuffling_the_sample_changes_nothing() -> None:
    sample = pareto_quantile_sample(n=300, alpha=3.0)
    shuffled = sample[150:] + sample[:150]
    assert hill_alpha(shuffled, k=50).alpha == hill_alpha(sample, k=50).alpha


def test_threshold_is_the_k_plus_first_order_statistic() -> None:
    sample = pareto_quantile_sample(n=200, alpha=3.0)
    ordered = sorted(sample, reverse=True)
    estimate = hill_alpha(sample, k=40)
    assert estimate.threshold == ordered[40]
    assert estimate.n == 200
    assert estimate.k == 40


@pytest.mark.parametrize(
    ("sample", "k", "match"),
    [
        ([3.0, 2.0, 1.0], 0, "at least 1"),
        ([3.0, 2.0, 1.0], 5, "at least 6 observations"),
        ([3.0, 2.0, 0.0], 1, "strictly positive"),
        ([2.0, 2.0, 2.0], 2, "tie-degenerate"),
    ],
)
def test_refusals(sample: list[float], k: int, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        hill_alpha(sample, k=k)


def test_hill_plot_is_ascending_in_k_and_skips_degenerate_points() -> None:
    plot = hill_plot(pareto_quantile_sample(n=200, alpha=3.0), k_min=10, k_max=50, step=5)
    assert [p.k for p in plot] == [10, 15, 20, 25, 30, 35, 40, 45, 50]
    for point, expected_k in zip(plot, range(10, 51, 5), strict=True):
        assert point.alpha == pytest.approx(expected_alpha(alpha=3.0, k=expected_k), rel=1e-12)


def test_stable_k_finds_the_plateau_on_a_pareto_sample() -> None:
    """An exact Pareto sample has a genuine plateau, so a plateau-finder that
    cannot find one here is broken."""
    plot = hill_plot(pareto_quantile_sample(n=2_000, alpha=3.0), k_min=50, k_max=400)
    found = stable_k(plot, window=20, tolerance=0.05)
    assert found is not None
    assert found.alpha == pytest.approx(3.0, rel=0.1)


def test_stable_k_refuses_when_there_is_no_plateau() -> None:
    """A sample whose Hill estimate drifts steadily has no stable region, and
    ``None`` is the honest answer -- an alpha read off such a plot is a choice
    dressed as a measurement."""
    drifting = [
        HillEstimate(alpha=1.0 + 0.1 * i, k=i, threshold=1.0, standard_error=0.1, n=100)
        for i in range(60)
    ]
    assert stable_k(drifting, window=20, tolerance=0.01) is None


def test_stable_k_rejects_a_plateau_inside_the_body() -> None:
    """The gate that matters. A Hill plot flattens wherever the log-log slope
    is locally constant, and that includes the body of a lognormal-ish
    distribution -- on real index down-moves the default settings find a
    plateau at a 1.9% daily move, the 5th percentile. Passing the Karamata
    onset as ``min_threshold`` is what distinguishes a tail from a body.
    """
    flat = [
        HillEstimate(alpha=2.3, k=i, threshold=0.019, standard_error=0.2, n=500) for i in range(40)
    ]
    assert stable_k(flat, window=20, tolerance=0.05) is not None
    assert stable_k(flat, window=20, tolerance=0.05, min_threshold=0.04) is None


def test_hill_plot_guards() -> None:
    sample = pareto_quantile_sample(n=100, alpha=3.0)
    with pytest.raises(ValueError, match="step must be at least 1"):
        hill_plot(sample, step=0)
    with pytest.raises(ValueError, match="exceeds the largest usable k"):
        hill_plot(sample, k_min=500)


def test_hill_plot_skips_tie_degenerate_k_rather_than_failing() -> None:
    """A flat stretch at the top makes small ``k`` undefined. One such ``k``
    must not destroy the rest of the plot."""
    sample = [5.0, 5.0, 5.0, 4.0, 3.0, 2.0, 1.5, 1.2, 1.1, 1.05, 1.01]
    plot = hill_plot(sample, k_min=1, k_max=9)
    assert [p.k for p in plot] == [3, 4, 5, 6, 7, 8, 9]


def test_stable_k_guards() -> None:
    plot = hill_plot(pareto_quantile_sample(n=200, alpha=3.0), k_min=10, k_max=100)
    with pytest.raises(ValueError, match="window must be at least 1"):
        stable_k(plot, window=0)
    with pytest.raises(ValueError, match="tolerance must be positive"):
        stable_k(plot, tolerance=0.0)


def test_stable_k_ignores_a_window_whose_mean_alpha_is_not_positive() -> None:
    """Defensive: a non-positive mean cannot be tested against a relative
    tolerance, so such a window is skipped rather than dividing by it."""
    degenerate = [
        HillEstimate(alpha=-1.0, k=i, threshold=1.0, standard_error=0.1, n=10) for i in range(5)
    ]
    assert stable_k(degenerate, window=5, tolerance=0.5) is None
