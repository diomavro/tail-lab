"""Rodman's Paradox, tested: does a hedged S&P 500 program make the blend
better than either end?

Artemis Capital's April 2016 letter ("Dennis Rodman and the Art of Portfolio
Optimization") claims that a 50/50 mix of a long-volatility hedge-fund index
and the S&P 500 beat both, 2005-2016: a negative-carry asset that pays in the
left tail lifts the *combined* portfolio. That is the claim ``docs/END_STATE.md``
§4 Q8 says is the only way a put book can be justified -- as a hedge, judged
on the growth of the whole portfolio, never standalone.

**What this tests, and what it does not.** The letter's long-vol index
(CBOE Eurekahedge) is licensed and not in the lake. What the lake has is
Cboe's own strategy indices, priced from real OPRA data (PPUT's family at
traded VWAPs, VXTH at quoted mid and ask), and every one
of them already *contains* the S&P 500: PPUT is the index plus a monthly 5%
OTM put, PPUT3M plus a quarterly 10% OTM put, VXTH plus VIX calls. So the
test is a **hedge-ratio blend**: hold ``w`` in the program and ``1 - w`` in
the plain S&P 500 total return, rebalanced monthly. ``w`` is then the
fraction of the equity book that carries the hedge. The paradox holds, in the
sense the letter means, when some interior ``w`` grows faster than both
``w = 0`` (no hedge) and ``w = 1`` (all hedged). ``LTV`` is deliberately
absent: it is a quoted price of tail protection, not a return series, and
blending a price level is meaningless.

**The dividend leg is assumed, not measured.** The programs reinvest real
dividends; ``SPXT`` is not served free (HTTP 403), so the unhedged leg is SPX
price plus a flat yield. Too low a yield flatters every blend, so the verdict
is re-run across :data:`SENSITIVITY_YIELDS` and shown beside the headline.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.cboe_strategy import DATASET as CBOE_STRATEGY_DATASET
from tail_lab.contracts.cboe_strategy import STRATEGY_INDEX_CATALOGUE
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.index_replication import (
    DAYS_PER_YEAR,
    DEFAULT_DIVIDEND_YIELD,
    SENSITIVITY_YIELDS,
    UNDERLYING_SYMBOL,
    series_from,
)

#: The hedged programs blended against the S&P 500. Each is a total-return
#: S&P 500 position plus a hedge, so a blend weight is a hedge ratio.
OVERLAY_PROGRAMS: tuple[str, ...] = ("PPUT", "PPUT3M", "VXTH")

#: Hedge ratios evaluated, 0% to 100% of the book in the program.
WEIGHTS: tuple[float, ...] = tuple(round(0.1 * i, 1) for i in range(11))

#: The letter's own window: "since 2005" to its April 2016 publication.
COLE_WINDOW: tuple[dt.date, dt.date] = (dt.date(2005, 1, 3), dt.date(2016, 3, 31))

#: Trading days per year, for annualising daily volatility.
TRADING_DAYS = 252

#: A margin within this of zero, either side, is ``inconclusive``: 1bp/yr of
#: growth, and the same absolute amount of CAGR-per-vol on the risk-adjusted
#: test. Symmetric on purpose -- a 0.1bp loss is no more a verdict than a
#: 0.1bp win, and on every real window a 0.5pp change in the assumed dividend
#: yield moves the margin by more than 5bp.
MIN_MARGIN = 1e-4

#: How far a window's data may fall short of the span asked for before it is
#: flagged ``clipped``: a long weekend plus a holiday, not a missing feed.
CLIP_TOLERANCE_DAYS = 7

#: The three readings of a margin. ``inconclusive`` exists so a page never
#: calls a sliver either way a win or a loss.
Outcome = Literal["holds", "inconclusive", "fails"]


class OverlayDataMissing(LookupError):
    """The lake cannot answer: no snapshot, no SPX, or too short an overlap.

    Its own type so callers can turn exactly this into a refusal; a
    ``KeyError`` from a bug is also a ``LookupError`` and must not be.
    """


#: Fewer common trading days than this cannot carry a monthly-rebalanced test.
MIN_DAYS = 60


class WeightPoint(BaseModel):
    """The blend at one hedge ratio."""

    weight: float  # fraction of the book in the hedged program
    cagr: float  # geometric growth rate per year: the time-average growth
    volatility: float  # annualised stdev of daily returns
    max_drawdown: float  # worst peak-to-trough, as a negative fraction
    cagr_per_vol: float | None  # risk-adjusted; no risk-free rate is netted


class SensitivityPoint(BaseModel):
    """The growth verdict re-run at one alternative dividend yield."""

    dividend_yield: float
    best_weight: float
    margin: float
    outcome: Outcome


class OverlayWindow(BaseModel):
    """One program blended against the S&P 500 over one window."""

    key: str  # "cole" | "full"
    label: str
    #: The span asked for, and the span the data actually cover.
    requested_start: dt.date
    requested_end: dt.date
    start: dt.date
    end: dt.date
    #: True when the data cover less than the span asked for -- the program
    #: or SPX starts late, a feed goes stale, or ``as_of`` precedes the
    #: window's end -- so this is a different test from the one asked for.
    clipped: bool
    years: float
    points: list[WeightPoint]
    #: Hedge ratio with the highest growth rate.
    best_weight: float
    #: Best interior growth minus the better of the two ends.
    margin: float
    outcome: Outcome
    #: Same test on CAGR per unit of volatility -- the letter's own yardstick.
    #: ``None`` when any mix has zero volatility and the ratio is undefined.
    best_weight_risk_adjusted: float | None
    outcome_risk_adjusted: Outcome | None
    sensitivity: list[SensitivityPoint]


class ProgramOverlay(BaseModel):
    """Every window for one hedged program."""

    index_symbol: str
    description: str
    first_date: dt.date
    windows: list[OverlayWindow]
    #: Windows the lake cannot cover, keyed by window, with the reason -- a
    #: refusal is a value, so a missing window is never just an absent row.
    unavailable: dict[str, str]


class HedgeOverlayResult(BaseModel):
    """The whole test, as of one date."""

    as_of: dt.date
    dividend_yield: float
    weights: list[float]
    programs: list[ProgramOverlay]
    #: Programs the test wanted but the snapshot lacks, with the reason.
    missing: dict[str, str]


def total_return_levels(price: pd.Series, *, dividend_yield: float) -> pd.Series:
    """S&P 500 price levels turned into a total-return index, from a flat yield.

    The yield accrues by calendar days between observations, so a weekend
    earns its three days, and compounds to exactly ``dividend_yield`` a year
    on a flat price. Rebased to 1.0 on the first date.
    """
    gaps = price.index.to_series().diff().dt.days.fillna(0).to_numpy(dtype=float)
    growth = (price / price.shift(1)).fillna(1.0).to_numpy(dtype=float)
    growth = growth + (1.0 + dividend_yield) ** (gaps / DAYS_PER_YEAR) - 1.0
    return pd.Series(np.cumprod(growth), index=price.index)


def blend_nav(program: pd.Series, equity: pd.Series, *, weight: float) -> pd.Series:
    """Daily NAV of ``weight`` in ``program``, the rest in ``equity``.

    Rebalanced to target on the first trading day of each calendar month and
    left to drift in between, which is what an investor holding two funds
    does. The two series must share an index. Starts at 1.0.
    """
    if not program.index.equals(equity.index):
        raise ValueError("program and equity must share one date index")
    months = pd.DatetimeIndex(program.index).to_period("M")
    last = len(program) - 1
    # Rebalance days: the first date, then the first date of each new month.
    resets = [0, *(np.flatnonzero(months[1:] != months[:-1]) + 1)]
    prog = program.to_numpy(dtype=float)
    eq = equity.to_numpy(dtype=float)
    nav = np.empty(len(program))
    base = 1.0
    for s, e in zip(resets, [*resets[1:], last], strict=True):
        # Each segment runs from one rebalance close to the next, inclusive,
        # so the reset day ends one segment and starts the next.
        p, q = prog[s : e + 1], eq[s : e + 1]
        nav[s : e + 1] = base * (weight * p / p[0] + (1.0 - weight) * q / q[0])
        base = nav[e]
    return pd.Series(nav, index=program.index)


def _point(nav: pd.Series, *, weight: float, years: float) -> WeightPoint:
    daily = nav.pct_change().dropna()
    vol = float(daily.std(ddof=1) * math.sqrt(TRADING_DAYS))
    cagr = float(nav.iloc[-1] / nav.iloc[0]) ** (1.0 / years) - 1.0
    drawdown = float((nav / nav.cummax() - 1.0).min())
    return WeightPoint(
        weight=weight,
        cagr=cagr,
        volatility=vol,
        max_drawdown=drawdown,
        cagr_per_vol=cagr / vol if vol > 0 else None,
    )


def _verdict(values: Sequence[float]) -> tuple[int, float]:
    """Index of the best value, and best-interior minus better-end.

    A tie goes to an end -- either end -- so an interior mix is only ever
    "best" when it strictly beats both.
    """
    if len(values) < 3:
        raise ValueError("a verdict needs both ends and at least one interior mix")
    ends = max(values[0], values[-1])
    margin = max(values[1:-1]) - ends
    if margin > 0:
        return int(np.argmax(values)), margin
    return (0 if values[0] >= values[-1] else len(values) - 1), margin


def outcome(margin: float) -> Outcome:
    """Read a margin: a clear win, a clear loss, or too close to call."""
    if margin > MIN_MARGIN:
        return "holds"
    return "fails" if margin < -MIN_MARGIN else "inconclusive"


class _Window(BaseModel):
    """Every hedge ratio over one span, before the verdict is dressed up."""

    start: dt.date
    end: dt.date
    years: float
    points: list[WeightPoint]


def evaluate_window(
    program: pd.Series,
    spx: pd.Series,
    *,
    start: dt.date,
    end: dt.date,
    dividend_yield: float,
) -> _Window:
    """Every hedge ratio over the common trading days in ``[start, end]``."""
    common = program.index.intersection(spx.index)
    common = common[(common >= pd.Timestamp(start)) & (common <= pd.Timestamp(end))]
    if len(common) < MIN_DAYS:
        raise OverlayDataMissing(f"only {len(common)} common trading days in {start}..{end}")
    prog = program.loc[common]
    equity = total_return_levels(spx.loc[common], dividend_yield=dividend_yield)
    years = (common[-1] - common[0]).days / DAYS_PER_YEAR
    return _Window(
        start=common[0].date(),
        end=common[-1].date(),
        years=years,
        points=[_point(blend_nav(prog, equity, weight=w), weight=w, years=years) for w in WEIGHTS],
    )


def run_overlay_window(
    program: pd.Series,
    spx: pd.Series,
    *,
    key: str,
    label: str,
    start: dt.date,
    end: dt.date,
    dividend_yield: float,
) -> OverlayWindow:
    """One program over one window, with its dividend-yield sensitivity."""
    window = evaluate_window(program, spx, start=start, end=end, dividend_yield=dividend_yield)
    best, margin = _verdict([p.cagr for p in window.points])
    ratios = [p.cagr_per_vol for p in window.points]
    best_rv: float | None = None
    outcome_rv: Outcome | None = None
    if all(r is not None for r in ratios):
        i, m = _verdict([r for r in ratios if r is not None])
        best_rv, outcome_rv = WEIGHTS[i], outcome(m)
    sensitivity = []
    for q in SENSITIVITY_YIELDS:
        alt = (
            window
            if q == dividend_yield
            else evaluate_window(program, spx, start=start, end=end, dividend_yield=q)
        )
        w, m = _verdict([p.cagr for p in alt.points])
        sensitivity.append(
            SensitivityPoint(dividend_yield=q, best_weight=WEIGHTS[w], margin=m, outcome=outcome(m))
        )
    return OverlayWindow(
        key=key,
        label=label,
        requested_start=start,
        requested_end=end,
        start=window.start,
        end=window.end,
        clipped=(window.start - start).days > CLIP_TOLERANCE_DAYS
        or (end - window.end).days > CLIP_TOLERANCE_DAYS,
        years=window.years,
        points=window.points,
        best_weight=WEIGHTS[best],
        margin=margin,
        outcome=outcome(margin),
        best_weight_risk_adjusted=best_rv,
        outcome_risk_adjusted=outcome_rv,
        sensitivity=sensitivity,
    )


def run_hedge_overlay(
    spx: pd.Series,
    programs: Mapping[str, pd.Series],
    *,
    as_of: dt.date,
    dividend_yield: float = DEFAULT_DIVIDEND_YIELD,
) -> HedgeOverlayResult:
    """The test over the letter's window and over each program's full history.

    Every series is cut at ``as_of`` first, so no window can read past it; an
    ``as_of`` before the letter's window ends shows up as that window
    ``clipped``, never as a shorter test wearing the letter's label.
    """
    unknown = sorted(set(programs) - set(OVERLAY_PROGRAMS))
    if unknown:
        # LTV in particular is a quoted level, not a NAV: never blend it.
        raise ValueError(f"not a hedged S&P 500 program: {unknown}; use {OVERLAY_PROGRAMS}")
    cutoff = pd.Timestamp(as_of)
    spx = spx[spx.index <= cutoff]
    out: list[ProgramOverlay] = []
    missing = {s: "not in the Cboe snapshot" for s in OVERLAY_PROGRAMS if s not in programs}
    for symbol, full_levels in programs.items():
        levels = full_levels[full_levels.index <= cutoff]
        if levels.empty:
            missing[symbol] = f"no rows on or before {as_of.isoformat()}"
            continue
        first = levels.index[0].date()
        spans = (
            ("cole", "The letter's window (2005 to Mar 2016)", *COLE_WINDOW),
            ("full", "Full history", first, as_of),
        )
        windows: list[OverlayWindow] = []
        unavailable: dict[str, str] = {}
        for key, label, start, end in spans:
            try:
                windows.append(
                    run_overlay_window(
                        levels,
                        spx,
                        key=key,
                        label=label,
                        start=start,
                        end=end,
                        dividend_yield=dividend_yield,
                    )
                )
            except OverlayDataMissing as exc:
                unavailable[key] = str(exc)
        out.append(
            ProgramOverlay(
                index_symbol=symbol,
                description=STRATEGY_INDEX_CATALOGUE[symbol],
                first_date=first,
                windows=windows,
                unavailable=unavailable,
            )
        )
    return HedgeOverlayResult(
        as_of=as_of,
        dividend_yield=dividend_yield,
        weights=list(WEIGHTS),
        programs=out,
        missing=missing,
    )


def compute_hedge_overlay(store: LakeStore, *, as_of: dt.date) -> HedgeOverlayResult:
    """Read SPX and the programs point-in-time as of ``as_of`` and run the test.

    Raises :class:`OverlayDataMissing` when there is no Cboe snapshot or no
    SPX in it. A program missing from the snapshot does not fail the others;
    it is listed in ``missing`` with the reason.
    """
    try:
        bronze = store.read_bronze_as_of(CBOE_STRATEGY_DATASET, as_of)
    except KeyError:
        # A LookupError subclass, but a store bug, not an absent snapshot.
        raise
    except LookupError as exc:
        raise OverlayDataMissing(
            f"no Cboe strategy indices known as of {as_of.isoformat()}"
        ) from exc

    def series(symbol: str) -> pd.Series | None:
        rows = bronze[bronze["index_symbol"] == symbol]
        if rows.empty:
            return None
        return series_from(rows, date_col="trade_date", value_col="close")

    spx = series(UNDERLYING_SYMBOL)
    if spx is None:
        raise OverlayDataMissing(f"no {UNDERLYING_SYMBOL} rows in the snapshot known as of {as_of}")
    programs = {s: p for s in OVERLAY_PROGRAMS if (p := series(s)) is not None}
    return run_hedge_overlay(spx, programs, as_of=as_of)
