"""The out-of-sample signal test: does the fragility signal, read at a date,
pick puts that pay *afterwards*?

The in-sample bake-off (``metric_screen.py``) measures a metric and the payoff
over the same window, so it cannot say whether the signal predicts anything.
This module is the walk-forward counterpart. At each rebalance date ``t``:

1. every name is ranked using only data known at ``t`` (a ``signal_years``
   trailing window of returns, the 20-day volatility, the VIX);
2. the top ``top_k`` names get one model-priced put each, held one roll;
3. the roll is settled at expiry and the result recorded.

The basket is then compared with four things: all names, uniformly random
``top_k``-baskets, an equal-premium SPY put, and **plain-volatility screens
built and tested the same way** (``rv20``, ``rv_long``, ``rv_ratio``, both
directions). The last comparison is the honest one: fragility metrics are
correlated with volatility, and a model-priced put pays more on volatile names
whatever the signal knows.

Structure -- two seams, so the statistics are testable without a lake:

* :func:`collect_periods` reads the store and produces one
  :class:`PeriodInputs` per rebalance date (the shared eligible set, every
  screen's signal, and each name's payoff and cost). Point-in-time correctness
  lives here: all prices come through ``load_asof_series``; each date's window
  is sliced by date up to that date, never after it.
* :func:`evaluate_screens` is pure: ``PeriodInputs`` in, results out. Tests
  plant signals into it directly (a zero-noise volatility signal, a perfect
  signal) and check the verdict.

Definitions that matter:

* **Name-period ROI** is ``payoff / (notional + spread) - 1`` -- the premium
  budget plus the model half-spread. It differs from the tape's
  ``net / notional`` and is labelled so. Totals are ``sum(payoff) /
  sum(cost) - 1``.
* **Commission is zero.** It is ``$0.65 * contracts / 100`` and ``contracts =
  notional / premium`` scales with the share price LEVEL. Bronze OHLCV is
  split-adjusted (``docs/DATA_CONTRACTS.md``), so a split never shows up as a
  crash -- but the adjusted level at ``t`` already embeds splits that happen
  after ``t`` (a pre-2024 NVDA close is stated post-10:1). Charging commission
  would make the result at ``t`` depend on those later splits. A put's ROI is
  scale-free, so the level can only matter through commission; zero it and the
  level drops out. The model half-spread (a fraction of notional) is kept.
  ("Raw close" in ``put_roll`` means not dividend-adjusted, not unsplit.)
* **The signal window** is the trailing ``signal_years`` of the SPY calendar
  (``round(signal_years * 252)`` sessions ending at ``t``). A name is eligible
  only if at least 90% of those sessions have an aligned return row, and its
  metrics use exactly the rows dated inside that window: a gap never pulls
  older data in.
* **One eligible set per period, shared by every screen.** A name with any
  signal missing at ``t`` is out for all 13 screens, so their baskets, the
  universe ROI and the random baselines are comparable.
* **Paid** (per-name descriptives) means the roll made money: ``payoff >
  cost``, the same line as ``PutRollCycle.net > 0`` with commission zero. A
  payoff multiple is ``payoff / cost``.

Not claimed: a pass says the signal's picks beat trailing-volatility pricing
on the next roll. It does not say real puts would have paid (the premium is a
model price) and the universe is today's names, picked with hindsight
(``CAVEATS``).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np
import numpy.typing as npt
import pandas as pd
from pydantic import BaseModel, Field

from tail_lab.contracts.regime import RegimeLabel
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.metric_screen import (
    _ALL_METRIC_NAMES,
    _COMPOSITE,
    _spearman,
    fragility_from_returns,
)
from tail_lab.research.backtest.metric_screen import (
    _LABELS as _METRIC_LABELS,
)
from tail_lab.research.backtest.multiple_testing import (
    benjamini_hochberg,
    benjamini_hochberg_adjusted,
)
from tail_lab.research.backtest.put_roll import (
    DEFAULT_RATE,
    MODEL_PRICED_MAX_MONEYNESS_PCT,
    REALIZED_VOL_CAP,
    REALIZED_VOL_FLOOR,
    REALIZED_VOL_WINDOW,
    TRADING_DAYS_PER_WEEK,
    CycleFacts,
    PricingBasis,
    RollTerms,
    clamp_realized_vol,
    load_asof_series,
    price_model_roll,
    price_snapshot_ids,
)
from tail_lab.research.backtest.ranking import (
    BENCHMARK,
    FRAGILE_HIGH,
    _returns,
    composite_scores,
)
from tail_lab.research.backtest.strike_rule import ByMoneyness, StrikeRule
from tail_lab.research.dividends import DividendLookup, dividend_lookup
from tail_lab.research.regimes.timeline import (
    VIX_DATASET,
    compute_regime_timeline,
    load_vix_close,
    regime_on_or_before,
)

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.intp]
BoolArray = npt.NDArray[np.bool_]

#: The composite -- the pre-registered hypothesis -- and the six raw metrics.
COMPOSITE = _COMPOSITE
RAW_SCREENS: tuple[str, ...] = _ALL_METRIC_NAMES
#: The three plain-volatility measures, each screened in both directions.
VOL_MEASURES: tuple[str, ...] = ("rv20", "rv_long", "rv_ratio")
VOL_BASELINES: tuple[str, ...] = tuple(f"{m}_{d}" for m in VOL_MEASURES for d in ("high", "low"))
#: All 13 screens: 6 raw metrics, the composite, 6 volatility baselines.
SCREENS: tuple[str, ...] = (*RAW_SCREENS, COMPOSITE, *VOL_BASELINES)
#: Everything but the composite: BH-corrected together (12 tests).
EXPLORATORY_SCREENS: tuple[str, ...] = tuple(s for s in SCREENS if s != COMPOSITE)

_LABELS: dict[str, str] = {
    **_METRIC_LABELS,
    "rv20_high": "20-day vol, high",
    "rv20_low": "20-day vol, low",
    "rv_long_high": "Long-window vol, high",
    "rv_long_low": "Long-window vol, low",
    "rv_ratio_high": "Vol ratio (long / 20-day), high",
    "rv_ratio_low": "Vol ratio (long / 20-day), low",
}

#: Fewer periods or episodes than this and the result is not a test.
MIN_PERIODS = 24
MIN_EPISODES = 3
#: Share of the signal window that must have aligned return rows. New to this
#: module; NOT ``ranking.MIN_WINDOW_COVERAGE`` (a different question: how much
#: of a backtest window a name covers).
MIN_SIGNAL_COVERAGE = 0.90
#: A settlement on a close older than this many calendar days before expiry is
#: counted as stale (the name had a gap), and still settles on that close.
STALE_SETTLEMENT_DAYS = 3
#: The pre-registered hurdle for the composite's raw p, and the BH level.
P_THRESHOLD = 0.05
_BAND_PCT = (5.0, 95.0)
_TIE_TOLERANCE = 1e-12
_NOTIONAL = 1.0
#: Zero, deliberately -- see the module docstring. Tests that mutate this to
#: 0.65 must see the split-invariance test go red.
_COMMISSION_PER_CONTRACT = 0.0
#: Label that always accompanies the best volatility baseline: it is chosen after the fact.
BEST_BASELINE_LABEL = "best of 6 plain-volatility screens, picked after the fact"
#: Ids only; the texts live in the frontend's ``concepts.ts``.
CAVEATS: tuple[str, ...] = ("survivorship", "model_priced_signal")

VerdictStatus = Literal[
    "insufficient_data", "no_better_than_random", "crash_carried", "beats_random"
]
InsufficientReason = Literal["too_few_periods", "too_few_episodes", "no_test_window"]
ScreenRole = Literal["composite", "best_vol_baseline", "exploratory"]
_REASON_TEXT: dict[str, str] = {
    "too_few_periods": f"fewer than {MIN_PERIODS} periods",
    "too_few_episodes": f"fewer than {MIN_EPISODES} stress episodes",
    "no_test_window": "no test window",
}


# --------------------------------------------------------------------------
# Result models
# --------------------------------------------------------------------------


class RegimeExcess(BaseModel):
    regime: RegimeLabel
    n_periods: int
    excess_vs_universe: float


class ScreenSummary(BaseModel):
    """One screen over the whole test, on the shared periods and eligible set."""

    screen: str
    label: str
    kind: Literal["composite", "raw_metric", "vol_baseline"]
    n_periods: int
    basket_total_roi: float
    excess_vs_universe: float
    excess_vs_spy: float
    #: Spearman rank correlation of signal and ROI, averaged over the periods
    #: where it is defined; descriptive only (no p-value is used).
    mean_ic: float | None
    ic_hit_share: float | None
    n_ic_periods: int
    #: Share of random ``top_k`` baskets whose total ROI is >= the basket's,
    #: +1 smoothed: always in (0, 1].
    p_random: float
    #: BH q-value across the 12 exploratory screens; ``None`` for the
    #: composite, which is judged on its raw ``p_random``.
    p_bh: float | None
    significant_bh: bool | None
    excess_ex_best_period: float | None
    excess_ex_best_name_period: float | None
    excess_by_regime: list[RegimeExcess]


class PeriodRecord(BaseModel):
    """The composite's view of one rebalance period (the chart series)."""

    date: dt.date
    expiry_date: dt.date
    regime: RegimeLabel
    n_eligible: int
    top_k_assets: list[str]
    basket_roi: float
    universe_roi: float
    excess: float
    #: 5th / 95th percentile of random-basket minus universe ROI.
    band_low: float
    band_high: float
    spy_roi: float
    ic: float | None
    stress: bool


