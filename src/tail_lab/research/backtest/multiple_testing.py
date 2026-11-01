"""Benjamini-Hochberg false-discovery-rate correction (``docs/END_STATE.md``
§4 Q1) -- the bake-off in ``metric_screen.py`` tests seven candidate screens
against the same historical window, and the conventional ``p < 0.05`` hurdle
is exactly what Harvey, Liu & Zhu (*...and the Cross-Section of Expected
Returns*, Review of Financial Studies 29(1), 2016) showed names a winner far
more often than it should once a literature has tested this many candidates.

A leaf module -- no dependency on ``metric_screen.py`` or the lake -- so it is
testable against hand-computable textbook cases independent of any backtest
machinery, matching ``growth.py``'s and ``sizing.py``'s precedent of pulling
a pure calculation out of the module that consumes it.
"""

from __future__ import annotations

from collections.abc import Sequence


def benjamini_hochberg(p_values: Sequence[float], *, alpha: float = 0.05) -> list[bool]:
    """Which of ``p_values`` are significant at false-discovery-rate ``alpha``,
    via Benjamini & Hochberg (1995)'s step-up procedure.

    Sort ascending as p_(1) <= ... <= p_(m); find the largest rank k with
    p_(k) <= (k / m) * alpha; reject the null (mark significant) for every
    hypothesis at or below rank k -- everything past it stays non-significant
    even if its own p-value happens to be small, because the procedure looks
    for the *last* rank the line still holds at, not each point independently.

    Returns one bool per input, in the *original* order, so a caller
    reporting a table does not have to re-sort to align results back to rows.
    An empty input returns an empty list.

    Raises:
        ValueError: ``alpha`` is not in ``(0, 1]``, or any p-value is outside
            ``[0, 1]``.
    """
    if not p_values:
        return []
    if not (0.0 < alpha <= 1.0):
        raise ValueError("alpha must be in (0, 1]")
    if any(not (0.0 <= p <= 1.0) for p in p_values):
        raise ValueError("p-values must be in [0, 1]")

    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    threshold_rank = 0
    for rank, idx in enumerate(order, start=1):
        if p_values[idx] <= (rank / m) * alpha:
            threshold_rank = rank
    significant = [False] * m
    for rank, idx in enumerate(order, start=1):
        if rank <= threshold_rank:
            significant[idx] = True
    return significant


def benjamini_hochberg_adjusted(p_values: Sequence[float]) -> list[float]:
    """BH-adjusted p-values (q-values), in the original order: the smallest
    FDR level at which each hypothesis would be called significant. Step-up
    from the largest p: ``q_(i) = min(1, min_{j >= i} p_(j) * m / j)``.

    For DISPLAY only. Significance is always decided by
    :func:`benjamini_hochberg`, never by ``q <= alpha``: the two forms round
    differently, so at an exact boundary (e.g. ``[0.0, 0.0, 0.05]`` at 0.05,
    where q = 0.05000000000000001) a q-value can read a hair above alpha for a
    hypothesis that is significant.

    Raises:
        ValueError: any p-value is outside ``[0, 1]``.
    """
    if any(not (0.0 <= p <= 1.0) for p in p_values):
        raise ValueError("p-values must be in [0, 1]")
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        running = min(running, p_values[idx] * m / rank)
        adjusted[idx] = running
    return adjusted
