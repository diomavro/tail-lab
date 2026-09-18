"""The fragility screen (``docs/END_STATE.md`` §1.1, §4 Q1).

The strategy is timing-free: don't predict *when* disorder comes — hold OOM-put
convexity continuously on the **most fragile** names, and the fragility itself
produces the payoff when the (untimed) dislocation hits. So this ranks the
universe by fragility, not by market-timing.

Fragility is measured by six complementary sensitivity metrics — five against
the market (SPY): downside beta (how far it falls with the market),
co-skewness (crash-direction co-movement), co-kurtosis (tail amplification),
tail beta (beta restricted to the market's worst days), and downside
capture (the ratio of mean returns on the market's down days); plus one
against the volatility factor: vol beta (co-movement with VIX changes,
independent of what the benchmark's own price did that day) — combined
into a cross-sectional composite. Alongside each
name's fragility, it runs the model-priced put backtest, so you can see whether
fragility actually translates into put payoff (the §4 Q1 question) and spot
"cheap fragility": fragile names whose puts the market still underprices.

The VIX regime timeline and the market benchmark are read once and reused
across every name.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.hypothesis import Verdict
from tail_lab.contracts.options_calendar import cadence_for
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.put_roll import (
    load_asof_series,
    run_put_roll,
)
from tail_lab.research.backtest.regime_verdict import regime_breakdown
from tail_lab.research.backtest.sizing import SizingMode
from tail_lab.research.backtest.sweep import (
    MODEL_PRICED_SWEEP_MONEYNESS,
    best_point,
    run_sweep,
)
from tail_lab.research.metrics.co_kurtosis import co_kurtosis
from tail_lab.research.metrics.co_skewness import co_skewness
from tail_lab.research.metrics.downside_beta import downside_beta
from tail_lab.research.metrics.downside_capture import downside_capture
from tail_lab.research.metrics.tail_beta import tail_beta
from tail_lab.research.metrics.vol_beta import vol_beta
from tail_lab.research.regimes.timeline import compute_regime_timeline, load_vix_close

#: The market benchmark the fragility metrics are measured against.
BENCHMARK = "spy"

#: How much of the asked-for window a name must actually cover to be ranked.
#:
#: ``run_put_roll`` now paces ``annualized_return`` by the span actually traded
#: (fixed 2026-09-09), so the specific distortion this was written for — a name
#: listed two years ago getting its two-year loss annualized as if over four,
#: shrinking it toward zero and floating it up a ranking sorted on that number —
#: no longer happens.
#:
#: The floor stays, because coverage and pacing are different objections. A name
#: with one year of history against a four-year request is now annualized
#: honestly, and is still not comparable to names measured over four: it has not
#: been through the same regimes, and one lucky or unlucky year annualizes into a
#: confident-looking rate. Correct pacing removes the arithmetic flatterer, not
#: the sampling problem.
#:
#: 0.9 rather than 1.0 because a real listing's first bars are ragged and the
#: IV proxy needs a warm-up, so an exact match never happens.
MIN_WINDOW_COVERAGE = 0.9

#: Metrics that feed the composite fragility score. Co-kurtosis is computed and
#: shown on the screen but DELIBERATELY EXCLUDED here: co-kurtosis *with the
#: benchmark* rewards names that co-move with SPY's own tails, so it scores
#: broad indices (SPY/DIA/QQQ) as most "fragile" -- backwards for a single-name
#: OOM-put screen, which wants names that fall *harder* than the market. The
#: metric bake-off (``metric_screen.py``, END_STATE §4 Q1) confirmed it as the
#: worst screen in-sample, but the exclusion is structural, not curve-fit. Vol
#: beta IS included: it measures co-movement with the volatility factor (VIX
#: changes) rather than the benchmark's own tails, so it does not share
#: co-kurtosis's index-flattering bias. The remaining five are equal-weighted.
#: One place, so the screen and the bake-off composite can never diverge.
COMPOSITE_METRICS: tuple[str, ...] = (
    "downside_beta",
    "co_skewness",
    "tail_beta",
    "downside_capture",
    "vol_beta",
)


class RankedAsset(BaseModel):
    """One universe member: its fragility vs the market + the put backtest."""

    asset: str
    name: str
    spot: float
    # fragility (vs SPY); None if too little overlapping history to estimate
    downside_beta: float | None
    co_skewness: float | None
    co_kurtosis: float | None
    tail_beta: float | None
    downside_capture: float | None
    vol_beta: float | None  # fragility vs the volatility factor (VIX changes), not SPY
    fragility_score: float | None  # cross-sectional composite, 0..1 (1 = most fragile)
    # put backtest at the screened strike/tenor
    roi_on_premium: float
    annualized_return: float  # geometric annualization of roi_on_premium over `years`
    verdict: Verdict
    hit_rate: float
    biggest_payoff_mult: float
    n_cycles: int
    # "model" (BlackScholesPricer) or "market" (a real listed quote) -- see
    # PutBacktestResult.priced_from. `rank_universe` never passes a
    # PricingBasis today, so this is always "model"; the field exists so the
    # screen states that plainly (AGENT_TODO.md, "the ranked screen is still
    # model-priced") instead of leaving a model-priced ROI looking identical
    # to a market-priced one, and so it flips honestly the day this orchestrator
    # is wired to real quotes.
    priced_from: Literal["model", "market"]
    # The best this name gets when its parameters are chosen well: the argmax
    # of its own strike x tenor sweep, bounded to the strikes the model can
    # actually price (docs/adr/0018). This is the headline the ranking leads
    # with, and the parameters a click on the row lands on -- so the number the
    # user reads in the table is the number the backtest then shows them.
    # ``None`` when the window was too short to score any cell.
    #
    # EVERY ``best_*`` field below is measured at the SAME cell. That is the
    # point of the prefix: a recommendation is a whole strategy, and quoting an
    # argmax return beside a hit rate from the screened cell would describe two
    # different strategies on one line. Do not add a ``best_*`` field sourced
    # from anywhere but the run at (best_moneyness_pct, best_tenor_weeks).
    best_annualized: float | None = None
    best_moneyness_pct: float | None = None
    best_tenor_weeks: float | None = None
    best_roi_on_premium: float | None = None
    best_hit_rate: float | None = None
    best_biggest_payoff_mult: float | None = None
    best_n_cycles: int | None = None
    best_verdict: Verdict | None = None


class UniverseRanking(BaseModel):
    as_of: dt.date
    moneyness_pct: float
    tenor_weeks: float
    lookback_years: float
    notional: float
    ranked: list[RankedAsset]


def _returns(prices: pd.Series) -> pd.Series:
    return prices.pct_change().dropna()


def _frac_rank(values: list[float | None], *, fragile_high: bool) -> dict[int, float]:
    """Cross-sectional fragility rank of one metric, 0..1 (1 = most fragile).
    ``fragile_high`` = a higher raw value means more fragile."""
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(present) < 2:
        return {i: 0.5 for i, _ in present}
    ordered = sorted(present, key=lambda x: x[1], reverse=fragile_high)
    return {i: 1.0 - pos / (len(ordered) - 1) for pos, (i, _) in enumerate(ordered)}


def _load_benchmark_returns(store: LakeStore, as_of: dt.date) -> pd.Series | None:
    """SPY's returns as of ``as_of``, or ``None`` if no benchmark snapshot exists yet
    (a name's own fragility columns come back ``None`` in that case, not a hard failure)."""
    try:
        bench_prices, _ = load_asof_series(store, BENCHMARK, as_of)
    except LookupError:
        return None
    return _returns(bench_prices)


def _score_and_sort(rows: list[RankedAsset]) -> list[RankedAsset]:
    """Composite fragility score (average of ``COMPOSITE_METRICS``' cross-sectional
    ranks) then sort most-fragile-first; rows too thin to score sort last."""
    ranks = {
        "downside_beta": _frac_rank([r.downside_beta for r in rows], fragile_high=True),
        "co_skewness": _frac_rank([r.co_skewness for r in rows], fragile_high=False),
        "tail_beta": _frac_rank([r.tail_beta for r in rows], fragile_high=True),
        "downside_capture": _frac_rank([r.downside_capture for r in rows], fragile_high=True),
        "vol_beta": _frac_rank([r.vol_beta for r in rows], fragile_high=False),
    }
    for i, r in enumerate(rows):
        parts = [ranks[m][i] for m in COMPOSITE_METRICS if i in ranks[m]]
        r.fragility_score = sum(parts) / len(parts) if parts else None
    rows.sort(
        key=lambda r: r.fragility_score if r.fragility_score is not None else -1.0, reverse=True
    )
    return rows


@dataclass(frozen=True)
class _RankContext:
    """Everything ``_rank_one`` needs for a single name, computed once per
    ``rank_universe`` call and shared read-only across the thread pool."""

    store: LakeStore
    as_of: dt.date
    notional: float
    moneyness_pct: float
    tenor_weeks: float
    years: float
    timeline: pd.Series
    bench_ret: pd.Series | None
    vol_changes: pd.Series
    lookback_days: int


def _fragility(
    asset_prices: pd.Series, bench_ret: pd.Series | None, lookback_days: int
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    if bench_ret is None:
        return None, None, None, None, None
    aligned = pd.concat({"a": _returns(asset_prices), "b": bench_ret}, axis=1).dropna()
    window = aligned.iloc[-lookback_days:]
    a, b = window["a"], window["b"]

    def _safe(fn: Callable[[pd.Series, pd.Series], float]) -> float | None:
        try:
            return fn(a, b)
        except ValueError:
            return None

    return (
        _safe(downside_beta),
        _safe(co_skewness),
        _safe(co_kurtosis),
        _safe(tail_beta),
        _safe(downside_capture),
    )


def _vol_beta_for(
    asset_prices: pd.Series, vol_changes: pd.Series, lookback_days: int
) -> float | None:
    """Unlike ``_fragility``, this needs no SPY benchmark -- the regressor is
    the VIX change series, always present here."""
    aligned = pd.concat({"a": _returns(asset_prices), "v": vol_changes}, axis=1).dropna()
    window = aligned.iloc[-lookback_days:]
    try:
        return vol_beta(window["a"], window["v"])
    except ValueError:
        return None


def _rank_one(symbol: str, ctx: _RankContext) -> RankedAsset | None:
    try:
        prices, realized_vol_proxy = load_asof_series(ctx.store, symbol, ctx.as_of)
        result = run_put_roll(
            prices,
            realized_vol_proxy,
            asset=symbol,
            as_of=ctx.as_of,
            notional=ctx.notional,
            moneyness_pct=ctx.moneyness_pct,
            tenor_weeks=ctx.tenor_weeks,
            lookback_years=ctx.years,
        )
    except LookupError:
        return None  # no data / too short a window for this name -> skip
    # Drop a name whose history does not cover the window being asked about,
    # rather than comparing an annualized-over-four-years figure that only
    # saw two (see MIN_WINDOW_COVERAGE).
    covered_days = (result.cycles[-1].expiry_date - result.cycles[0].entry_date).days
    if covered_days / 365.25 < ctx.years * MIN_WINDOW_COVERAGE:
        return None
    # The heatmap's grid, over the price path already in hand, but only the
    # strikes the model can actually price: `best_point` is bounded to them
    # (the raw argmax is drawn to the deepest cell, where the premium rounds
    # to nothing -- docs/adr/0018), so sweeping deeper here would compute
    # numbers this function is required to discard.
    best = best_point(
        run_sweep(
            prices,
            realized_vol_proxy,
            asset=symbol,
            as_of=ctx.as_of,
            notional=ctx.notional,
            years=ctx.years,
            moneyness_grid=MODEL_PRICED_SWEEP_MONEYNESS,
        )
    )
    db, cs, ck, tb, dc = _fragility(prices, ctx.bench_ret, ctx.lookback_days)
    vb = _vol_beta_for(prices, ctx.vol_changes, ctx.lookback_days)
    _, verdict = regime_breakdown(result.cycles, ctx.timeline)
    # One more roll, at the winning cell, so every ``best_*`` figure comes from
    # the same strategy. ~2% on top of the 55-cell sweep already run.
    best_run = None
    best_verdict: Verdict | None = None
    if best is not None:
        try:
            best_run = run_put_roll(
                prices,
                realized_vol_proxy,
                asset=symbol,
                as_of=ctx.as_of,
                notional=ctx.notional,
                moneyness_pct=best.moneyness_pct,
                tenor_weeks=best.tenor_weeks,
                lookback_years=ctx.years,
                include_curves=False,
            )
            _, best_verdict = regime_breakdown(best_run.cycles, ctx.timeline)
        except LookupError:  # pragma: no cover - the sweep already scored it
            best_run = None
    return RankedAsset(
        asset=symbol,
        name=cadence_for(symbol).name,
        spot=result.spot,
        downside_beta=db,
        co_skewness=cs,
        co_kurtosis=ck,
        tail_beta=tb,
        downside_capture=dc,
        vol_beta=vb,
        fragility_score=None,  # filled in cross-sectionally below
        roi_on_premium=result.roi_on_premium,
        annualized_return=result.annualized_return,
        verdict=verdict,
        hit_rate=result.hit_rate,
        biggest_payoff_mult=result.biggest_payoff_mult,
        n_cycles=result.n_cycles,
        priced_from=result.priced_from,
        best_annualized=best.annualized_return if best else None,
        best_moneyness_pct=best.moneyness_pct if best else None,
        best_tenor_weeks=best.tenor_weeks if best else None,
        best_roi_on_premium=best_run.roi_on_premium if best_run else None,
        best_hit_rate=best_run.hit_rate if best_run else None,
        best_biggest_payoff_mult=best_run.biggest_payoff_mult if best_run else None,
        best_n_cycles=best_run.n_cycles if best_run else None,
        best_verdict=best_verdict,
    )


def rank_universe(
    store: LakeStore,
    *,
    symbols: tuple[str, ...],
    as_of: dt.date,
    moneyness_pct: float,
    tenor_weeks: float,
    years: float,
    notional: float = 1000.0,
    sizing_mode: SizingMode | None = None,
) -> UniverseRanking:
    """Rank ``symbols`` by fragility (most fragile first), each with its put
    backtest at the given strike/tenor.

    Point-in-time: the regime timeline, the benchmark, and every price path are
    read as of ``as_of``. A symbol with no OHLCV as-of, or too short a window
    for one roll, is skipped. Raises ``LookupError`` only if the VIX regime
    timeline itself is missing.

    ``sizing_mode``, when given, resolves the per-name premium budget and
    ``notional`` is ignored; it resolves with ``n_legs=len(symbols)`` since
    every screened name is priced independently, so a wealth fraction is split
    evenly across the whole universe being ranked. Leaving it ``None`` (the
    default) uses ``notional`` for every name exactly as before -- see
    ``research/backtest/sizing.py``.
    """
    budget = (
        sizing_mode.resolve(n_legs=len(symbols))
        if sizing_mode is not None and symbols
        else notional
    )
    timeline = compute_regime_timeline(store, as_of=as_of)
    lookback_days = round(years * 252)
    # compute_regime_timeline above already proved a VIX snapshot exists as of
    # `as_of`, so this cannot raise LookupError here.
    vol_changes = load_vix_close(store, as_of=as_of).pct_change().dropna()
    bench_ret = _load_benchmark_returns(store, as_of)
    ctx = _RankContext(
        store=store,
        as_of=as_of,
        notional=budget,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
        timeline=timeline,
        bench_ret=bench_ret,
        vol_changes=vol_changes,
        lookback_days=lookback_days,
    )

    # Each name is an independent lake read + roll; the S3 read releases the
    # GIL, so a bounded thread pool cuts the cold warm-up.
    with ThreadPoolExecutor(max_workers=8) as pool:
        # `is not None`, not `filter(None, ...)`: the latter drops every falsy
        # value, and only stays correct while RankedAsset happens to define no
        # __bool__/__len__. Making the predicate say what it means costs
        # nothing and removes a trap for whoever adds one. (Design review, #60.)
        ranked_or_none = pool.map(partial(_rank_one, ctx=ctx), symbols)
        rows = [row for row in ranked_or_none if row is not None]

    # Composite fragility: higher downside beta / tail beta / downside capture,
    # and *lower* (more negative) co-skewness / vol beta, all mean more fragile.
    # Average the cross-sectional ranks of the COMPOSITE_METRICS present
    # (co-kurtosis is ranked for display but excluded from the blend -- see
    # COMPOSITE_METRICS). Most fragile first; names with no fragility estimate
    # sort last.
    rows = _score_and_sort(rows)
    return UniverseRanking(
        as_of=as_of,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=years,
        notional=budget,
        ranked=rows,
    )