class Episode(BaseModel):
    start: dt.date
    end: dt.date
    n_periods: int


class SignalVerdict(BaseModel):
    """The composite's verdict, with the best volatility baseline beside it.

    ``best_vol_baseline`` is chosen after the fact, from six; it is never
    presented as a pre-chosen screen (the sentence says so:
    ``BEST_BASELINE_LABEL``).
    """

    status: VerdictStatus
    reasons: list[InsufficientReason]
    n_periods: int
    n_episodes: int
    composite_excess_vs_universe: float | None
    composite_p_random: float | None
    excess_ex_best_period: float | None
    excess_ex_best_name_period: float | None
    vol_baseline_as_good: bool
    best_vol_baseline: str | None
    best_vol_baseline_excess: float | None
    best_vol_baseline_p_bh: float | None
    sentence: str


class PaidGroup(BaseModel):
    """ "k of n paid, largest payoff multiple" for one group of name-periods."""

    n: int
    n_paid: int
    largest_payoff_multiple: float | None


class RegimeGroup(PaidGroup):
    regime: RegimeLabel


class NameScreenRecord(BaseModel):
    """What one screen did with one name. Descriptive: no p-values, no means."""

    screen: str
    #: ``best_vol_baseline`` marks the screen chosen after the fact.
    role: ScreenRole
    n_picked_periods: int
    #: Periods the name was in the basket / eligible but not in it.
    picked: PaidGroup
    other: PaidGroup
    #: Sum over the periods it was picked of (ROI - universe ROI) / top_k.
    contribution: float


class NameRecord(BaseModel):
    """One name across every screen. Eligibility is shared by all screens, so
    the eligible-period count and the regime breakdown live here, once."""

    symbol: str
    n_eligible_periods: int
    #: Over ALL the name's eligible periods, not split by picked.
    by_regime: list[RegimeGroup]
    screens: list[NameScreenRecord]


class Diagnostics(BaseModel):
    """What the collection dropped, counted. A silent drop is a bug."""

    n_symbols_requested: int = 0
    n_symbols_loaded: int = 0
    skipped_symbols: list[str] = Field(default_factory=list)
    n_rebalance_dates: int = 0
    #: Rebalance dates with fewer than ``2 * top_k`` eligible names (or no
    #: regime on or before them): no test that period.
    n_sat_out: int = 0
    n_no_regime: int = 0
    #: Name-periods with no close on the entry date.
    n_no_entry_close: int = 0
    #: The next three count only name-periods that ENTERED the test (after the
    #: signal drops and the period sit-out), so they describe the sample the
    #: statistics saw. Stale: settled on a close more than
    #: ``STALE_SETTLEMENT_DAYS`` before expiry.
    n_stale_settlements: int = 0
    n_name_periods_priced: int = 0
    n_beyond_model_depth: int = 0
    depth_beyond_10pct_share: float | None = None
    #: Moneyness rule only: name-periods removed for a floored premium or a
    #: strike deeper than ``MODEL_PRICED_MAX_MONEYNESS_PCT``.
    n_removed_floor_or_depth: int = 0
    #: Name-periods each signal removed (``None``), among otherwise-eligible.
    signal_removed: dict[str, int] = Field(default_factory=dict)


class Evaluation(BaseModel):
    """:func:`evaluate_screens`'s output, before provenance is attached."""

    test_start: dt.date | None
    test_end: dt.date | None
    n_periods: int
    #: Totals every screen is measured against; screen-independent because all
    #: screens share one eligible set per period.
    universe_total_roi: float | None
    spy_total_roi: float | None
    screens: list[ScreenSummary]
    periods: list[PeriodRecord]
    episodes: list[Episode]
    n_episodes: int
    verdict: SignalVerdict
    caveats: list[str] = Field(default_factory=lambda: list(CAVEATS))
    per_name: list[NameRecord]
    diagnostics: Diagnostics


