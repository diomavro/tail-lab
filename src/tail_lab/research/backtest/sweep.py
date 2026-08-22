"""The strike x tenor sweep — one asset's whole parameter grid, and its best cell.

Extracted from the API route so that :mod:`tail_lab.research.backtest.ranking`
can use it too. The grid itself lives here rather than in ``api/`` because
``research`` sits below ``api`` in the layer contract, and because "which
strikes and tenors this platform considers" is a research choice, not a
presentation one.

**Why the ranking needs this.** A fragility ranking that reports each name's
return at *one* strike/tenor answers a question nobody asked: the user picks
the name first and the parameters second. What they want to know is how good
each name gets when its parameters are chosen well — the **maximum annualized
return over the grid** — and then to land on exactly those parameters. So the
ranking runs the same sweep the heatmap does, and reports its argmax.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import pandas as pd
from pydantic import BaseModel

from tail_lab.research.backtest.put_roll import annualized_return, run_put_roll

#: The heatmap axes — one place, so the sweep endpoint, the ranking, and the
#: frontend grid all agree. Moneyness in % out-of-the-money, tenor in weeks.
SWEEP_MONEYNESS: tuple[float, ...] = (2, 4, 6, 8, 10, 12, 15, 18, 22)
SWEEP_TENORS_WEEKS: tuple[float, ...] = (1, 2, 4, 8, 12)


class SweepPoint(BaseModel):
    """One (moneyness, tenor) cell. ``roi_on_premium`` is the total over the
    window; ``annualized_return`` geometrically annualizes it — the value the
    heatmap colours by and the ranking maximizes."""

    moneyness_pct: float
    tenor_weeks: float
    roi_on_premium: float
    annualized_return: float
    n_cycles: int


def run_sweep(
    prices: pd.Series,
    iv_proxy: pd.Series,
    *,
    asset: str,
    as_of: dt.date,
    notional: float,
    years: float,
    moneyness_grid: Sequence[float] = SWEEP_MONEYNESS,
    tenor_grid: Sequence[float] = SWEEP_TENORS_WEEKS,
) -> list[SweepPoint]:
    """Every cell of the grid, rolled over one already-loaded price path.

    Pure, and deliberately takes the series rather than the store: the caller
    reads the lake **once** and sweeps many cells over it, instead of
    re-reading per cell. A tenor too long for the available window yields no
    cell rather than failing the whole grid, so a short history degrades to a
    smaller heatmap instead of an error.
    """
    points: list[SweepPoint] = []
    for tenor in tenor_grid:
        for moneyness in moneyness_grid:
            try:
                result = run_put_roll(
                    prices,
                    iv_proxy,
                    asset=asset,
                    as_of=as_of,
                    notional=notional,
                    moneyness_pct=moneyness,
                    tenor_weeks=tenor,
                    lookback_years=years,
                    # Nothing here plots a per-day curve; see run_put_roll's docstring.
                    include_curves=False,
                )
            except LookupError:
                continue
            points.append(
                SweepPoint(
                    moneyness_pct=moneyness,
                    tenor_weeks=tenor,
                    roi_on_premium=result.roi_on_premium,
                    annualized_return=annualized_return(result.roi_on_premium, years),
                    n_cycles=result.n_cycles,
                )
            )
    return points


def best_point(points: Sequence[SweepPoint]) -> SweepPoint | None:
    """The cell with the highest annualized return, or ``None`` for an empty grid.

    Ties break toward the **shallower strike and shorter tenor** — the cheaper,
    more liquid, more frequently-rolled contract. Two cells that earned the
    same return are not equally good in practice, and an arbitrary tie-break
    would make the ranking flicker between reloads.
    """
    if not points:
        return None
    return min(
        points,
        key=lambda p: (-p.annualized_return, p.moneyness_pct, p.tenor_weeks),
    )
