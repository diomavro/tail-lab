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
#: Deliberately reaches past what the pricer can handle: seeing the deep tail
#: is the point of a tail-hedge lab, and hiding it would be its own kind of lie.
#: What the depth costs is handled by MODEL_PRICED_MAX_MONEYNESS_PCT below.
SWEEP_MONEYNESS: tuple[float, ...] = (2, 4, 6, 8, 10, 12, 15, 18, 22, 26, 30)
SWEEP_TENORS_WEEKS: tuple[float, ...] = (1, 2, 4, 8, 12)

#: The strike depth past which this platform's premium is not a price.
#:
#: ``docs/MODEL_RESIDUAL.md`` priced the same puts twice over real historical
#: quotes — once by our Black-Scholes-at-VIX model, once by the market — and
#: measured the median market/model premium ratio by depth:
#:
#:     5% OTM   1.42x        15% OTM     180x
#:     10% OTM  7.38x        20% OTM  21,663x
#:
#: Past ~10% the flat-vol model does not merely misprice the option, it reports
#: that it is nearly free. Because every backtest here fixes the premium
#: *budget* rather than the contract count, a premium rounded to nothing buys
#: an absurd number of contracts and inflates any payoff by the same factor. So
#: 10% is where the ratio is still within one order of magnitude, and it is the
#: deepest strike whose return is a measurement rather than an artefact.
#:
#: This is NOT ``accuracy.applicability``. That asks "does the measured
#: residual of a published Cboe program describe this run" (a distance in
#: parameter space). This asks "is the model's premium a price at all at this
#: depth" (a property of the pricer). A run can be `indicative` and still
#: model-priced, or `direct` and — were the reference deeper — not.
#:
#: Raising this is not a config tweak: it is a claim about the pricer, and the
#: thing that would justify it is the skew-aware ``OptionPricer`` queued in
#: ``AGENT_TODO.md``, which has an exact calibration target (+2.2 vol points at
#: 5%, +7.2 at 10%, +18.2 at 20%). See ``docs/adr/0018``.
MODEL_PRICED_MAX_MONEYNESS_PCT: float = 10.0


def is_model_priced(moneyness_pct: float) -> bool:
    """Whether this platform's premium at ``moneyness_pct`` is a price rather
    than a rounding artefact. See MODEL_PRICED_MAX_MONEYNESS_PCT."""
    return moneyness_pct <= MODEL_PRICED_MAX_MONEYNESS_PCT


#: The strikes worth sweeping when only the argmax is wanted. Derived, never
#: hand-maintained: :func:`best_point` cannot return an unpriced cell, so for
#: the ranking every deeper column is computed only to be discarded — and
#: across a 70-name universe that is most of the ranking's runtime. The heatmap
#: still sweeps ``SWEEP_MONEYNESS`` in full, because a reader should see the
#: whole surface.
MODEL_PRICED_SWEEP_MONEYNESS: tuple[float, ...] = tuple(
    m for m in SWEEP_MONEYNESS if is_model_priced(m)
)


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
    realized_vol_proxy: pd.Series,
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
                    realized_vol_proxy,
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


def best_point(points: Sequence[SweepPoint], *, priced_only: bool = True) -> SweepPoint | None:
    """The best cell of a grid, or ``None`` if there is no eligible one.

    By default only cells the model can actually price are eligible
    (:data:`MODEL_PRICED_MAX_MONEYNESS_PCT`). This matters because the raw
    argmax is systematically drawn to the deepest strike on the grid: that is
    where the flat-vol premium rounds toward zero, so a fixed premium budget
    buys the most contracts and any payoff that lands is inflated the most. A
    headline picked that way advertises the pricer's blind spot rather than the
    name's convexity.

    ``priced_only=False`` gives the unbounded grid maximum, which the sweep
    endpoint still reports for display — the reader should see the whole
    surface. It is the *headline* that is bounded, not the picture.

    Ties break toward the **shallower strike and shorter tenor** — the cheaper,
    more liquid, more frequently-rolled contract. Two cells that earned the
    same return are not equally good in practice, and an arbitrary tie-break
    would make the ranking flicker between reloads.
    """
    eligible = (
        [p for p in points if is_model_priced(p.moneyness_pct)] if priced_only else list(points)
    )
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda p: (-p.annualized_return, p.moneyness_pct, p.tenor_weeks),
    )