# --------------------------------------------------------------------------
# The statistics: pure, planted-signal testable
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class PeriodInputs:
    """One rebalance period, as the statistics see it.

    ``signals[screen]`` is oriented so that a HIGHER value is picked (the
    collector flips the fragile-when-low metrics and the ``_low`` baselines).
    ``payoff`` and ``cost`` are per name, aligned with ``symbols``, where
    ``cost = notional + spread``. ``spy_payoff`` / ``spy_cost`` are the
    equal-premium SPY put under the same rule. ``spy_return`` is SPY's own
    price move over the period (expiry spot / entry spot - 1), which the
    beyond-volatility test regresses its per-period slope on.
    """

    #: Position on the rebalance calendar (0, 1, 2, ...), counting periods that
    #: sat out: two periods are adjacent only if their slots are consecutive.
    slot: int
    date: dt.date
    expiry_date: dt.date
    regime: RegimeLabel
    symbols: tuple[str, ...]
    signals: dict[str, FloatArray]
    payoff: FloatArray
    cost: FloatArray
    spy_payoff: float
    spy_cost: float
    spy_return: float


def _require_finite_outcomes(per: PeriodInputs) -> None:
    """A NaN payoff ranks as 'paid most' and a NaN spy_payoff reads as 'not
    stressed': neither may reach the statistics."""
    if not math.isfinite(per.spy_return):
        raise ValueError(f"{per.date}: spy_return must be finite")
    if not np.all(np.isfinite(per.payoff)) or not math.isfinite(per.spy_payoff):
        raise ValueError(f"{per.date}: payoff and spy_payoff must be finite")


def _validate_periods(periods: Sequence[PeriodInputs], top_k: int, n_random: int) -> None:
    if top_k < 1 or n_random < 1:
        raise ValueError("top_k and n_random must be >= 1")
    for per in periods:
        n = len(per.symbols)
        if n == 0 or per.payoff.shape != (n,) or per.cost.shape != (n,):
            raise ValueError(f"{per.date}: payoff/cost must match symbols")
        if not np.all(per.cost > 0) or per.spy_cost <= 0:
            raise ValueError(f"{per.date}: costs must be positive")
        _require_finite_outcomes(per)
        for screen in SCREENS:
            sig = per.signals.get(screen)
            if sig is None or sig.shape != (n,) or not np.all(np.isfinite(sig)):
                raise ValueError(f"{per.date}: signal {screen!r} missing, misshaped or non-finite")


def _total(payoff: float, cost: float) -> float | None:
    return payoff / cost - 1.0 if cost > 0 else None


def _period_ic(signal: FloatArray, roi: FloatArray) -> float | None:
    """Spearman rank correlation of signal and ROI across one period's names;
    ``None`` where ``_spearman`` has none (ROI constant -- common: no name
    paid --, signal constant, or fewer than 3 names)."""
    corr, _ = _spearman([float(v) for v in signal], [float(v) for v in roi], fragile_high=True)
    return None if corr is None else max(-1.0, min(1.0, corr))


@dataclass(frozen=True, eq=False)
class _PeriodStats:
    """Screen-independent per-period arrays, computed once."""

    roi: list[FloatArray]
    universe_roi: FloatArray
    universe_payoff: FloatArray
    universe_cost: FloatArray


def _period_stats(periods: Sequence[PeriodInputs]) -> _PeriodStats:
    roi = [per.payoff / per.cost - 1.0 for per in periods]
    return _PeriodStats(
        roi=roi,
        universe_roi=np.array([r.mean() for r in roi]),
        universe_payoff=np.array([per.payoff.sum() for per in periods]),
        universe_cost=np.array([per.cost.sum() for per in periods]),
    )


@dataclass(frozen=True, eq=False)
class _ScreenRun:
    """One screen's baskets over every period."""

    picks: list[IntArray]
    basket_payoff: FloatArray
    basket_cost: FloatArray
    basket_roi: FloatArray
    ic: list[float | None]
    #: The single largest name-period payoff inside a basket, with its cost.
    best_cell_payoff: float
    best_cell_cost: float


def _top_k(signal: FloatArray, top_k: int, rng: np.random.Generator) -> IntArray:
    """Highest ``top_k`` signal values. Exact ties (common for the composite's
    averaged ranks) are broken by the seeded ``rng``, never by input order:
    names arrive in symbol order, so "first wins" would hand a tied signal a
    systematic pick of the alphabet's front."""
    return np.lexsort((rng.random(signal.size), -signal))[:top_k]


def _run_screen(
    periods: Sequence[PeriodInputs],
    stats: _PeriodStats,
    screen: str,
    top_k: int,
    rng: np.random.Generator,
) -> _ScreenRun:
    picks = [_top_k(per.signals[screen], top_k, rng) for per in periods]
    cell_pay = [float(per.payoff[ix].max()) for per, ix in zip(periods, picks, strict=True)]
    best = int(np.argmax(cell_pay))
    best_ix = picks[best][int(np.argmax(periods[best].payoff[picks[best]]))]
    return _ScreenRun(
        picks=picks,
        basket_payoff=np.array(
            [per.payoff[ix].sum() for per, ix in zip(periods, picks, strict=True)]
        ),
        basket_cost=np.array([per.cost[ix].sum() for per, ix in zip(periods, picks, strict=True)]),
        basket_roi=np.array([r[ix].mean() for r, ix in zip(stats.roi, picks, strict=True)]),
        ic=[_period_ic(per.signals[screen], r) for per, r in zip(periods, stats.roi, strict=True)],
        best_cell_payoff=float(periods[best].payoff[best_ix]),
        best_cell_cost=float(periods[best].cost[best_ix]),
    )


@dataclass(frozen=True, eq=False)
class _RandomBaseline:
    """``n_random`` uniform ``top_k``-baskets per period, shared by every screen."""

    totals: FloatArray
    band_low: list[float]
    band_high: list[float]


def _random_baseline(
    periods: Sequence[PeriodInputs], stats: _PeriodStats, top_k: int, n_random: int, seed: int
) -> _RandomBaseline:
    """Draw ``n_random`` baskets in every period (seeded), accumulate each
    draw's total ROI across the test, and the per-period 5-95% band of its
    excess over the universe."""
    rng = np.random.default_rng(seed)
    num, den = np.zeros(n_random), np.zeros(n_random)
    lows: list[float] = []
    highs: list[float] = []
    for per, roi, universe in zip(periods, stats.roi, stats.universe_roi, strict=True):
        ix = rng.random((n_random, len(per.symbols))).argsort(axis=1)[:, :top_k]
        num += per.payoff[ix].sum(axis=1)
        den += per.cost[ix].sum(axis=1)
        low, high = np.percentile(roi[ix].mean(axis=1) - universe, _BAND_PCT)
        lows.append(float(low))
        highs.append(float(high))
    return _RandomBaseline(totals=num / den - 1.0, band_low=lows, band_high=highs)


