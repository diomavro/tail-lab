"""The Book's monthly-contributions plan (``docs/adr/0027`` §3).

The lump-sum Book asks whether a hedged book out-grows an unhedged one. This
asks the saver's version: put ``E0`` in now and ``X`` every month into a book
that carries a Cboe hedge program at ratio ``w``, or put the same cash into an
alternative -- which ends richer, at what money-weighted return, through how
deep a drawdown, and **how often across every start date in history**.

**Accounting.** Self-financed throughout (``docs/adr/0027`` §1): the hedged
arm is :func:`~tail_lab.research.backtest.hedge_overlay.blend_nav` at ``w``,
so its premium is paid out of the book at Cboe's real-quote prices. Both arms
receive identical cash flows, so neither is handed free money.

**One path serves every start.** ``blend_nav`` rebalances to target on the
first trading day of each month, so from any such day a plan grows exactly
like the full-history NAV does from that day. Each start is therefore a
cash-flow-weighted read of one NAV path, never a re-simulation.

**The index leg** is the lump-sum Book's own (:func:`~index_leg.build_index_leg`):
SPY's measured total return, fee-adjusted, when ``tiingo_eod`` holds SPY, else
SPX price plus a labelled assumed yield. The hedged arm, the S&P 500
comparator and every per-leg row read it, so the 1993 clamp, the window-end
extension and the degraded-input rule are the overlay's exactly. The plan
recommends no size: sizing is the lump-sum overlay's job.

**Comparators** are total-return levels or nothing: the S&P 500 (the index
leg above), cash at 0%, 3-month
T-bills from the point-in-time ``rates`` dataset, or another Cboe strategy
index (each is a real-quote, dividends-reinvested NAV). ``LTV`` is a quoted
level and bronze OHLCV is split-adjusted only, so neither is offered.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
from pydantic import BaseModel
from scipy.optimize import brentq

from tail_lab.contracts.cboe_strategy import DATASET as CBOE_STRATEGY_DATASET
from tail_lab.contracts.cboe_strategy import STRATEGY_INDEX_CATALOGUE
from tail_lab.contracts.rates import DATASET as RATES_DATASET
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.hedge_overlay import (
    MIN_MARGIN,
    OVERLAY_PROGRAMS,
    Outcome,
    OverlayDataMissing,
    blend_nav,
    outcome,
)
from tail_lab.research.backtest.index_leg import (
    BILL_SERIES,
    DividendBasis,
    IndexLeg,
    LegKey,
    assumed_leg,
    bill_rates_in_force,
    build_index_leg,
    compound,
)
from tail_lab.research.backtest.index_replication import (
    DAYS_PER_YEAR,
    DEFAULT_DIVIDEND_YIELD,
    UNDERLYING_SYMBOL,
    series_from,
)

#: Cboe indices offered as "something else": real-quote NAVs that are not a
#: program being tested and not a price-only or quoted level.
OTHER_CBOE_COMPARATORS: tuple[str, ...] = tuple(
    s for s in STRATEGY_INDEX_CATALOGUE if s not in {*OVERLAY_PROGRAMS, "SPX", "LTV"}
)

#: Bounds for the IRR root search, per year. The floor sits just above -100%:
#: as r -> -1 every compounded payment vanishes, so any positive terminal has
#: a root above it -- a short plan that loses most of its cash can need far
#: below -99.9%. The top is equally loose: a one-month window that gains 50%
#: is ~+13,000%/yr annualised, and (1 + 1e6) ** 30 is still a finite float.
_IRR_BRACKET = (-1.0 + 1e-12, 1e6)


class PlanArm(BaseModel):
    """One arm of one plan over one window."""

    label: str
    contributed: float
    terminal_wealth: float
    irr: float  # money-weighted return per year
    #: Worst peak-to-trough of the arm's per-unit NAV over the window: the
    #: investment's own drawdown, not masked by fresh contributions.
    max_drawdown: float


class PlanWindow(BaseModel):
    """Both arms of the plan over one window."""

    start: dt.date
    end: dt.date
    years: float
    hedged: PlanArm
    comparator: PlanArm
    irr_gap: float  # hedged minus comparator
    outcome: Outcome


class RollingSummary(BaseModel):
    """The same plan begun on every month start that leaves a full horizon."""

    horizon_years: int
    n_starts: int
    first_start: dt.date
    last_start: dt.date
    #: Share of starts where the hedged arm's IRR led by more than 1bp/yr,
    #: trailed by more than 1bp/yr, or sat within 1bp either way.
    share_ahead: float
    share_behind: float
    share_inconclusive: float
    median_gap: float
    p10_gap: float
    p90_gap: float
    worst_gap: float
    best_gap: float


class PlanLegRow(BaseModel):
    """The rolling share re-run on one index leg: a measured leg (base or
    conservative) or, in the fallback, one assumed S&P dividend yield."""

    key: LegKey
    share_ahead: float
    median_gap: float


@dataclass(frozen=True)
class Comparator:
    key: str
    label: str
    levels: pd.Series


def xirr(
    flows: Sequence[float] | npt.NDArray[np.float64],
    years_to_end: Sequence[float] | npt.NDArray[np.float64],
    terminal: float,
) -> float:
    """Money-weighted return: the annual rate ``r`` at which every contribution,
    compounded to the end, adds up to ``terminal``.

    ``flows`` are the amounts paid in (positive); ``years_to_end`` how long
    each was invested. Raises ``ValueError`` when no rate in the bracket fits
    -- a caller bug for any plan with positive inputs and terminal wealth.
    """
    c = np.asarray(flows, dtype=float)
    t = np.asarray(years_to_end, dtype=float)
    if terminal <= 0.0:
        return -1.0  # everything paid in is gone: exactly -100%

    def gap(r: float) -> float:
        return float(np.sum(c * (1.0 + r) ** t)) - terminal

    lo, hi = _IRR_BRACKET
    if gap(lo) * gap(hi) > 0:
        raise ValueError(f"no IRR in [{lo}, {hi}] for terminal {terminal}")
    return float(brentq(gap, lo, hi, xtol=1e-12))


def month_starts(index: pd.DatetimeIndex) -> np.ndarray:
    """Positions of the first trading day of each calendar month (the first
    date counts as one) -- the days both arms rebalance and receive cash."""
    months = index.to_period("M")
    return np.array([0, *(np.flatnonzero(months[1:] != months[:-1]) + 1)], dtype=int)


def _arm(
    nav: np.ndarray, *, label: str, pay: np.ndarray, flows: np.ndarray, end: int, t_end: np.ndarray
) -> PlanArm:
    units = flows / nav[pay]
    terminal = float(units.sum() * nav[end])
    path = nav[pay[0] : end + 1]
    drawdown = float((path / np.maximum.accumulate(path) - 1.0).min())
    return PlanArm(
        label=label,
        contributed=float(flows.sum()),
        terminal_wealth=terminal,
        irr=xirr(flows, t_end, terminal),
        max_drawdown=drawdown,
    )


def run_window(
    hedged: pd.Series,
    comparator: pd.Series,
    *,
    hedged_label: str,
    comparator_label: str,
    start: int,
    end: int,
    e0: float,
    monthly: float,
) -> PlanWindow:
    """Both arms over positions ``[start, end]`` of their shared index.

    ``E0 + X`` is paid at ``start``; ``X`` on every later month start before
    ``end``. ``start`` must itself be a month start.
    """
    if not hedged.index.equals(comparator.index):
        raise ValueError("hedged and comparator must share one date index")
    if e0 < 0 or monthly < 0 or e0 + monthly <= 0:
        raise ValueError("a plan needs a non-negative E0 and X, not both zero")
    index = pd.DatetimeIndex(hedged.index)
    starts = month_starts(index)
    if start not in set(starts.tolist()):
        raise ValueError("a plan starts on a month start")
    pay = starts[(starts >= start) & (starts < end)]
    flows = np.full(len(pay), monthly)
    flows[0] += e0
    stamps = index.to_numpy(dtype="datetime64[D]")
    days = (stamps[end] - stamps[pay]).astype(float)
    t_end = days / DAYS_PER_YEAR
    h = _arm(
        hedged.to_numpy(dtype=float), label=hedged_label, pay=pay, flows=flows, end=end, t_end=t_end
    )
    c = _arm(
        comparator.to_numpy(dtype=float),
        label=comparator_label,
        pay=pay,
        flows=flows,
        end=end,
        t_end=t_end,
    )
    gap = h.irr - c.irr
    return PlanWindow(
        start=index[start].date(),
        end=index[end].date(),
        years=float(t_end[0]),
        hedged=h,
        comparator=c,
        irr_gap=gap,
        outcome=outcome(gap),
    )


def rolling_gaps(
    hedged: pd.Series, comparator: pd.Series, *, horizon_years: int, e0: float, monthly: float
) -> tuple[list[float], list[dt.date]]:
    """IRR gap for the plan begun on every month start with a full horizon left."""
    index = pd.DatetimeIndex(hedged.index)
    starts = month_starts(index)
    months = 12 * horizon_years
    gaps: list[float] = []
    begun: list[dt.date] = []
    for i in range(len(starts) - months):
        w = run_window(
            hedged,
            comparator,
            hedged_label="h",
            comparator_label="c",
            start=int(starts[i]),
            end=int(starts[i + months]),
            e0=e0,
            monthly=monthly,
        )
        gaps.append(w.irr_gap)
        begun.append(w.start)
    return gaps, begun


def summarize(
    gaps: Sequence[float], begun: Sequence[dt.date], horizon_years: int
) -> RollingSummary:
    if not gaps:
        raise OverlayDataMissing(f"history is shorter than one {horizon_years}-year plan")
    g = np.asarray(gaps)
    n = len(g)
    return RollingSummary(
        horizon_years=horizon_years,
        n_starts=n,
        first_start=begun[0],
        last_start=begun[-1],
        share_ahead=float((g > MIN_MARGIN).sum() / n),
        share_behind=float((g < -MIN_MARGIN).sum() / n),
        share_inconclusive=float((np.abs(g) <= MIN_MARGIN).sum() / n),
        median_gap=float(np.median(g)),
        p10_gap=float(np.quantile(g, 0.1)),
        p90_gap=float(np.quantile(g, 0.9)),
        worst_gap=float(g.min()),
        best_gap=float(g.max()),
    )


def bill_levels(rates: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.Series:
    """A cash account earning the 3-month T-bill yield as known each day.

    Point-in-time (``docs/adr/0009``): the rate in force on each day is
    :func:`~index_leg.bill_rates_in_force`'s. Interest over ``(t-1, t]``
    accrues at the rate in force on ``t-1``, compounding by calendar days.

    Returned on the tail of ``dates`` from the first day a rate is known
    (ALFRED's vintage history starts in 2005, so this narrows like a late
    Cboe index rather than refusing). Raises :class:`OverlayDataMissing`
    when the series is absent or never known on ``dates``.
    """
    if rates[rates["series_id"] == BILL_SERIES].empty:
        raise OverlayDataMissing(f"no {BILL_SERIES} in the rates dataset")
    in_force = bill_rates_in_force(rates, dates)
    known = np.flatnonzero(~np.isnan(in_force))
    if len(known) < 2:
        raise OverlayDataMissing(f"{BILL_SERIES} is not known point-in-time on these dates")
    start = int(known[0])
    tail = dates[start:]
    rate = in_force[start:] / 100.0
    gaps = tail.to_series().diff().dt.days.fillna(0).to_numpy(dtype=float)
    growth = np.ones(len(tail))
    growth[1:] = compound(rate[:-1], gaps[1:])
    return pd.Series(np.cumprod(growth), index=tail)


def dense_tail(levels: pd.Series) -> pd.Series:
    """``levels`` from the first date after its last **skipped calendar
    month** -- two consecutive dates whose months are two or more apart, so
    a whole month has no first trading day in the record.

    That is the calendar property the plan depends on (a month start is a
    month start), stated directly rather than as a day count: a 31-day hole
    can delete all of February while a 32-day one can skip no month at all.
    Cboe's PUT file has only 7 points in 1991-2004, which would otherwise
    pass as month starts and stretch a "10-year" plan to 25 years; CLL's two
    11-day holes (2008, 2013) and a short feed freeze skip no month and are
    kept.
    """
    ordered = levels.sort_index()
    index = pd.DatetimeIndex(ordered.index)
    month_number = index.year * 12 + index.month
    holes = np.flatnonzero(np.diff(month_number.to_numpy()) >= 2)
    return ordered if len(holes) == 0 else ordered.iloc[int(holes[-1]) + 1 :]


def comparator_levels(
    key: str,
    *,
    index_tr: pd.Series,
    index_label: str,
    dates: pd.DatetimeIndex,
    rates: pd.DataFrame | None,
    cboe: dict[str, pd.Series],
) -> Comparator:
    """Total-return levels for a comparator on ``dates``, or a refusal.

    ``index_tr`` is the index leg's total-return levels (the S&P 500
    comparator), ``index_label`` how the page names it. Raises
    :class:`OverlayDataMissing` with the reason a comparator cannot be
    offered; ``ValueError`` for a key that is never a comparator.
    """
    if key == "spx":
        return Comparator("spx", index_label, index_tr.reindex(dates))
    if key == "cash":
        return Comparator("cash", "Cash at 0%", pd.Series(1.0, index=dates))
    if key == "bills":
        if rates is None:
            raise OverlayDataMissing("no T-bill history in the lake yet (rates not ingested)")
        return Comparator(
            "bills",
            f"3-month T-bills ({BILL_SERIES}, as known each day)",
            bill_levels(rates, dates),
        )
    if key in OTHER_CBOE_COMPARATORS:
        levels = cboe.get(key)
        if levels is None:
            raise OverlayDataMissing(f"{key} is not in the Cboe snapshot")
        # Restricted to the shared history rather than refused: most of the
        # family starts years after PPUT (1986), and the plan reports its own
        # first and last start, so a shorter span is shown, never hidden.
        shared = dates.intersection(pd.DatetimeIndex(dense_tail(levels).index))
        if shared.empty:
            raise OverlayDataMissing(f"{key} shares no history with this program")
        return Comparator(key, STRATEGY_INDEX_CATALOGUE[key], levels.loc[shared])
    raise ValueError(f"unknown comparator {key!r}")


def _from_a_month_start(
    narrowed: pd.DatetimeIndex, *, program_dates: pd.DatetimeIndex
) -> pd.DatetimeIndex:
    """``narrowed`` cut to begin on an *observed* first trading day of a month:
    one whose previous program date falls in an earlier month. A series' own
    first date never qualifies -- PPUT's history opens on 1986-06-30, VXTH's
    on 2006-03-31, the T-bill record on 2005-06-28 -- so the leading partial
    month is dropped and the first two payments are a month apart, not days."""
    observed = program_dates[month_starts(program_dates)[1:]]
    later = observed[observed >= narrowed[0]] if not narrowed.empty else observed[:0]
    return narrowed[:0] if later.empty else narrowed[narrowed >= later[0]]


def common_dates(*series: pd.Series) -> pd.DatetimeIndex:
    out = pd.DatetimeIndex(series[0].index)
    for s in series[1:]:
        out = out.intersection(pd.DatetimeIndex(s.index))
    return out


def hedged_levels(
    program: pd.Series,
    index_tr: pd.Series,
    dates: pd.DatetimeIndex,
    *,
    hedge_ratio: float,
) -> pd.Series:
    """The self-financed hedged book: ``hedge_ratio`` in the program, the rest
    in the index leg's total return, rebalanced monthly."""
    return blend_nav(program.loc[dates], index_tr.loc[dates], weight=hedge_ratio)


