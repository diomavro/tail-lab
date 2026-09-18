"""Pinned tests for the Karamata onset.

Both references are constructions whose answer is known *before* the function
runs: an exact Pareto sample, where ``L`` is constant by algebra, and a splice
whose onset is placed by hand.
"""

from __future__ import annotations

import math

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


def test_the_onset_gate_keeps_a_genuine_tail_plateau() -> None:
    """The two modules wired together, on a sample whose tail is real.

    Gating on the **measured onset** -- not on some multiple of it -- must leave
    a genuine tail plateau standing, and the surviving plateau's threshold must
    sit at or beyond that onset. An earlier version of this test passed
    ``min_threshold=fit.onset * 50``, which only showed that an unreachable
    threshold rejects everything; it demonstrated nothing about the gate.
    """
    sample = spliced_sample(n=600, n_tail=200, alpha=3.0, onset=2.0)
    fit = karamata_onset(sample, alpha=3.0, tolerance=0.01)
    assert fit.is_flat
    plot = hill_plot(sample, k_min=20, k_max=400)

    gated = stable_k(plot, window=20, tolerance=0.05, min_threshold=fit.onset)
    assert gated is not None
    assert gated.threshold >= fit.onset
    assert gated.alpha == pytest.approx(3.0, rel=0.2)


def test_the_onset_gate_rejects_a_plateau_that_lies_in_the_body() -> None:
    """The other direction, on a sample built so the only plateau is a body
    slope: exactly Pareto over the lower range, and a geometric block at the
    top whose Hill estimate drifts with ``k`` rather than settling.

    Ungated, ``stable_k`` returns the body plateau. Gated at the splice boundary
    -- which is where the Karamata onset lies by construction here -- it returns
    nothing, which is the behaviour that stops a body slope being published as a
    tail index. (The gate is fed ``min(top)`` rather than a fitted onset on
    purpose: the point under test is ``stable_k``'s threshold logic, and
    ``test_the_onset_gate_keeps_a_genuine_tail_plateau`` covers the wiring to a
    measured onset.)
    """
    body = pareto_quantile_sample(n=400, alpha=3.0, karamata_l=1.0)
    top = [max(body) * math.exp(0.35 * (40 - i)) for i in range(40)]
    sample = top + body

    plot = hill_plot(sample, k_min=20, k_max=380)
    ungated = stable_k(plot, window=20, tolerance=0.05)
    assert ungated is not None, "the construction must contain a body plateau to reject"

    gated = stable_k(plot, window=20, tolerance=0.05, min_threshold=min(top))
    assert gated is None
    assert ungated.threshold < min(top)


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
        ([3.0, 2.0, 1.0], 0.0, "alpha must be a finite positive number"),
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


def test_the_onset_search_does_not_stop_at_the_first_violation() -> None:
    """``_flatness`` is ``(max - min) / mean``: ``max - min`` is non-decreasing
    in the prefix length but the **mean** can rise faster, so flatness is not
    monotone and can dip back under tolerance after exceeding it.

    On ``L = [0.92]*3 + [1.00]*17`` at tolerance 0.085 the flatness is 0.000 at
    3 points, 0.0851 at 4 (over), then 0.0840 at 5 and 0.0810 at 20 (under
    again). A search that stopped at the first violation put the onset at
    ``x=6.44`` where the answer is ``x=1.05`` -- 6.1x too far out, on 3
    observations instead of 20 -- and reported ``is_flat=True``, so every
    downstream consumer took it as a measurement.
    """
    levels = [0.92] * 3 + [1.00] * 17
    n, alpha = len(levels), 1.0
    # Invert L_i = (i/(n+1)) * x_i**alpha to get a sample with exactly these levels.
    sample = [(levels[i - 1] * (n + 1) / i) ** (1.0 / alpha) for i in range(1, n + 1)]

    fit = karamata_onset(sample, alpha=alpha, tolerance=0.085, min_beyond=3)
    assert fit.n_beyond == 20
    assert fit.onset == pytest.approx(1.05, rel=1e-9)
    assert fit.is_flat


@pytest.mark.parametrize("min_beyond", [0, -5])
def test_a_non_positive_min_beyond_is_refused(min_beyond: int) -> None:
    """``levels[:0]`` divides by zero and ``levels[:-5]`` gives a negative mean
    whose fractional power is **complex** -- silently, in a float-typed field.
    """
    sample = pareto_quantile_sample(n=100, alpha=3.0)
    with pytest.raises(ValueError, match="min_beyond must be at least 1"):
        karamata_onset(sample, alpha=2.0, min_beyond=min_beyond)


def test_slowly_varying_refuses_a_non_finite_observation() -> None:
    """``nan <= 0.0`` is False, so a NaN passes the positivity check; and
    ``sorted`` around a NaN is order-undefined, so ``ordered[-1]`` is not
    reliably the minimum either."""
    with pytest.raises(ValueError, match="finite observations"):
        slowly_varying([1.0, float("nan"), 2.0], alpha=3.0)


def test_a_deep_observation_that_underflows_does_not_refuse_the_whole_sample() -> None:
    """Only the levels the search CONSUMES need to be positive.

    ``_onset_for_alpha`` divides by the mean of a prefix, and every prefix
    contains the top ``min_beyond`` -- so one plausible deep loss (1e-6 against a
    3% top) that underflows at a large alpha never enters any reported number.
    An earlier version validated the whole curve and refused such samples
    outright, which made ``scripts/tail_alpha.py`` traceback where it had
    previously printed its honest "nothing here is flat" verdict.
    """
    sample = [0.03 * (1 - 4e-4 * i) for i in range(598)] + [1e-6]
    levels = [level for _, level in slowly_varying(sample, alpha=82.0)]
    assert levels[0] > 0.0, "the consumed prefix must be computable"
    assert levels[-1] == 0.0, "the deepest level must underflow, or this tests nothing"

    fit = karamata_onset(sample, alpha=None)
    assert not fit.is_flat
    assert fit.onset > 0.0


def test_an_underflowing_top_prefix_is_still_refused() -> None:
    """The other side: when the observations the search actually reads underflow,
    there is nothing to divide by and refusing is right."""
    with pytest.raises(ValueError, match="underflows to zero across the top"):
        karamata_onset([1e-5 * (1 + 0.01 * i) for i in range(60)], alpha=70.0, min_beyond=10)


def test_slowly_varying_converts_an_overflow_into_a_value_error() -> None:
    """``float.__pow__`` RAISES on overflow rather than returning inf, and this
    module's contract is ``ValueError``."""
    with pytest.raises(ValueError, match="overflows on this sample"):
        slowly_varying([1e5 * (1 + 0.01 * i) for i in range(60)], alpha=100.0)