def _p_random(basket_total: float, random_totals: FloatArray) -> float:
    """Share of random baskets at least as good as the basket, +1 smoothed."""
    at_least = int(np.sum(random_totals >= basket_total - _TIE_TOLERANCE))
    return (1 + at_least) / (1 + random_totals.size)


def _excess_without(run: _ScreenRun, stats: _PeriodStats, drop_period: int | None) -> float | None:
    mask = np.ones(run.basket_payoff.size, dtype=bool)
    if drop_period is not None:
        mask[drop_period] = False
    basket = _total(run.basket_payoff[mask].sum(), run.basket_cost[mask].sum())
    universe = _total(stats.universe_payoff[mask].sum(), stats.universe_cost[mask].sum())
    return None if basket is None or universe is None else basket - universe


def _excess_ex_best_period(run: _ScreenRun, stats: _PeriodStats) -> float | None:
    """Excess over all names after dropping the one period whose basket paid
    the most (the crash that may be carrying the result)."""
    return _excess_without(run, stats, int(np.argmax(run.basket_payoff)))


def _excess_ex_best_name_period(run: _ScreenRun, stats: _PeriodStats) -> float | None:
    """Excess over all names after removing the single largest name-period
    payoff from BOTH the basket and the universe (the name-period is treated
    as absent from the sample)."""
    pay, cost = run.best_cell_payoff, run.best_cell_cost
    basket = _total(run.basket_payoff.sum() - pay, run.basket_cost.sum() - cost)
    universe = _total(stats.universe_payoff.sum() - pay, stats.universe_cost.sum() - cost)
    return None if basket is None or universe is None else basket - universe


def _excess_by_regime(
    periods: Sequence[PeriodInputs], run: _ScreenRun, stats: _PeriodStats
) -> list[RegimeExcess]:
    out: list[RegimeExcess] = []
    for regime in ("calm", "elevated", "crisis"):
        sel = np.array([per.regime == regime for per in periods])
        if not sel.any():
            continue
        basket = run.basket_payoff[sel].sum() / run.basket_cost[sel].sum()
        universe = stats.universe_payoff[sel].sum() / stats.universe_cost[sel].sum()
        out.append(
            RegimeExcess(
                regime=regime, n_periods=int(sel.sum()), excess_vs_universe=basket - universe
            )
        )
    return out


def _screen_kind(screen: str) -> Literal["composite", "raw_metric", "vol_baseline"]:
    if screen == COMPOSITE:
        return "composite"
    return "vol_baseline" if screen in VOL_BASELINES else "raw_metric"


def _summarise_screen(
    screen: str,
    run: _ScreenRun,
    ctx: _EvalContext,
) -> ScreenSummary:
    stats = ctx.stats
    basket_total = run.basket_payoff.sum() / run.basket_cost.sum() - 1.0
    ics = [v for v in run.ic if v is not None]
    return ScreenSummary(
        screen=screen,
        label=_LABELS[screen],
        kind=_screen_kind(screen),
        n_periods=len(ctx.periods),
        basket_total_roi=float(basket_total),
        excess_vs_universe=float(basket_total - ctx.universe_total),
        excess_vs_spy=float(basket_total - ctx.spy_total),
        mean_ic=float(np.mean(ics)) if ics else None,
        ic_hit_share=float(np.mean([v > 0 for v in ics])) if ics else None,
        n_ic_periods=len(ics),
        p_random=_p_random(float(basket_total), ctx.baseline.totals),
        p_bh=None,
        significant_bh=None,
        excess_ex_best_period=_excess_ex_best_period(run, stats),
        excess_ex_best_name_period=_excess_ex_best_name_period(run, stats),
        excess_by_regime=_excess_by_regime(ctx.periods, run, stats),
    )


@dataclass(frozen=True, eq=False)
class _EvalContext:
    periods: Sequence[PeriodInputs]
    stats: _PeriodStats
    baseline: _RandomBaseline
    universe_total: float
    spy_total: float


def _apply_bh(summaries: list[ScreenSummary]) -> list[ScreenSummary]:
    """BH across the 12 exploratory screens; the composite keeps its raw p.
    Significance comes from the same step-up decision ``compare_metric_screens``
    uses, at the composite's level; the q-value is shown alongside for reading
    (see ``benjamini_hochberg_adjusted`` on why it is never the decision)."""
    explore = [s for s in summaries if s.screen in EXPLORATORY_SCREENS]
    pvals = [s.p_random for s in explore]
    qvals = benjamini_hochberg_adjusted(pvals)
    flags = benjamini_hochberg(pvals, alpha=P_THRESHOLD)
    updates = {
        s.screen: {"p_bh": q, "significant_bh": sig}
        for s, q, sig in zip(explore, qvals, flags, strict=True)
    }
    return [s.model_copy(update=updates.get(s.screen, {})) for s in summaries]


def _episode(run: Sequence[PeriodInputs]) -> Episode:
    return Episode(start=run[0].date, end=run[-1].expiry_date, n_periods=len(run))


def _episodes(periods: Sequence[PeriodInputs]) -> list[Episode]:
    """Maximal runs of periods in which the SPY put paid and whose slots on the
    REBALANCE calendar are consecutive. A period that sat out between two
    stressed ones breaks the run: nothing says the stress was continuous
    across a roll the test never observed."""
    episodes: list[Episode] = []
    run: list[PeriodInputs] = []
    for per in periods:
        stressed = per.spy_payoff > 0.0
        if stressed and run and per.slot == run[-1].slot + 1:
            run.append(per)
            continue
        if run:
            episodes.append(_episode(run))
        run = [per] if stressed else []
    if run:
        episodes.append(_episode(run))
    return episodes


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _vol_clause(v: SignalVerdict) -> str:
    """The best volatility baseline's excess beside the composite's -- always
    present, always labelled as picked after the fact."""
    if v.best_vol_baseline is None or v.best_vol_baseline_excess is None:
        return ""
    q = "" if v.best_vol_baseline_p_bh is None else f", BH p = {v.best_vol_baseline_p_bh:.3f}"
    sig = "n/a" if v.composite_excess_vs_universe is None else _pct(v.composite_excess_vs_universe)
    return (
        f"{BEST_BASELINE_LABEL} ({v.best_vol_baseline}{q}): "
        f"{_pct(v.best_vol_baseline_excess)} vs the signal's {sig}"
    )