def index_label(leg: IndexLeg) -> str:
    """How the page names the S&P 500 leg -- measured or assumed."""
    if leg.basis.source == "measured":
        return "S&P 500 total return — SPY, fee-adjusted"
    return f"S&P 500 total return ({leg.basis.assumed_yield or 0.0:.1%} assumed yield)"


def leg_shares(
    program: pd.Series,
    leg: IndexLeg,
    comparator: Comparator,
    dates: pd.DatetimeIndex,
    *,
    hedge_ratio: float,
    horizon_years: int,
    e0: float,
    monthly: float,
    rebuild: bool,
) -> list[PlanLegRow]:
    """The rolling verdict on each index leg. ``rebuild`` re-derives the
    comparator on the row's leg too when it is the S&P 500 (whose dividends
    the legs differ on). A leg with no value on some plan date (the
    conservative leg before T-bills are known) is left out, never run on a
    shorter history."""
    out: list[PlanLegRow] = []
    for key, levels in leg.rows:
        on = levels.reindex(dates)
        if on.isna().any():
            continue
        h = hedged_levels(program, on, dates, hedge_ratio=hedge_ratio)
        c = on if rebuild else comparator.levels
        gaps, _ = rolling_gaps(h, c, horizon_years=horizon_years, e0=e0, monthly=monthly)
        g = np.asarray(gaps)
        out.append(
            PlanLegRow(
                key=key,
                share_ahead=float((g > MIN_MARGIN).sum() / len(g)) if len(g) else math.nan,
                median_gap=float(np.median(g)) if len(g) else math.nan,
            )
        )
    return out


