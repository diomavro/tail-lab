"""Summarise a backtest's return in annual terms."""

from __future__ import annotations

__all__ = ["annualize_total_return", "summarise_growth"]


def annualize_total_return(total_roi: float, years: float) -> float:
    """The constant yearly rate that compounds to ``total_roi`` over ``years``."""
    if years <= 0:
        return total_roi
    base = 1.0 + total_roi
    if base <= 0.0:
        return -1.0
    return float(base ** (1.0 / years) - 1.0)


def summarise_growth(total_roi: float, years: float) -> str:
    """One line describing a backtest's annualised return."""
    rate = annualize_total_return(total_roi, years)
    return f"{rate * 100:.1f}%/yr over {years:g}y (total {total_roi * 100:.1f}%)"