def _verdict_lead(v: SignalVerdict, top_k: int) -> tuple[str, str]:
    """The status wording and the link to the volatility comparison (which
    differs by status and by ``vol_baseline_as_good``)."""
    p = "n/a" if v.composite_p_random is None else f"{v.composite_p_random:.3f}"
    excess = (
        "n/a" if v.composite_excess_vs_universe is None else _pct(v.composite_excess_vs_universe)
    )
    if v.status == "insufficient_data":
        why = ", ".join(_REASON_TEXT[r] for r in v.reasons)
        return f"Too little history to judge ({why})", "; for comparison, "
    if v.status == "no_better_than_random":
        lead = (
            f"The composite's top {top_k} did not beat random baskets "
            f"(p = {p}, excess over all names {excess})"
        )
        return lead, "; for comparison, "
    lead = (
        f"The composite's top {top_k} beat random baskets (p = {p}, excess over all names {excess})"
    )
    as_well = ", and a plain-volatility screen did as well: "
    if v.status == "crash_carried":
        ex = _pct(
            min(x for x in (v.excess_ex_best_period, v.excess_ex_best_name_period) if x is not None)
        )
        lead += f", but the edge depends on its best period or best single name-period (without the one that matters more: {ex})"
        return lead, (as_well if v.vol_baseline_as_good else "; for comparison, ")
    if v.vol_baseline_as_good:
        return lead, ", but a plain-volatility screen did as well: "
    return lead, ", and better than any plain-volatility screen: "


def _verdict_sentence(v: SignalVerdict, top_k: int) -> str:
    """The lead, then the volatility comparison -- only when there is a
    baseline to compare with (an empty run has none)."""
    lead, link = _verdict_lead(v, top_k)
    clause = _vol_clause(v)
    return lead + (link + clause if clause else "") + "."


def _insufficient_reasons(n_periods: int, n_episodes: int) -> list[InsufficientReason]:
    if n_periods == 0:
        return ["no_test_window"]
    reasons: list[InsufficientReason] = []
    if n_periods < MIN_PERIODS:
        reasons.append("too_few_periods")
    if n_episodes < MIN_EPISODES:
        reasons.append("too_few_episodes")
    return reasons


def _status(reasons: list[InsufficientReason], comp: ScreenSummary | None) -> VerdictStatus:
    """Checked in order: insufficient data, no better than random, edge carried
    by the best period, beats random."""
    if reasons or comp is None:
        return "insufficient_data"
    if comp.p_random >= P_THRESHOLD or comp.excess_vs_universe <= 0.0:
        return "no_better_than_random"
    ex = (comp.excess_ex_best_period, comp.excess_ex_best_name_period)
    if any(x is None or x <= 0.0 for x in ex):
        return "crash_carried"
    return "beats_random"


def _best_baseline(summaries: Sequence[ScreenSummary]) -> ScreenSummary | None:
    baselines = [s for s in summaries if s.kind == "vol_baseline"]
    return max(baselines, key=lambda s: s.excess_vs_universe) if baselines else None


def _build_verdict(
    summaries: Sequence[ScreenSummary], n_periods: int, n_episodes: int, top_k: int
) -> SignalVerdict:
    comp = next((s for s in summaries if s.screen == COMPOSITE), None)
    best = _best_baseline(summaries)
    reasons = _insufficient_reasons(n_periods, n_episodes)
    # `best` is the maximum, so a tie with the composite sets the flag.
    as_good = (
        comp is not None and best is not None and best.excess_vs_universe >= comp.excess_vs_universe
    )
    draft = SignalVerdict(
        status=_status(reasons, comp),
        reasons=reasons,
        n_periods=n_periods,
        n_episodes=n_episodes,
        composite_excess_vs_universe=comp.excess_vs_universe if comp else None,
        composite_p_random=comp.p_random if comp else None,
        excess_ex_best_period=comp.excess_ex_best_period if comp else None,
        excess_ex_best_name_period=comp.excess_ex_best_name_period if comp else None,
        vol_baseline_as_good=as_good,
        best_vol_baseline=best.screen if best else None,
        best_vol_baseline_excess=best.excess_vs_universe if best else None,
        best_vol_baseline_p_bh=best.p_bh if best else None,
        sentence="",
    )
    return draft.model_copy(update={"sentence": _verdict_sentence(draft, top_k)})


# ---- per-name descriptives ----


@dataclass
class _Group:
    n: int = 0
    n_paid: int = 0
    largest: float | None = None

    def add(self, multiple: float) -> None:
        self.n += 1
        self.n_paid += multiple > 1.0
        self.largest = multiple if self.largest is None else max(self.largest, multiple)

    def model(self) -> PaidGroup:
        return PaidGroup(n=self.n, n_paid=self.n_paid, largest_payoff_multiple=self.largest)


@dataclass
class _NameAcc:
    picked: _Group = field(default_factory=_Group)
    other: _Group = field(default_factory=_Group)
    contribution: float = 0.0


def _accumulate_screen(
    accs: dict[str, _NameAcc], periods: Sequence[PeriodInputs], run: _ScreenRun, stats: _PeriodStats
) -> None:
    for p, per in enumerate(periods):
        picked = set(run.picks[p].tolist())
        multiples = per.payoff / per.cost
        for i, symbol in enumerate(per.symbols):
            acc = accs.setdefault(symbol, _NameAcc())
            if i in picked:
                acc.picked.add(float(multiples[i]))
                acc.contribution += float(stats.roi[p][i] - stats.universe_roi[p]) / len(picked)
            else:
                acc.other.add(float(multiples[i]))


def _name_screen_record(screen: str, role: ScreenRole, acc: _NameAcc) -> NameScreenRecord:
    return NameScreenRecord(
        screen=screen,
        role=role,
        n_picked_periods=acc.picked.n,
        picked=acc.picked.model(),
        other=acc.other.model(),
        contribution=acc.contribution,
    )


def _regime_groups(periods: Sequence[PeriodInputs]) -> dict[str, dict[str, _Group]]:
    """Per name, per regime at entry: the paid-group over every period the
    name was eligible -- the same for all screens, so computed once."""
    out: dict[str, dict[str, _Group]] = {}
    for per in periods:
        multiples = per.payoff / per.cost
        for i, symbol in enumerate(per.symbols):
            out.setdefault(symbol, {}).setdefault(per.regime, _Group()).add(float(multiples[i]))
    return out


def _per_name(
    periods: Sequence[PeriodInputs],
    runs: dict[str, _ScreenRun],
    stats: _PeriodStats,
    best_baseline: str | None,
) -> list[NameRecord]:
    by_screen: dict[str, dict[str, _NameAcc]] = {}
    for screen in SCREENS:
        by_screen[screen] = {}
        _accumulate_screen(by_screen[screen], periods, runs[screen], stats)
    regimes = _regime_groups(periods)
    roles: dict[str, ScreenRole] = {
        s: "composite"
        if s == COMPOSITE
        else "best_vol_baseline"
        if s == best_baseline
        else "exploratory"
        for s in SCREENS
    }
    return [
        NameRecord(
            symbol=sym,
            n_eligible_periods=sum(g.n for g in regimes[sym].values()),
            by_regime=[
                RegimeGroup(regime=r, **regimes[sym][r].model().model_dump())
                for r in ("calm", "elevated", "crisis")
                if r in regimes[sym]
            ],
            screens=[_name_screen_record(s, roles[s], by_screen[s][sym]) for s in SCREENS],
        )
        for sym in sorted(regimes)
    ]


