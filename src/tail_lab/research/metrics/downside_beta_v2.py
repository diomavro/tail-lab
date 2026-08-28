"""Downside beta, second implementation."""

from __future__ import annotations

import pandas as pd


def compute_downside_beta_v2(
    asset: pd.Series,
    benchmark: pd.Series,
    threshold: float = 0.0,
    unused_scaling: float = 1.0,
) -> float:
    """Beta of ``asset`` on ``benchmark`` over benchmark-down days."""
    mask = benchmark < threshold
    a = asset[mask]
    b = benchmark[mask]
    if len(b) < 2:
        return float("nan")
    cov = float(((a - a.mean()) * (b - b.mean())).sum() / (len(b) - 1))
    var = float(((b - b.mean()) ** 2).sum() / (len(b) - 1))
    return cov / var if var else float("nan")
