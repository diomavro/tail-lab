"""Pinned tests for the Karamata onset.

Both references are constructions whose answer is known *before* the function
runs: an exact Pareto sample, where ``L`` is constant by algebra, and a splice
whose onset is placed by hand.
"""

from __future__ import annotations

import pytest

from tail_lab.research.surface.hill import hill_plot, stable_k
from tail_lab.research.surface.karamata import karamata_onset, slowly_varying
from tests.test_research_surface_hill import pareto_quantile_sample


def spliced_sample(*, n: int, n_tail: int, alpha: float, onset: float) -> list[float]:
    """Pareto above ``onset``, a uniform body below it.

    For the top ``m`` points, survival ``i/(n+1)`` and ``P(X>x) = C x^-alpha``
    with ``C 	imes onset^-alpha = m/(n+1)`` give ``Y_i = onset (m/i)^(1/alpha)``,
    so ``Y_m = onset`` exactly. ``L`` is then ``onset^alpha m/(n+1)`` for every
    tail point -- constant by construction, and *not* constant below.
    """
    tail = [onset * (n_tail / i) ** (1.0 / alpha) for i in range(1, n_tail + 1)]
    body_n = n - n_tail
    body = [onset * (1.0 - 0.9 * (j + 1) / body_n) for j in range(body_n)]
    return tail + body


@pytest.mark.parametrize("alpha", [2.0, 3.0, 4.5])
def test_exact_pareto_has_constant_L_and_an_onset_at_the_sample_minimum(alpha: float) -> None:
    """For ``Y_i = l (i/(n+1))^(-1/alpha)``, ``L(Y_i) = (i/(n+1)) Y_i^alpha``
    telescopes to ``l^alpha`` for every ``i``. So the whole sample is already
    beyond the Karamata point: the onset is its minimum, flatness is zero, and
    ``karamata_l`` recovers ``l`` itself."""
    sample = pareto_quantile_sample(n=500, alpha=alpha, karamata_l=1.0)
    fit = karamata_onset(sample, alpha=alpha)
    assert fit.onset == pytest.approx(min(sample), rel=1e-12)
    assert fit.karamata_l == pytest.approx(1.0, rel=1e-9)
    assert fit.flatness == pytest.approx(0.0, abs=1e-12)
    assert fit.n_beyond == 500
    assert fit.converged


@pytest.mark.parametrize("karamata_l", [0.05, 1.0, 7.5])
def test_karamata_l_recovers_the_scale(karamata_l: float) -> None:
    sample = pareto_quantile_sample(n=400, alpha=3.0, karamata_l=karamata_l)
    fit = karamata_onset(sample, alpha=3.0)
    assert fit.karamata_l == pytest.approx(karamata_l, rel=1e-9)


def test_slowly_varying_is_flat_on_a_pareto_sample() -> None:
    curve = slowly_varying(pareto_quantile_sample(n=300, alpha=2.5), alpha=2.5)
    levels = [level for _, level in curve]
    assert max(levels) == pytest.approx(min(levels), rel=1e-12)
    assert [x for x, _ in curve] == sorted([x for x, _ in curve], reverse=True)


def test_onset_lands_where_the_splice_was_placed() -> None:
    """The onset is known by construction, not by running the function: the
    sample is Pareto above 2.0 and uniform below."""
    sample = spliced_sample(n=600, n_tail=200, alpha=3.0, onset=2.0)
    fit = karamata_onset(sample, alpha=3.0, tolerance=0.01, min_beyond=30)
    assert fit.onset == pytest.approx(2.0, rel=0.05)
    assert fit.n_beyond == pytest.approx(200, abs=5)


def test_a_looser_tolerance_never_yields_a_deeper_onset() -> None:
    """Monotonicity in ``tolerance``: admitting more wobble can only push the
    onset further into the body, never further out into the tail."""
    sample = spliced_sample(n=600, n_tail=200, alpha=3.0, onset=2.0)
    tight = karamata_onset(sample, alpha=3.0, tolerance=0.01)
    loose = karamata_onset(sample, alpha=3.0, tolerance=0.30)
    assert loose.onset <= tight.onset
    assert loose.n_beyond >= tight.n_beyond


