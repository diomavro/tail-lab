"""Summarise a backtest's return in annual terms.

PROBE (2026-09-04): this module exists to exercise the review -> fix ->
re-review -> merge loop end to end, specifically the ONE step that has never
run to completion -- the fixer pushing its commit with ``AGENT_FIX_TOKEN``.
The duplication below is deliberate and the reviewer is expected to find it.
Delete this file once the loop has been observed closing.
"""

from __future__ import annotations

__all__ = ["annualise_roi", "describe_roi"]


def annualise_roi(total_roi: float, years: float) -> float:
    """The constant yearly rate that compounds to ``total_roi`` over ``years``."""
    if years <= 0:
        return total_roi
    base = 1.0 + total_roi
    if base <= 0.0:
        return -1.0
    return float(base ** (1.0 / years) - 1.0)


def describe_roi(total_roi: float, years: float) -> str:
    """One line describing a backtest's annualised return."""
    rate = annualise_roi(total_roi, years)
    return f"{rate * 100:.1f}%/yr over {years:g}y (total {total_roi * 100:.1f}%)"