#: Comparator keys in display order: the three buttons, then the picker.
COMPARATOR_KEYS: tuple[str, ...] = ("spx", "cash", "bills", *OTHER_CBOE_COMPARATORS)


class ComparatorOption(BaseModel):
    """One comparator and whether this lake can offer it (refusals are values)."""

    key: str
    label: str
    available: bool
    reason: str | None


class BookPlanResult(BaseModel):
    """The contributions plan, as of one date."""

    as_of: dt.date
    program: str
    hedge_ratio: float
    e0: float
    monthly: float
    horizon_years: int
    #: The assumed flat yield in the fallback; ``None`` when measured.
    dividend_yield: float | None
    dividend: DividendBasis
    comparator: str
    comparators: list[ComparatorOption]
    #: ``None`` with ``refusal`` set when the chosen comparator or history
    #: cannot answer; the options above still say what can.
    window: PlanWindow | None
    rolling: RollingSummary | None
    #: The rolling share on every index leg (see ``hedge_overlay.OverlayLegRow``).
    legs: list[PlanLegRow]
    refusal: str | None


def _label(key: str) -> str:
    return {"spx": "S&P 500", "cash": "Cash", "bills": "T-bills"}.get(
        key, STRATEGY_INDEX_CATALOGUE.get(key, key)
    )