def test_fixed_point_reports_whether_it_settled() -> None:
    """With ``alpha=None`` the circularity is real -- ``L`` needs alpha, alpha
    is estimated beyond the onset -- so the result carries ``converged``
    instead of presenting a solved number as if nothing had been solved."""
    sample = pareto_quantile_sample(n=800, alpha=3.0)
    fit = karamata_onset(sample, alpha=None)
    assert fit.converged
    assert fit.alpha == pytest.approx(3.0, rel=0.15)


def test_supplied_alpha_is_not_solved_for() -> None:
    sample = pareto_quantile_sample(n=200, alpha=3.0)
    fit = karamata_onset(sample, alpha=2.0)
    assert fit.alpha == 2.0
    assert fit.converged


def test_onset_gates_a_body_plateau_end_to_end() -> None:
    """The two modules together: a Hill plateau found below the Karamata onset
    is refused. This is the wiring that stops a body slope being published as
    a tail index."""
    sample = spliced_sample(n=600, n_tail=200, alpha=3.0, onset=2.0)
    fit = karamata_onset(sample, alpha=3.0, tolerance=0.01)
    plot = hill_plot(sample, k_min=20, k_max=400)
    ungated = stable_k(plot, window=20, tolerance=0.05)
    gated = stable_k(plot, window=20, tolerance=0.05, min_threshold=fit.onset * 50)
    assert ungated is not None
    assert gated is None


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"tolerance": 0.0}, "tolerance must be positive"),
        ({"min_beyond": 10_000}, "need at least"),
    ],
)
def test_refusals(kwargs: dict[str, float], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        karamata_onset(pareto_quantile_sample(n=100, alpha=3.0), alpha=3.0, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("sample", "alpha", "match"),
    [
        ([3.0, 2.0, 1.0], 0.0, "alpha must be positive"),
        ([], 3.0, "non-empty sample"),
        ([3.0, 2.0, 0.0], 3.0, "strictly positive"),
    ],
)
def test_slowly_varying_guards(sample: list[float], alpha: float, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        slowly_varying(sample, alpha=alpha)


def test_fixed_point_reports_when_it_does_not_settle() -> None:
    """``converged=False`` must be reachable, or the flag is decoration. One
    iteration is never enough for the alpha/onset map to settle."""
    from tail_lab.research.surface import karamata as karamata_module

    sample = spliced_sample(n=600, n_tail=200, alpha=3.0, onset=2.0)
    original = karamata_module.MAX_FIXED_POINT_ITERATIONS
    try:
        karamata_module.MAX_FIXED_POINT_ITERATIONS = 1
        assert not karamata_onset(sample, alpha=None).converged
    finally:
        karamata_module.MAX_FIXED_POINT_ITERATIONS = original
    assert karamata_onset(sample, alpha=None).converged


def test_is_flat_is_true_when_a_genuine_flat_region_exists() -> None:
    sample = pareto_quantile_sample(n=400, alpha=3.0)
    assert karamata_onset(sample, alpha=3.0).is_flat


def test_is_flat_is_false_when_the_floor_is_returned() -> None:
    """The bug this flag exists for, caught on the first real run.

    When even the top ``min_beyond`` observations fail the tolerance there is no
    Karamata region, and the search has nothing to return but its own floor. An
    earlier version handed that back as ``onset`` with no way to tell it apart
    from a measured one -- on real SPY down-moves it reported a 2.22% onset with
    a flatness of 0.61 against a tolerance of 0.05.
    """
    body_only = [1.0 + 0.5 * i for i in range(60)]  # linear, nothing like a power law
    fit = karamata_onset(body_only, alpha=3.0, tolerance=0.01, min_beyond=30)
    assert not fit.is_flat
    assert fit.flatness > 0.01
    assert fit.n_beyond == 30


def test_is_flat_is_reported_through_the_fixed_point_path_too() -> None:
    body_only = [1.0 + 0.5 * i for i in range(60)]
    assert not karamata_onset(body_only, alpha=None, tolerance=0.01, min_beyond=30).is_flat