def _period_records(
    periods: Sequence[PeriodInputs],
    run: _ScreenRun,
    stats: _PeriodStats,
    baseline: _RandomBaseline,
) -> list[PeriodRecord]:
    return [
        PeriodRecord(
            date=per.date,
            expiry_date=per.expiry_date,
            regime=per.regime,
            n_eligible=len(per.symbols),
            top_k_assets=[per.symbols[i] for i in run.picks[p]],
            basket_roi=float(run.basket_roi[p]),
            universe_roi=float(stats.universe_roi[p]),
            excess=float(run.basket_roi[p] - stats.universe_roi[p]),
            band_low=baseline.band_low[p],
            band_high=baseline.band_high[p],
            spy_roi=per.spy_payoff / per.spy_cost - 1.0,
            ic=run.ic[p],
            stress=per.spy_payoff > 0.0,
        )
        for p, per in enumerate(periods)
    ]


def _empty_evaluation(top_k: int, diagnostics: Diagnostics) -> Evaluation:
    verdict = _build_verdict([], 0, 0, top_k)
    return Evaluation(
        test_start=None,
        test_end=None,
        n_periods=0,
        universe_total_roi=None,
        spy_total_roi=None,
        screens=[],
        periods=[],
        episodes=[],
        n_episodes=0,
        verdict=verdict,
        per_name=[],
        diagnostics=diagnostics,
    )


def evaluate_screens(
    periods: Sequence[PeriodInputs],
    *,
    top_k: int = 5,
    n_random: int = 2000,
    seed: int = 0,
    diagnostics: Diagnostics | None = None,
) -> Evaluation:
    """Every screen's walk-forward statistics from per-period inputs.

    Pure: no store, no clock. Every total and p-value is computed from
    ``payoff`` and ``cost`` as the module docstring defines them. Random
    baskets and signal ties are broken by the seeded ``seed`` stream (the
    baskets are shared by all screens). Raises
    ``ValueError`` on malformed periods (non-positive cost, a missing or
    non-finite signal).
    """
    _validate_periods(periods, top_k, n_random)
    diag = diagnostics or Diagnostics()
    if not periods:
        return _empty_evaluation(top_k, diag)
    stats = _period_stats(periods)
    baseline = _random_baseline(periods, stats, top_k, n_random, seed)
    universe_total = float(stats.universe_payoff.sum() / stats.universe_cost.sum() - 1.0)
    spy_total = float(
        sum(per.spy_payoff for per in periods) / sum(per.spy_cost for per in periods) - 1.0
    )
    ctx = _EvalContext(periods, stats, baseline, universe_total, spy_total)
    runs = {
        screen: _run_screen(periods, stats, screen, top_k, np.random.default_rng((seed, i)))
        for i, screen in enumerate(SCREENS)
    }
    summaries = _apply_bh([_summarise_screen(s, runs[s], ctx) for s in SCREENS])
    episodes = _episodes(periods)
    verdict = _build_verdict(summaries, len(periods), len(episodes), top_k)
    return Evaluation(
        test_start=periods[0].date,
        test_end=periods[-1].expiry_date,
        n_periods=len(periods),
        universe_total_roi=universe_total,
        spy_total_roi=spy_total,
        screens=summaries,
        periods=_period_records(periods, runs[COMPOSITE], stats, baseline),
        episodes=episodes,
        n_episodes=len(episodes),
        verdict=verdict,
        per_name=_per_name(periods, runs, stats, verdict.best_vol_baseline),
        diagnostics=diag,
    )


# --------------------------------------------------------------------------
# Collection: the point-in-time seam
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Calendar:
    """Everything fixed for the whole run."""

    dates: pd.DatetimeIndex  # SPY trading dates
    window: int  # signal window, trading days
    tenor_days: int
    top_k: int
    bench_ret: pd.Series
    vol_changes: pd.Series
    timeline: pd.Series
    base_terms: RollTerms  # no dividends; each name adds its own
    moneyness_rule: bool


@dataclass(frozen=True, eq=False)
class _Name:
    """One name's series, prepared once and sliced by position per date."""

    symbol: str
    dates: pd.DatetimeIndex
    px: FloatArray
    rv20: FloatArray
    #: Per SPY date: the name's own row for that date, or -1 without a close.
    own_pos: IntArray
    #: Per SPY date: the pre-metric eligibility (entry close, signal-window
    #: coverage, 20-day vol strictly inside the clamp).
    cheap_ok: BoolArray
    bench: pd.DataFrame  # aligned a (asset) / b (SPY) returns
    vol: pd.DataFrame  # aligned a (asset) / v (VIX change) returns
    terms: RollTerms


@dataclass(frozen=True, eq=False)
class Collected:
    """What :func:`collect_periods` found: the periods and what it dropped."""

    periods: list[PeriodInputs]
    diagnostics: Diagnostics
    loaded: tuple[str, ...]
    #: The bronze snapshots read: VIX, then SPY's and each loaded name's price
    #: snapshots, then the dividend snapshot -- collected as each was read, so
    #: citing them opens no table a second time.
    snapshot_ids: tuple[str, ...]


def _cheap_ok(
    px_dates: pd.DatetimeIndex,
    rv20: FloatArray,
    bench: pd.DataFrame,
    cal_dates: pd.DatetimeIndex,
    window: int,
) -> tuple[IntArray, BoolArray]:
    """Per SPY date: the name's row (or -1) and whether it passes the
    pre-metric eligibility. Every term at date ``i`` reads only rows ``<= i``:
    the coverage count is a cumulative sum differenced over the window."""
    n = len(cal_dates)
    own_pos = px_dates.get_indexer(cal_dates).astype(np.intp)
    has_close = own_pos >= 0
    aligned = np.concatenate([[0], np.cumsum(cal_dates.isin(bench.index))])
    covered = np.zeros(n, dtype=bool)
    ends = np.arange(window, n)
    covered[ends] = (aligned[ends + 1] - aligned[ends + 1 - window]) >= MIN_SIGNAL_COVERAGE * window
    sigma = np.full(n, np.nan)
    sigma[has_close] = rv20[own_pos[has_close]]
    inside = (sigma > REALIZED_VOL_FLOOR) & (sigma < REALIZED_VOL_CAP)
    return own_pos, has_close & covered & inside


