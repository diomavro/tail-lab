"""The realised side of the Surface: a name's own tail index, gated or refused.

This is the gate ``scripts/tail_alpha.py`` used to carry inline, lifted so the
Surface route and the script read one implementation instead of two copies
(``docs/adr/0026`` §7). A Hill plateau is only reported when a measured
Karamata onset says the sample has entered its power-law region, and the
plateau itself sits beyond that onset. Every other outcome is a refusal with a
reason -- a normal return value, never an exception.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tail_lab.research.surface.hill import HillEstimate, hill_plot, stable_k
from tail_lab.research.surface.karamata import KaramataFit, karamata_onset
from tail_lab.research.surface.returns import loss_magnitudes

# Trading rows per calendar year over calendar days per year: converts a
# horizon quoted in calendar days (an option's time to expiry) into OHLCV rows.
_TRADING_ROWS_PER_CALENDAR_DAY = 252 / 365


@dataclass(frozen=True)
class RealisedAlpha:
    """The gated realised tail index, or why there is none.

    ``plateau`` is set only when ``refusal`` is ``None``. ``fit`` is ``None``
    when no onset could be computed at all (fixed-point refusal, or no
    down-moves). ``stepped_prices`` is the horizon-stepped series that was
    fitted, so a caller can run the log-basis comparison on the same series.
    """

    fit: KaramataFit | None
    plateau: HillEstimate | None
    n_beyond: int
    refusal: str | None
    stepped_prices: pd.Series


def step_prices(prices: pd.Series, *, horizon_days: int) -> pd.Series:
    """Non-overlapping closes ``horizon_days`` *calendar* days apart.

    Rows are trading days, so the step is ``round(horizon_days * 252 / 365)``
    rows, at least 1. Taking every step-th close makes the arithmetic returns
    between them non-overlapping.
    """
    if horizon_days < 1:
        raise ValueError(f"horizon_days must be >= 1 calendar day, got {horizon_days}")
    step = max(1, round(horizon_days * _TRADING_ROWS_PER_CALENDAR_DAY))
    return prices.iloc[::step]


def _refused(stepped: pd.Series, reason: str, *, fit: KaramataFit | None = None) -> RealisedAlpha:
    return RealisedAlpha(
        fit=fit,
        plateau=None,
        n_beyond=fit.n_beyond if fit else 0,
        refusal=reason,
        stepped_prices=stepped,
    )


def gated_realised_alpha(prices: pd.Series, *, horizon_days: int = 1) -> RealisedAlpha:
    """Fit the realised tail index of ``prices`` at a calendar-day horizon.

    Refuses (``plateau is None``, ``refusal`` set) when: there is no down-move
    to fit; ``karamata_onset`` raises; its onset did not converge (a
    non-converged onset must not be reported); the sample is not flat to
    tolerance anywhere (``onset`` is then only the ``min_beyond`` floor, not a
    measurement); or there is no Hill plateau beyond the onset.
    """
    stepped = step_prices(prices, horizon_days=horizon_days)
    try:
        losses = [float(x) for x in loss_magnitudes(stepped)]
    except ValueError as exc:
        return _refused(stepped, f"no left tail to fit: {exc}")
    try:
        fit = karamata_onset(losses, alpha=None)
    except ValueError as exc:
        return _refused(stepped, f"no Karamata region to gate on: {exc}")
    if not fit.converged:
        return _refused(stepped, "the Karamata onset did not converge", fit=fit)
    if not fit.is_flat:
        return _refused(
            stepped,
            f"no stretch of this sample is flat to tolerance (best flatness "
            f"{fit.flatness:.3f} over {fit.n_beyond} observations)",
            fit=fit,
        )
    plateau = stable_k(hill_plot(losses), min_threshold=fit.onset)
    if plateau is None:
        return _refused(stepped, "no Hill plateau beyond the Karamata onset", fit=fit)
    return RealisedAlpha(
        fit=fit, plateau=plateau, n_beyond=fit.n_beyond, refusal=None, stepped_prices=stepped
    )
