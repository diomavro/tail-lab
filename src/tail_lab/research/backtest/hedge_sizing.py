"""How much of the book should carry the hedge -- including "none".

The Book's Rodman test asks whether some mix out-grows both ends. A desk needs
a different number: the hedge ratio ``w`` to hold, judged by ``docs/adr/0027``'s
criterion (self-financed, time-average growth, beats **no hedge**). This module
answers it per program and window, on the same monthly-rebalanced blend
:func:`~hedge_overlay.blend_nav` builds.

**Why it is cheap and exact.** A blend rebalanced to target at each month's
first close grows, over month ``m``, by ``w p_m + (1 - w) e_m`` -- the
program's and the index's gross returns over that segment. So log growth is

    g(w) = (1/T) * sum_m ln(w p_m + (1 - w) e_m)

a sum of logs of functions linear in ``w``: **concave**, with one maximum on
``[0, 1]``. That makes a golden-section search exact to tolerance, and lets
"drop one month" be a subtraction rather than a re-simulation.

**Units.** Every growth figure and margin served is in CAGR units
(``e^g - 1``), as :data:`MIN_MARGIN` and ``WeightPoint.cagr`` are. CAGR is a
monotone transform of ``g``, so the argmax carries over; the "half keeps about
three quarters of the gain" line is exact only for a quadratic ``g``, which is
why the measured share is served beside it.

**The recommendation.** ``w*`` is the base leg's growth-optimal (full-Kelly)
ratio. The gate is ``g(w*) - g(0) > MIN_MARGIN`` -- strict, as
:func:`~hedge_overlay.outcome` is -- on **both** legs (the smaller margin
binds). Failing it, the answer is **0**, with the reason and margin named.
Passing it with ``w*`` inside ``(0, 1)``, the answer is ``w*/2``: one history
estimates ``w*``, and full Kelly is notoriously sensitive to that error. At
the cap ``w* = 1`` the program dominates the index at every mix; the answer
is 0.5, and it is not called half-Kelly. Degraded inputs (no measured
dividends, no T-bills) withhold the recommendation -- ``None``, with why --
rather than gate on a partial leg set.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel
from scipy.optimize import brentq

from tail_lab.research.backtest.index_replication import DAYS_PER_YEAR
from tail_lab.research.optimise import golden_section

#: A margin within this of zero, either side, is too close to call: 1bp/yr of
#: CAGR. Shared with the Rodman verdict (``hedge_overlay.outcome``).
MIN_MARGIN = 1e-4

#: Golden-section tolerance on ``w``: far finer than any ratio a desk holds.
W_TOL = 1e-6

#: ``w*`` at or above this is "at the cap" (the program dominates).
_CAP = 1.0 - 1e-4

#: Months listed as moving ``w*`` most when one is left out.
DECISIVE_MONTHS = 3

#: Points on the served ``g(w)`` curve, 0 to 1 inclusive.
CURVE_POINTS = 41


@dataclass(frozen=True)
class Segments:
    """Gross returns of the program (``p``) and the index (``e``) over each
    rebalancing segment, and the span they cover in years."""

    p: np.ndarray
    e: np.ndarray
    starts: tuple[dt.date, ...]
    years: float

    def log_sum(self, w: float, keep: np.ndarray | None = None) -> float:
        mix = w * self.p + (1.0 - w) * self.e
        terms = np.log(mix)
        return float(terms.sum() if keep is None else terms[keep].sum())

    def cagr(self, w: float) -> float:
        return math.expm1(self.log_sum(w) / self.years)


def rebalance_points(index: pd.DatetimeIndex) -> list[int]:
    """Positions of the first date and of each new month's first date."""
    months = index.to_period("M")
    return [0, *(np.flatnonzero(months[1:] != months[:-1]) + 1).tolist()]


def segments(program: pd.Series, equity: pd.Series) -> Segments:
    """The per-segment gross returns of the two legs, on ``blend_nav``'s own
    rebalancing rule (each segment runs from one reset close to the next,
    the last to the final date)."""
    if not program.index.equals(equity.index):
        raise ValueError("program and equity must share one date index")
    index = pd.DatetimeIndex(program.index)
    resets = rebalance_points(index)
    ends = [*resets[1:], len(index) - 1]
    pairs = [(s, e) for s, e in zip(resets, ends, strict=True) if e > s]
    prog = program.to_numpy(dtype=float)
    eq = equity.to_numpy(dtype=float)
    return Segments(
        p=np.array([prog[e] / prog[s] for s, e in pairs]),
        e=np.array([eq[e] / eq[s] for s, e in pairs]),
        starts=tuple(index[s].date() for s, _ in pairs),
        years=(index[-1] - index[0]).days / DAYS_PER_YEAR,
    )