def _prepare_name(
    symbol: str,
    prices: pd.Series,
    rv20: pd.Series,
    cal: _Calendar,
    dividends: DividendLookup,
) -> _Name:
    own_ret = _returns(prices)
    bench = pd.concat({"a": own_ret, "b": cal.bench_ret}, axis=1, sort=True).dropna()
    vol = pd.concat({"a": own_ret, "v": cal.vol_changes}, axis=1, sort=True).dropna()
    rv = rv20.to_numpy(dtype=float)
    index = pd.DatetimeIndex(prices.index)
    own_pos, ok = _cheap_ok(index, rv, bench, cal.dates, cal.window)
    return _Name(
        symbol=symbol,
        dates=index,
        px=prices.to_numpy(dtype=float),
        rv20=rv,
        own_pos=own_pos,
        cheap_ok=ok,
        bench=bench,
        vol=vol,
        terms=replace(cal.base_terms, basis=PricingBasis(dividends=dividends)),
    )


def _settle_index(name: _Name, p: int, cal: _Calendar) -> int:
    """Row of ``name``'s last close on or before the expiry of the roll entered
    at SPY position ``p``."""
    expiry = cal.dates[p + cal.tenor_days]
    return int(name.dates.searchsorted(expiry, side="right")) - 1


def _spy_return(spy: _Name, p: int, cal: _Calendar) -> float:
    """SPY's price move over the roll: settlement close / entry close - 1."""
    return float(spy.px[_settle_index(spy, p, cal)] / spy.px[int(spy.own_pos[p])] - 1.0)


def _roll_for(name: _Name, p: int, cal: _Calendar) -> tuple[CycleFacts, bool]:
    """Price and settle ``name``'s roll entered at SPY position ``p`` with the
    SAME function ``run_put_roll`` prices with, and whether the settlement was
    stale (more than ``STALE_SETTLEMENT_DAYS`` before expiry). Settles at the
    name's last close on or before the expiry date, so a gap is never a reason
    to drop a name after ``t``."""
    pos = int(name.own_pos[p])
    settle = _settle_index(name, p, cal)
    expiry = cal.dates[p + cal.tenor_days]
    facts = price_model_roll(
        float(name.px[pos]),
        clamp_realized_vol(float(name.rv20[pos])),
        float(name.px[settle]),
        cal.dates[p].date(),
        name.terms,
    )
    return facts, (expiry - name.dates[settle]).days > STALE_SETTLEMENT_DAYS