@dataclass(frozen=True)
class PlanRequest:
    program: str
    hedge_ratio: float
    e0: float
    monthly: float
    comparator: str
    horizon_years: int
    start: dt.date | None = None


def _window_bounds(
    dates: pd.DatetimeIndex, *, horizon_years: int, start: dt.date | None
) -> tuple[int, int]:
    """The illustrated window: from the first month start on/after ``start``
    (default: the most recent full horizon) for ``horizon_years``, cut at the
    end of history."""
    starts = month_starts(dates)
    months = 12 * horizon_years
    if start is None:
        i = max(len(starts) - 1 - months, 0)
    else:
        after = np.flatnonzero(dates[starts] >= pd.Timestamp(start))
        if len(after) == 0:
            raise OverlayDataMissing(f"no month start on or after {start}")
        i = int(after[0])
    j = i + months
    end = int(starts[j]) if j < len(starts) else len(dates) - 1
    # At least one full month: a window of days has no meaningful annual
    # rate (and is how a stray `start` met the IRR solver's bracket). When a
    # later month start exists the end is always at or past it.
    if i + 1 >= len(starts):
        raise OverlayDataMissing("the window holds less than one full month")
    return int(starts[i]), end


def _comparator_options(
    wanted: str,
    *,
    leg: IndexLeg,
    dates: pd.DatetimeIndex,
    rates: pd.DataFrame | None,
    cboe: dict[str, pd.Series],
) -> tuple[list[ComparatorOption], Comparator | None, str | None]:
    """Every comparator's availability, the chosen one's levels, and why the
    chosen one is refused (``None`` when it is not)."""
    options: list[ComparatorOption] = []
    chosen: Comparator | None = None
    refusal: str | None = None
    for key in COMPARATOR_KEYS:
        try:
            c = comparator_levels(
                key,
                index_tr=leg.headline,
                index_label=index_label(leg),
                dates=dates,
                rates=rates,
                cboe=cboe,
            )
        except OverlayDataMissing as exc:
            options.append(
                ComparatorOption(key=key, label=_label(key), available=False, reason=str(exc))
            )
            if key == wanted:
                refusal = f"{_label(key)}: {exc}"
            continue
        options.append(ComparatorOption(key=key, label=_label(key), available=True, reason=None))
        if key == wanted:
            chosen = c

    return options, chosen, refusal


