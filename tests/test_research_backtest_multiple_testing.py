"""Pinned cases for ``research.backtest.multiple_testing.benjamini_hochberg``
(``docs/END_STATE.md`` §4 Q1) -- see ``docs/STANDARDS.md``'s "numeric results
pinned against known cases": every expected value here is hand-computable
independently of the function under test."""

from __future__ import annotations

import pytest

from tail_lab.research.backtest.multiple_testing import benjamini_hochberg


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