def _window(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """The rows of ``frame`` dated in ``[start, end]`` -- sliced by date, so
    nothing after ``end`` and nothing before ``start`` can enter."""
    lo = int(frame.index.searchsorted(start, side="left"))
    hi = int(frame.index.searchsorted(end, side="right"))
    return frame.iloc[lo:hi]


def _long_vol(returns: pd.Series) -> float | None:
    """Annualised volatility of the signal window's log returns."""
    if len(returns) < 2:
        return None
    value = float(np.log1p(returns).std(ddof=1) * math.sqrt(252.0))
    return value if math.isfinite(value) else None


def _measure(name: _Name, p: int, cal: _Calendar) -> dict[str, float | None]:
    """The six raw metrics and the three volatility measures for ``name`` at
    SPY position ``p``, from exactly the rows dated inside the signal window:
    the ``cal.window`` SPY sessions ending at ``p`` (the same sessions the
    90% coverage test counts). A name with gaps gets fewer rows, never older
    ones."""
    start, when = cal.dates[p - cal.window + 1], cal.dates[p]
    bench_window = _window(name.bench, start, when)
    vol_window = _window(name.vol, start, when)
    values = dict(fragility_from_returns(bench_window, vol_window))
    rv20 = float(name.rv20[name.own_pos[p]])
    rv_long = _long_vol(bench_window["a"])
    values["rv20"] = rv20
    values["rv_long"] = rv_long
    values["rv_ratio"] = None if rv_long is None else rv_long / rv20
    return values


def _is_complete(values: dict[str, float | None], diag: Diagnostics) -> bool:
    """False (and counted per signal) if any signal is missing: such a name is
    out for every screen, which keeps all 13 on one eligible set."""
    missing = [k for k, v in values.items() if v is None or not math.isfinite(v)]
    for key in missing:
        diag.signal_removed[key] = diag.signal_removed.get(key, 0) + 1
    return not missing


@dataclass(frozen=True, eq=False)
class _Priced:
    """A name priced at ``t``, and whether its settlement close was stale."""

    name: _Name
    facts: CycleFacts
    stale: bool


def _is_deep(facts: CycleFacts) -> bool:
    return (facts.entry_moneyness_pct or 0.0) > MODEL_PRICED_MAX_MONEYNESS_PCT


def _priced_candidates(
    names: Sequence[_Name], p: int, cal: _Calendar, diag: Diagnostics
) -> list[_Priced]:
    """Names passing the pre-metric checks, priced. Under a moneyness rule a
    floored premium or a strike past the model-priced depth is not a price and
    removes the name; under delta the depth is only a diagnostic (counted later,
    for the names that enter the test)."""
    out: list[_Priced] = []
    when = cal.dates[p]
    for name in names:
        if name.own_pos[p] < 0 and name.dates[0] <= when <= name.dates[-1]:
            diag.n_no_entry_close += 1
        if not name.cheap_ok[p]:
            continue
        facts, stale = _roll_for(name, p, cal)
        if cal.moneyness_rule and (_is_deep(facts) or facts.premium_floored):
            diag.n_removed_floor_or_depth += 1
            continue
        out.append(_Priced(name, facts, stale))
    return out


def _oriented_signals(
    rows: Sequence[dict[str, float | None]],
) -> dict[str, FloatArray]:
    """Every screen's signal across the eligible names, oriented so higher =
    picked: raw metrics by ``FRAGILE_HIGH``, the composite as ranked among
    these names, each volatility measure in both directions."""
    cols = {k: np.array([r[k] for r in rows], dtype=float) for k in (*RAW_SCREENS, *VOL_MEASURES)}
    signals = {m: cols[m] if FRAGILE_HIGH[m] else -cols[m] for m in RAW_SCREENS}
    signals[COMPOSITE] = np.array(composite_scores(rows), dtype=float)
    for vm in VOL_MEASURES:
        signals[f"{vm}_high"] = cols[vm]
        signals[f"{vm}_low"] = -cols[vm]
    return signals


def _count_entered(entered: Sequence[_Priced], diag: Diagnostics) -> None:
    """Count the name-periods that entered the test (and only those)."""
    diag.n_name_periods_priced += len(entered)
    diag.n_stale_settlements += sum(x.stale for x in entered)
    diag.n_beyond_model_depth += sum(_is_deep(x.facts) for x in entered)


def _collect_period(
    slot: int, p: int, names: Sequence[_Name], spy: _Name, cal: _Calendar, diag: Diagnostics
) -> PeriodInputs | None:
    date = cal.dates[p].date()
    try:
        regime = regime_on_or_before(cal.timeline, date)
    except LookupError:
        diag.n_no_regime += 1
        diag.n_sat_out += 1
        return None
    priced = _priced_candidates(names, p, cal, diag)
    measured = [(x, _measure(x.name, p, cal)) for x in priced]
    kept = [(x, v) for x, v in measured if _is_complete(v, diag)]
    if len(kept) < 2 * cal.top_k:
        diag.n_sat_out += 1
        return None
    _count_entered([x for x, _ in kept], diag)
    spy_facts, _ = _roll_for(spy, p, cal)
    return PeriodInputs(
        slot=slot,
        date=date,
        expiry_date=cal.dates[p + cal.tenor_days].date(),
        regime=regime,
        symbols=tuple(x.name.symbol for x, _ in kept),
        signals=_oriented_signals([v for _, v in kept]),
        payoff=np.array([x.facts.payoff for x, _ in kept]),
        cost=np.array([_NOTIONAL + x.facts.cost for x, _ in kept]),
        spy_payoff=spy_facts.payoff,
        spy_cost=_NOTIONAL + spy_facts.cost,
        spy_return=_spy_return(spy, p, cal),
    )


def _rebalance_positions(names: Sequence[_Name], n: int, tenor_days: int, top_k: int) -> list[int]:
    """SPY positions stepped every ``tenor_days`` from the first date with at
    least ``2 * top_k`` names passing the pre-metric checks, while a full roll
    still lies ahead."""
    if not names:
        return []
    last = n - 1 - tenor_days
    count = np.sum([nm.cheap_ok for nm in names], axis=0)
    first = np.flatnonzero(count[: max(last, -1) + 1] >= 2 * top_k)
    return [] if first.size == 0 else list(range(int(first[0]), last + 1, tenor_days))


def _load_names(
    store: LakeStore, symbols: Sequence[str], as_of: dt.date, cal: _Calendar
) -> tuple[list[_Name], list[str], list[str]]:
    """Load and prepare every symbol; also the price snapshot ids read for
    them, taken right after each read while the store still holds its
    resolution -- so citing them costs no second open of the table."""
    names: list[_Name] = []
    skipped: list[str] = []
    snapshot_ids: list[str] = []
    for symbol in dict.fromkeys(symbols):
        try:
            prices, rv20 = load_asof_series(store, symbol, as_of)
        except LookupError:
            skipped.append(symbol)
            continue
        snapshot_ids.extend(price_snapshot_ids(store, symbol, as_of))
        dividends = dividend_lookup(store, symbol, as_of).lookup
        names.append(_prepare_name(symbol, prices, rv20, cal, dividends))
    return names, skipped, snapshot_ids


def _make_calendar(
    store: LakeStore,
    as_of: dt.date,
    rule: StrikeRule,
    tenor_weeks: float,
    signal_years: float,
    top_k: int,
) -> tuple[_Calendar, pd.Series, pd.Series]:
    if tenor_weeks <= 0 or signal_years <= 0 or top_k < 1:
        raise ValueError("tenor_weeks, signal_years > 0 and top_k >= 1 required")
    window = round(signal_years * 252)
    if window <= REALIZED_VOL_WINDOW:
        raise ValueError("signal_years is too short for the 20-day volatility window")
    timeline = compute_regime_timeline(store, as_of=as_of)  # LookupError if no VIX
    vol_changes = load_vix_close(store, as_of=as_of).pct_change().dropna()
    try:
        spy_prices, spy_rv = load_asof_series(store, BENCHMARK, as_of)
    except LookupError as exc:
        raise LookupError(
            f"no {BENCHMARK.upper()} benchmark known as of {as_of.isoformat()}; "
            "the signal test cannot be run"
        ) from exc
    tenor_days = max(round(tenor_weeks * TRADING_DAYS_PER_WEEK), 1)
    base = RollTerms(
        basis=PricingBasis(),
        rate=DEFAULT_RATE,
        notional=_NOTIONAL,
        rule=rule,
        tenor_weeks=tenor_weeks,
        t_years=tenor_days / 252.0,
        commission_per_contract=_COMMISSION_PER_CONTRACT,
        spread_scale=1.0,
    )
    cal = _Calendar(
        dates=pd.DatetimeIndex(spy_prices.index),
        window=window,
        tenor_days=tenor_days,
        top_k=top_k,
        bench_ret=_returns(spy_prices),
        vol_changes=vol_changes,
        timeline=timeline,
        base_terms=base,
        moneyness_rule=isinstance(rule, ByMoneyness),
    )
    return cal, spy_prices, spy_rv


def collect_periods(
    store: LakeStore,
    *,
    symbols: Sequence[str],
    as_of: dt.date,
    rule: StrikeRule,
    tenor_weeks: float,
    signal_years: float,
    top_k: int,
) -> Collected:
    """Read ``symbols`` as of ``as_of`` and build one :class:`PeriodInputs`
    per rebalance date. Every price comes through ``load_asof_series``; each
    date's windows are sliced by position up to that date. Raises
    ``LookupError`` without VIX or the SPY benchmark, ``ValueError`` on
    non-positive parameters."""
    cal, spy_prices, spy_rv = _make_calendar(store, as_of, rule, tenor_weeks, signal_years, top_k)
    spy_dividends = dividend_lookup(store, BENCHMARK, as_of)
    spy = _prepare_name(BENCHMARK, spy_prices, spy_rv, cal, spy_dividends.lookup)
    snapshot_ids = [
        store.bronze_snapshot_id(VIX_DATASET, as_of),
        *price_snapshot_ids(store, BENCHMARK, as_of),
    ]
    names, skipped, name_snapshot_ids = _load_names(store, symbols, as_of, cal)
    positions = _rebalance_positions(names, len(cal.dates), cal.tenor_days, top_k)
    diag = Diagnostics(
        n_symbols_requested=len(dict.fromkeys(symbols)),
        n_symbols_loaded=len(names),
        skipped_symbols=skipped,
        n_rebalance_dates=len(positions),
    )
    maybe = (_collect_period(slot, p, names, spy, cal, diag) for slot, p in enumerate(positions))
    periods = [per for per in maybe if per is not None]
    if diag.n_name_periods_priced:
        diag.depth_beyond_10pct_share = diag.n_beyond_model_depth / diag.n_name_periods_priced
    # Order and content as `ranking.snapshot_ids_for`: VIX, price snapshots
    # (deduplicated), then the one dividend snapshot every name was priced with.
    dividend_ids = [spy_dividends.snapshot_id] if spy_dividends.snapshot_id else []
    ordered = dict.fromkeys([*snapshot_ids, *name_snapshot_ids, *dividend_ids])
    return Collected(periods, diag, tuple(n.symbol for n in names), tuple(ordered))