def run_book_plan(
    spx: pd.Series,
    cboe: dict[str, pd.Series],
    rates: pd.DataFrame | None,
    request: PlanRequest,
    *,
    as_of: dt.date,
    leg: IndexLeg | None = None,
    dividend_yield: float = DEFAULT_DIVIDEND_YIELD,
) -> BookPlanResult:
    """Every series is cut at ``as_of`` first; no read passes it.

    ``leg`` is the index leg (:func:`~index_leg.build_index_leg`); without one
    the flat-yield fallback is built on ``spx`` at ``dividend_yield``."""
    if request.program not in OVERLAY_PROGRAMS:
        raise ValueError(f"not a hedged S&P 500 program: {request.program}")
    if not 0.0 <= request.hedge_ratio <= 1.0:
        raise ValueError("hedge_ratio must be in [0, 1]")
    if request.comparator not in COMPARATOR_KEYS:
        raise ValueError(f"unknown comparator {request.comparator!r}")
    cutoff = pd.Timestamp(as_of)
    spx = spx[spx.index <= cutoff]
    leg = (leg or assumed_leg(spx, dividend_yield=dividend_yield)).cut(as_of)
    cboe = {k: v[v.index <= cutoff] for k, v in cboe.items()}
    program = cboe.get(request.program)
    if program is None or program.empty:
        raise OverlayDataMissing(f"{request.program} is not in the Cboe snapshot")
    dates = common_dates(program, leg.headline)

    options, chosen, refusal = _comparator_options(
        request.comparator, leg=leg, dates=dates, rates=rates, cboe=cboe
    )

    base = {
        "as_of": as_of,
        "program": request.program,
        "hedge_ratio": request.hedge_ratio,
        "e0": request.e0,
        "monthly": request.monthly,
        "horizon_years": request.horizon_years,
        "dividend_yield": leg.basis.assumed_yield,
        "dividend": leg.basis,
        "comparator": request.comparator,
        "comparators": options,
    }
    if chosen is None:
        return BookPlanResult(**base, window=None, rolling=None, legs=[], refusal=refusal)

    # Every comparator's levels already sit on (a subset of) the program's
    # dates; the plan runs on exactly those, from the first observed month
    # start (see _from_a_month_start).
    dates = _from_a_month_start(pd.DatetimeIndex(chosen.levels.index), program_dates=dates)
    if len(dates) < 2:
        # A comparator whose usable record sits inside one month (a feed
        # frozen for longer than a month leaves exactly this) has no plan.
        return BookPlanResult(
            **base,
            window=None,
            rolling=None,
            legs=[],
            refusal=f"{chosen.label}: no full month of history shared with {request.program}",
        )
    chosen = Comparator(chosen.key, chosen.label, chosen.levels.loc[dates])
    hedged = hedged_levels(program, leg.headline, dates, hedge_ratio=request.hedge_ratio)
    hedged_label = f"{request.hedge_ratio:.0%} hedged with {request.program}"
    try:
        lo, hi = _window_bounds(dates, horizon_years=request.horizon_years, start=request.start)
        window = run_window(
            hedged,
            chosen.levels,
            hedged_label=hedged_label,
            comparator_label=chosen.label,
            start=lo,
            end=hi,
            e0=request.e0,
            monthly=request.monthly,
        )
        gaps, begun = rolling_gaps(
            hedged,
            chosen.levels,
            horizon_years=request.horizon_years,
            e0=request.e0,
            monthly=request.monthly,
        )
        rolling = summarize(gaps, begun, request.horizon_years)
    except OverlayDataMissing as exc:
        return BookPlanResult(**base, window=None, rolling=None, legs=[], refusal=str(exc))
    legs = leg_shares(
        program,
        leg,
        chosen,
        dates,
        hedge_ratio=request.hedge_ratio,
        horizon_years=request.horizon_years,
        e0=request.e0,
        monthly=request.monthly,
        rebuild=chosen.key == "spx",
    )
    return BookPlanResult(**base, window=window, rolling=rolling, legs=legs, refusal=None)


