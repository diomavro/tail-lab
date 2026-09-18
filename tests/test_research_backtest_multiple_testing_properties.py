"""Hypothesis property tests for
``research.backtest.multiple_testing.benjamini_hochberg``
(STANDARDS.md "Property tests for numeric code"). Complements the pinned
known-answer cases in ``test_research_backtest_multiple_testing.py`` with
invariants that hold for *any* valid input, not just the hand-picked ones.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from tail_lab.research.backtest.multiple_testing import benjamini_hochberg

_p_value = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_alpha = st.floats(min_value=1e-3, max_value=1.0, allow_nan=False, allow_infinity=False)


@given(p_values=st.lists(_p_value, min_size=1, max_size=30), alpha=_alpha)
def test_result_length_matches_input_and_is_boolean(p_values: list[float], alpha: float) -> None:
    result = benjamini_hochberg(p_values, alpha=alpha)
    assert len(result) == len(p_values)
    assert all(isinstance(r, bool) for r in result)


@given(p_values=st.lists(_p_value, min_size=1, max_size=30), alpha=_alpha)
def test_every_significant_p_value_is_at_most_alpha(p_values: list[float], alpha: float) -> None:
    """BH can only ever be *stricter* than the raw hurdle: a hypothesis it
    marks significant must also clear the uncorrected ``p <= alpha`` bar,
    since its critical value ``(k/m)*alpha`` is never larger than ``alpha``
    itself."""
    result = benjamini_hochberg(p_values, alpha=alpha)
    for p, significant in zip(p_values, result, strict=True):
        if significant:
            assert p <= alpha


@given(p_values=st.lists(_p_value, min_size=1, max_size=30), alpha=_alpha)
def test_more_significant_hypotheses_at_a_looser_alpha(p_values: list[float], alpha: float) -> None:
    """Loosening the FDR level can only ever add significant hypotheses, never
    remove one -- every BH critical value ``(k/m)*alpha`` grows monotonically
    with ``alpha``, so nothing that cleared a stricter bar can fail a looser
    one."""
    looser = min(1.0, alpha * 2.0)
    strict_result = benjamini_hochberg(p_values, alpha=alpha)
    loose_result = benjamini_hochberg(p_values, alpha=looser)
    for strict_sig, loose_sig in zip(strict_result, loose_result, strict=True):
        if strict_sig:
            assert loose_sig


@given(p_values=st.lists(_p_value, min_size=1, max_size=30))
def test_all_zero_p_values_are_all_significant(p_values: list[float]) -> None:
    zeros = [0.0 for _ in p_values]
    assert benjamini_hochberg(zeros, alpha=0.05) == [True] * len(zeros)
