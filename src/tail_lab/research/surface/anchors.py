"""The implied side of the Surface at three anchors, with their dispersion
(``AGENT_TODO.md`` P4; ``docs/adr/0026``).

One implied alpha is never evidence of a power law: flat-vol Black-Scholes is
accepted at alpha ~2.9 (90 days, 35 % vol) and betrays itself only by its alpha
climbing with anchor depth, where a true power law's is the same at every
anchor. So the Surface reads three anchors -- the caller's moneyness and two
and four points deeper -- and reports ``max - min`` of the accepted alphas
beside them. Each reading also carries the P3 ceiling, built from the smile's
slope at that anchor (``smile.py``); when there is no slope there is no
ceiling, never a ceiling computed from 0.0.

Anchors are picked by FIXED moneyness, which carries the regime confound of
``docs/PRIOR_ART.md`` §1; callers say so (``CLAUDE.md``). Refusals are values:
an anchor whose quote is not a price is listed as a refused reading, never
dropped and never a 500.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pandas as pd

from tail_lab.research.surface.alpha_bound import AlphaCeiling, alpha_upper_bound
from tail_lab.research.surface.implied_alpha import ImpliedAlphaFit, fit_implied_alpha
from tail_lab.research.surface.ladder import Anchor
from tail_lab.research.surface.smile import SmileSlope, smile_slope

#: Percentage points deeper than the caller's moneyness for the other anchors.
DEPTH_OFFSETS_PCT: Final = (0.0, 2.0, 4.0)
MIN_ACCEPTED_FOR_DISPERSION: Final = 2


@dataclass(frozen=True)
class AnchorReading:
    """One anchor: its fit (``None`` when the quote was not a price, with
    ``refusal``), the smile slope there, and the ceiling or why there is none."""

    strike: float
    fit: ImpliedAlphaFit | None
    refusal: str | None
    smile: SmileSlope | None
    ceiling: AlphaCeiling | None
    ceiling_reason: str | None


@dataclass(frozen=True)
class AnchorReadings:
    readings: tuple[AnchorReading, ...]
    dispersion: float | None
    dispersion_reason: str | None


def _nearest_strike(strikes: list[float], target: float, spot: float) -> float | None:
    below = [k for k in strikes if k < spot]
    return min(below, key=lambda k: abs(k - target)) if below else None


def _read_anchor(
    row: pd.Series, quotes: pd.DataFrame, *, underlying: str, spot: float, r: float, q: float
) -> AnchorReading:
    strike = float(row["strike"])
    try:
        anchor = Anchor(
            underlying,
            pd.Timestamp(row["quote_date"]).date(),
            pd.Timestamp(row["expiration"]).date(),
            spot,
            strike,
            (float(row["bid"]) + float(row["ask"])) / 2.0,
            float(row["bid"]),
            float(row["ask"]),
        )
    except ValueError as exc:
        return AnchorReading(strike, None, str(exc), None, None, None)
    fit = fit_implied_alpha(anchor, quotes)
    ivs = quotes["iv"].tolist() if "iv" in quotes.columns else [None] * len(quotes)
    smile = smile_slope(quotes["strike"].tolist(), [None if pd.isna(v) else v for v in ivs], strike)
    if smile.slope is None:
        return AnchorReading(strike, fit, None, smile, None, smile.refusal)
    ceiling = alpha_upper_bound(anchor, smile_slope=smile.slope, r=r, q=q)
    return AnchorReading(strike, fit, None, smile, ceiling, None)


def read_anchors(
    quotes: pd.DataFrame,
    *,
    underlying: str,
    spot: float,
    moneyness_pct: float,
    r: float,
    q: float,
) -> AnchorReadings:
    """Fit and bound the implied alpha at the three anchors on ``quotes``.

    ``quotes`` is one session and expiry (columns as ``fit_implied_alpha``
    expects, plus ``iv`` when the smile slope is wanted). Each anchor is the
    listed strike below spot nearest ``spot * (1 - m / 100)``; coincident picks
    on a coarse grid collapse to one reading, because two fits on one strike
    would show a dispersion of exactly 0 -- the value that marks a power law.
    """
    strikes = sorted(set(float(k) for k in quotes["strike"]))
    picked = {
        _nearest_strike(strikes, spot * (1 - (moneyness_pct + offset) / 100.0), spot)
        for offset in DEPTH_OFFSETS_PCT
    } - {None}
    readings = tuple(
        _read_anchor(row, quotes, underlying=underlying, spot=spot, r=r, q=q)
        for _, row in quotes[quotes["strike"].isin(picked)]
        .drop_duplicates("strike")
        .sort_values("strike", ascending=False)
        .iterrows()
    )
    alphas = [x.fit.alpha for x in readings if x.fit is not None and x.fit.alpha is not None]
    if len(readings) < MIN_ACCEPTED_FOR_DISPERSION:
        return AnchorReadings(readings, None, "fewer than two distinct anchor strikes on this grid")
    if len(alphas) < MIN_ACCEPTED_FOR_DISPERSION:
        return AnchorReadings(readings, None, "fewer than two anchors accepted")
    return AnchorReadings(readings, max(alphas) - min(alphas), None)


__all__ = ["AnchorReading", "AnchorReadings", "read_anchors"]