def compute_book_plan(store: LakeStore, request: PlanRequest, *, as_of: dt.date) -> BookPlanResult:
    """Read the Cboe snapshot (and ``rates``, if any) point-in-time and run.

    Raises :class:`OverlayDataMissing` with no Cboe snapshot or no SPX in it.
    The index leg is :func:`~index_leg.build_index_leg`'s. A missing ``rates``
    dataset disables the T-bill comparator and withholds the conservative
    leg's row; a missing ``tiingo_eod`` falls back to the labelled assumed
    yield.
    """
    try:
        bronze = store.read_bronze_as_of(CBOE_STRATEGY_DATASET, as_of)
    except KeyError:
        raise
    except LookupError as exc:
        raise OverlayDataMissing(
            f"no Cboe strategy indices known as of {as_of.isoformat()}"
        ) from exc
    cboe = {
        str(sym): series_from(rows, date_col="trade_date", value_col="close")
        for sym, rows in bronze.groupby("index_symbol")
    }
    spx = cboe.pop(UNDERLYING_SYMBOL, None)
    if spx is None:
        raise OverlayDataMissing(f"no {UNDERLYING_SYMBOL} rows in the snapshot known as of {as_of}")
    try:
        rates: pd.DataFrame | None = store.read_bronze_as_of(RATES_DATASET, as_of)
    except KeyError:
        raise
    except LookupError:
        rates = None
    leg = build_index_leg(store, as_of, spx=spx)
    return run_book_plan(spx, cboe, rates, request, as_of=as_of, leg=leg)