def golden_max(f: Callable[[float], float], lo: float = 0.0, hi: float = 1.0) -> float:
    """The maximiser of a unimodal ``f`` on ``[lo, hi]`` to :data:`W_TOL`.

    The ends are checked too, so a maximum at a bound is returned exactly
    (golden section alone would stop within the tolerance of it)."""
    mid = golden_section(f, lo, hi, W_TOL, maximise=True)
    return max((lo, mid, hi), key=f)


def w_star(seg: Segments, keep: np.ndarray | None = None) -> float:
    """Growth-optimal ratio on ``[0, 1]`` (full Kelly), over ``keep``'s months."""
    return golden_max(lambda w: seg.log_sum(w, keep))


class HalfWindow(BaseModel):
    """``w*`` estimated on one half of the window's months."""

    start: dt.date
    end: dt.date
    w_star: float


class DecisiveMonth(BaseModel):
    """A month whose removal moves ``w*`` most (leave-one-month-out)."""

    month: str  # "YYYY-MM", the segment's first month
    w_star_without: float
    delta: float  # w_star_without - w_star


class CurvePoint(BaseModel):
    weight: float
    cagr: float


BreakEvenStatus = Literal["found", "beyond_1", "none"]


class SizingAnswer(BaseModel):
    """How much of the book to hold in one program over one window."""

    #: The ratio to hold: ``None`` when withheld (degraded inputs), ``0.0``
    #: when the hedge earns no place.
    recommended_ratio: float | None
    reason: str
    #: ``w*`` is at the cap of 1 (:data:`_CAP`): the program dominates the
    #: index at every mix, so 50% is a cap, never "half-Kelly". Served so the
    #: page keys its wording on the same test, whatever size is held.
    at_cap: bool
    #: Best ratio on the 0.1 grid, and the refined full-Kelly ``w*``.
    w_star_grid: float
    w_star: float
    #: CAGR at no hedge, at ``w*`` and at ``w*/2`` (base leg).
    g0: float
    g_star: float
    g_half: float
    #: Share of the gain over no hedge kept at ``w*/2`` (``None`` when there
    #: is no gain to keep).
    half_keeps: float | None
    #: The ratio past ``w*`` where growth falls back to ``g0``.
    break_even: float | None
    break_even_status: BreakEvenStatus
    #: The mean-variance ("naive") Kelly ratio, unclamped: the variance
    #: approximation a skewed payoff breaks. ``None`` when undefined.
    naive_kelly: float | None
    #: ``g(w*) - g(0)`` on each leg, in CAGR units: "base", "conservative",
    #: or "assumed" in the fallback.
    margin_by_leg: dict[str, float]
    #: Set when the 0.1 grid and the refined ``w*`` fall on opposite sides of
    #: the 1bp bar.
    grid_note: str | None
    halves: list[HalfWindow]
    decisive_months: list[DecisiveMonth]
    curve: list[CurvePoint]


def _bp(x: float) -> str:
    return f"{x * 1e4:+.2f}bp/yr"


def _break_even(seg: Segments, w: float) -> tuple[float | None, BreakEvenStatus]:
    if w <= W_TOL:
        return None, "none"
    g0 = seg.log_sum(0.0)
    if seg.log_sum(1.0) >= g0:
        return None, "beyond_1"
    return float(brentq(lambda x: seg.log_sum(x) - g0, w, 1.0, xtol=W_TOL)), "found"


def naive_kelly(seg: Segments) -> float | None:
    """``(mean(d) - cov(x, d)) / var(d)``, ``d = p - e``, ``x = e - 1``: the
    maximiser of the mean-variance approximation ``E - Var/2`` of monthly
    growth. Unclamped, so a reader sees how far it strays from ``w*``."""
    d = seg.p - seg.e
    if len(d) < 2:
        return None
    var = float(np.var(d, ddof=1))
    if var <= 0.0:
        return None
    cov = float(np.cov(seg.e - 1.0, d, ddof=1)[0, 1])
    return (float(d.mean()) - cov) / var


