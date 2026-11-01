"""Pinned cases for ``research.backtest.multiple_testing.benjamini_hochberg``
(``docs/END_STATE.md`` §4 Q1) -- see ``docs/STANDARDS.md``'s "numeric results
pinned against known cases": every expected value here is hand-computable
independently of the function under test."""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tail_lab.research.backtest.multiple_testing import (
    benjamini_hochberg,
    benjamini_hochberg_adjusted,
)


def test_benjamini_hochberg_textbook_case() -> None:
    """Five p-values, alpha=0.05, m=5. Sorted ascending with BH critical
    values (k/5)*0.05:

        rank  p      critical  p <= critical
        1     0.005  0.01      True
        2     0.01   0.02      True
        3     0.03   0.03      True
        4     0.04   0.04      True
        5     0.5    0.05      False

    The largest rank where the line holds is 4, so ranks 1-4 are significant
    and rank 5 is not -- independent of each p-value's own size relative to
    alpha (rank-5's own p=0.5 is obviously not < 0.05 either, but rank-3's
    p=0.03 *is* < 0.05 on its own and would pass an uncorrected test too;
    the point of this case is ranks 1-4 surviving together as a block, not
    each one individually clearing 0.05)."""
    p_values = [0.01, 0.04, 0.03, 0.005, 0.5]  # unsorted, in "table row" order
    result = benjamini_hochberg(p_values, alpha=0.05)
    assert result == [True, True, True, True, False]


def test_benjamini_hochberg_empty_input() -> None:
    assert benjamini_hochberg([]) == []


def test_benjamini_hochberg_nothing_survives_when_all_p_values_are_large() -> None:
    assert benjamini_hochberg([0.9, 0.8, 0.7, 0.6], alpha=0.05) == [False, False, False, False]


def test_benjamini_hochberg_everything_survives_when_all_p_values_are_tiny() -> None:
    assert benjamini_hochberg([1e-6, 1e-7, 1e-8], alpha=0.05) == [True, True, True]


def test_benjamini_hochberg_is_stricter_than_the_uncorrected_hurdle() -> None:
    """The whole point (`docs/AGENT_TODO.md`'s "pure noise through the grid"
    ask): four candidates, two of which clear the nominal ``p < 0.05`` hurdle
    by chance -- exactly what a handful of true nulls does over many draws.
    An uncorrected reading calls both of those "significant"; BH's per-rank
    critical values (0.0125, 0.025, 0.0375, 0.05 at m=4) are never cleared by
    any of the four sorted p-values (0.03 > 0.0125, 0.04 > 0.025, 0.6 > 0.0375,
    0.7 > 0.05), so the corrected reading calls the whole grid a non-result."""
    p_values = [0.03, 0.04, 0.6, 0.7]
    uncorrected = [p < 0.05 for p in p_values]
    corrected = benjamini_hochberg(p_values, alpha=0.05)
    assert uncorrected == [True, True, False, False]
    assert corrected == [False, False, False, False]


@pytest.mark.parametrize("bad_alpha", [0.0, -0.1, 1.1])
def test_benjamini_hochberg_rejects_alpha_out_of_range(bad_alpha: float) -> None:
    with pytest.raises(ValueError, match="alpha"):
        benjamini_hochberg([0.01, 0.02], alpha=bad_alpha)


@pytest.mark.parametrize("bad_p", [-0.01, 1.5])
def test_benjamini_hochberg_rejects_p_value_out_of_range(bad_p: float) -> None:
    with pytest.raises(ValueError, match="p-value"):
        benjamini_hochberg([0.01, bad_p])


def test_adjusted_p_values_match_a_hand_computed_textbook_case() -> None:
    """Sorted p = .01, .04, .03, .005 (m = 4): step-up q from the largest:
    q4 = .04*4/4 = .04; q3 = min(.04, .03*4/3 = .04) = .04;
    q2 = min(.04, .01*4/2 = .02) = .02; q1 = min(.02, .005*4/1 = .02) = .02."""
    q = benjamini_hochberg_adjusted([0.01, 0.04, 0.03, 0.005])
    assert q == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert benjamini_hochberg_adjusted([]) == []
    assert benjamini_hochberg_adjusted([0.9, 0.95]) == pytest.approx([0.95, 0.95])


def test_adjusted_p_values_agree_with_the_rejection_rule() -> None:
    ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
    q = benjamini_hochberg_adjusted(ps)
    assert [x <= 0.05 for x in q] == benjamini_hochberg(ps, alpha=0.05)
    assert all(0.0 <= x <= 1.0 for x in q)


def test_adjusted_p_values_refuse_out_of_range_input() -> None:
    with pytest.raises(ValueError, match="p-values"):
        benjamini_hochberg_adjusted([0.2, 1.2])


def _textbook_step_up(ps: list[float], alpha: float) -> list[bool]:
    """Benjamini-Hochberg as the paper states it, written independently of the
    implementation: reject every p at or below the largest rank k with
    p_(k) <= k / m * alpha."""
    m = len(ps)
    ordered = sorted(ps)
    ks = [k for k in range(1, m + 1) if ordered[k - 1] <= k / m * alpha]
    cutoff = ordered[max(ks) - 1] if ks else -1.0
    return [p <= cutoff for p in ps]


@settings(max_examples=300, deadline=None)
@given(
    ps=st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=1, max_size=12),
    alpha=st.floats(min_value=0.01, max_value=0.5),
)
def test_the_single_step_up_implementation_matches_the_textbook_rule(
    ps: list[float], alpha: float
) -> None:
    """``benjamini_hochberg`` is ``q <= alpha`` over the adjusted p-values (one
    implementation, so the flag and the q-value cannot disagree). Away from
    the float knife-edge it must equal the textbook rule; ON a boundary the two
    float expressions may differ by an ulp, and that case is excluded rather
    than asserted either way."""
    m = len(ps)
    ordered = sorted(ps)
    assume(all(abs(p - (k / m) * alpha) > 1e-9 for k, p in enumerate(ordered, start=1)))
    got = benjamini_hochberg(ps, alpha=alpha)
    assert got == _textbook_step_up(ps, alpha)
    assert got == [q <= alpha for q in benjamini_hochberg_adjusted(ps)]