def stability(seg: Segments, w: float) -> tuple[list[HalfWindow], list[DecisiveMonth]]:
    """``w*`` on each half of the months, and the months whose removal moves
    it most (leave-one-month-out; ties and moves under the tolerance are not
    listed)."""
    n = len(seg.p)
    halves: list[HalfWindow] = []
    if n >= 2:
        mid = n // 2
        for lo, hi in ((0, mid), (mid, n)):
            keep = np.zeros(n, dtype=bool)
            keep[lo:hi] = True
            halves.append(
                HalfWindow(
                    start=seg.starts[lo],
                    end=seg.starts[hi - 1],
                    w_star=w_star(seg, keep),
                )
            )
    moves: list[DecisiveMonth] = []
    for m in range(n):
        keep = np.ones(n, dtype=bool)
        keep[m] = False
        without = w_star(seg, keep)
        if abs(without - w) > 10 * W_TOL:
            moves.append(
                DecisiveMonth(
                    month=seg.starts[m].strftime("%Y-%m"), w_star_without=without, delta=without - w
                )
            )
    moves.sort(key=lambda d: -abs(d.delta))
    return halves, moves[:DECISIVE_MONTHS]


@dataclass(frozen=True)
class SizingInputs:
    """The window's base-leg segments, the conservative leg's (``None`` when
    it cannot be built), the grid best, and why sizing is withheld (``None``
    when it is not)."""

    base: Segments
    conservative: Segments | None
    grid_best: float
    withheld: str | None
    base_leg_name: str = "base"


def _recommend(
    inputs: SizingInputs, w: float, margins: dict[str, float]
) -> tuple[float | None, str]:
    if inputs.withheld is not None:
        return None, inputs.withheld
    if inputs.conservative is None or any(math.isnan(m) for m in margins.values()):
        # Never gate on a partial leg set, whoever the caller is.
        return None, "sizing needs T-bills for the cash-drag check"
    smaller = min(margins.values())
    if smaller <= MIN_MARGIN:
        if w <= W_TOL:
            seg = inputs.base
            if seg.log_sum(W_TOL) < seg.log_sum(0.0):
                return 0.0, "growth falls at every step of the hedge ratio: no hedge grows fastest"
            return 0.0, "no hedge ratio out-grows no hedge"
        cons = margins.get("conservative")
        after = "" if cons is None else f", {_bp(cons)} after the cash-drag add-back"
        return 0.0, (
            f"margin {_bp(margins[inputs.base_leg_name])} on the base leg{after}: "
            "not above the 1bp/yr bar"
        )
    if w >= _CAP:
        return 0.5, ("the program dominates the index at every mix; 0.5 is a cap, not half-Kelly")
    return w / 2.0, (
        "half of the full-Kelly w*: one history estimates w*, and full Kelly is "
        "sensitive to that error"
    )


def size(inputs: SizingInputs) -> SizingAnswer:
    """The full answer for one program and window."""
    seg = inputs.base
    w = w_star(seg)
    g0, gw, gh = seg.cagr(0.0), seg.cagr(w), seg.cagr(w / 2.0)
    margins = {inputs.base_leg_name: gw - g0}
    if inputs.conservative is not None:
        cons = inputs.conservative
        margins["conservative"] = cons.cagr(w) - cons.cagr(0.0)
    recommended, reason = _recommend(inputs, w, margins)
    grid_gain = seg.cagr(inputs.grid_best) - g0
    grid_note = None
    if (grid_gain > MIN_MARGIN) != (gw - g0 > MIN_MARGIN):
        grid_note = (
            f"The 0.1 grid's best ({inputs.grid_best:.1f}) gains {_bp(grid_gain)} over no "
            f"hedge, the refined w* ({w:.3f}) {_bp(gw - g0)}: they fall on opposite sides "
            "of the 1bp bar; the refined value decides."
        )
    be, status = _break_even(seg, w)
    halves, decisive = stability(seg, w)
    return SizingAnswer(
        recommended_ratio=recommended,
        at_cap=w >= _CAP,
        reason=reason,
        w_star_grid=inputs.grid_best,
        w_star=w,
        g0=g0,
        g_star=gw,
        g_half=gh,
        half_keeps=(gh - g0) / (gw - g0) if gw - g0 > 0 else None,
        break_even=be,
        break_even_status=status,
        naive_kelly=naive_kelly(seg),
        margin_by_leg=margins,
        grid_note=grid_note,
        halves=halves,
        decisive_months=decisive,
        curve=[
            CurvePoint(weight=float(x), cagr=seg.cagr(float(x)))
            for x in np.linspace(0.0, 1.0, CURVE_POINTS)
        ],
    )
