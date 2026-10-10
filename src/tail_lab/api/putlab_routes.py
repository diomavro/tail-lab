"""HTTP routes for the Put Lab (``docs/END_STATE.md`` §1.2/§1.5). All public
GET, like the other cockpit reads — no auth to gate, model-priced data.

Three endpoints, all thin wrappers that resolve the as-of clock in UTC (so it
agrees with the ingest clock, as ``/api/vix/stretch`` does) and delegate to
``research.backtest.put_roll``:

- ``GET /api/putlab/backtest`` — one parameter set -> full equity curve,
  per-cycle P&L, and headline stats.
- ``GET /api/putlab/sweep`` — the strike x tenor grid of total return, one
  backtest per cell (the heatmap).
- ``GET /api/putlab/cadence`` — an asset's options-listing cadence, live-
  derived from the forward-collected chain snapshot when one exists
  (``research/cadence.py``), else the static fallback
  (``contracts/options_calendar.py``).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from tail_lab.api.memo import RefreshingMemo
from tail_lab.api.schemas import (
    BookPlanResponse,
    HedgeOverlayResponse,
    ModelPlanResponse,
    PortfolioRequest,
    SurfaceResponse,
    SweepResponse,
)
from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.config import get_settings
from tail_lab.contracts.cboe_strategy import DATASET as CBOE_STRATEGY_DATASET
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.contracts.option_chain import DATASET as OPTION_CHAIN_DATASET
from tail_lab.contracts.options_calendar import (
    OptionsCadence,
    screening_universe,
    universe_symbols,
)
from tail_lab.contracts.rates import DATASET as RATES_DATASET
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event
from tail_lab.research.accuracy import (
    RESIDUAL_PROGRAMS,
    AccuracyReport,
    compute_accuracy_report,
)
from tail_lab.research.backtest.contribution_plan import (
    COMPARATOR_KEYS,
    PlanRequest,
    compute_book_plan,
)
from tail_lab.research.backtest.hedge_overlay import (
    OVERLAY_PROGRAMS,
    OverlayDataMissing,
    compute_hedge_overlay,
)
from tail_lab.research.backtest.index_replication import (
    DEFAULT_DIVIDEND_YIELD,
    IndexReplicationResult,
    compute_index_replication,
)
from tail_lab.research.backtest.marks import MarkedSchedule, mark_schedule
from tail_lab.research.backtest.metric_screen import (
    MetricScreenComparison,
    compare_metric_screens,
)
from tail_lab.research.backtest.model_plan import MEASURED_VOL_GAP, compute_model_plan
from tail_lab.research.backtest.portfolio import PortfolioResult, run_portfolio
from tail_lab.research.backtest.put_roll import (
    DEFAULT_RATE,
    MODEL_PRICED_MAX_MONEYNESS_PCT,
    PutBacktestResult,
    annualized_return,
    compute_put_backtest,
    load_asof_series,
)
from tail_lab.research.backtest.ranking import BENCHMARK, UniverseRanking, rank_universe
from tail_lab.research.backtest.regime_verdict import RegimeVerdict, compute_regime_verdict
from tail_lab.research.backtest.roll_schedule import RollSchedule, build_roll_schedule
from tail_lab.research.backtest.strike_preview import StrikePreview, preview_strikes
from tail_lab.research.backtest.strike_rule import (
    MAX_TARGET_DELTA,
    MIN_TARGET_DELTA,
    ByDelta,
    ByMoneyness,
    StrikeRule,
)
from tail_lab.research.backtest.sweep import run_sweep
from tail_lab.research.cadence import resolve_cadence
from tail_lab.research.data_quality import DataQualityReport, assess_asset_quality
from tail_lab.research.dividends import dividend_lookup, dividend_snapshot_id
from tail_lab.research.regimes.timeline import RegimeTimelineView, compute_regime_view
from tail_lab.research.surface.reading import read_surface

router = APIRouter()
logger = logging.getLogger("tail_lab.api.putlab")

#: The universe ranking, keyed by (moneyness, tenor, years, as_of). A cold
#: screen takes ~25 s on the app's one shared CPU when nothing else runs, and
#: minutes when several overlap, so the memo serves a stale entry at once while
#: one background refresh recomputes it, and concurrent misses share a single
#: compute (api/memo.py). Fresh for 15 minutes -- longer than a screen, so a
#: steady reader does not keep one running; ingests land daily, and one shows
#: up after one refresh. Servable for the rest of the as-of day, whose date is
#: in the key (it changes at midnight UTC, when the daily warm re-screens).
_LEADERBOARD_CACHE: RefreshingMemo[tuple[float, float, float, str], UniverseRanking] = (
    RefreshingMemo(name="leaderboard", fresh_s=15 * 60.0, serve_stale_s=24 * 3600.0)
)
#: The screen the app opens on (the Workspace defaults, which are also the
#: leaderboard route's query defaults), warmed at startup -- every deploy
#: restarts the process with an empty memo -- and again each UTC day.
_WARM_SCREEN = (5.0, 4.0, 4.0)

#: Short-TTL memos of the single-name reads the Backtest cockpit refetches on
#: every OOM/tenor click. Bronze is immutable for a given as_of, so caching a
#: computed combo makes repeat/preset clicks instant server-side too. Keyed by
#: the endpoint's params + resolved as_of (mirrors the leaderboard pattern).
_BACKTEST_CACHE: dict[
    tuple[str, float, StrikeRule, float, float, str], tuple[float, PutBacktestResult]
] = {}
_BACKTEST_TTL_S = 120.0
_SWEEP_CACHE: dict[tuple[str, float, float, str], tuple[float, SweepResponse]] = {}
_SWEEP_TTL_S = 120.0
_REGIME_VERDICT_CACHE: dict[
    tuple[str, float, StrikeRule, float, float, str], tuple[float, RegimeVerdict]
] = {}
_REGIME_VERDICT_TTL_S = 120.0

#: Short-TTL memo of the (~35-backtest) metric bake-off, keyed by
#: (moneyness, tenor, years, top_k, as_of).
_METRIC_SCREEN_CACHE: dict[
    tuple[StrikeRule, float, float, int, str], tuple[float, MetricScreenComparison]
] = {}
_METRIC_SCREEN_TTL_S = 120.0

#: The accuracy panel's residual comes from replaying 438 monthly PPUT rolls,
#: which is far too expensive to redo per request and depends only on the
#: as-of date. Memoized separately from the report so every asset and every
#: parameter combination on the same day shares one replication.
_REPLICATION_CACHE: dict[str, tuple[float, list[IndexReplicationResult]]] = {}
_REPLICATION_TTL_S = 900.0
_ACCURACY_CACHE: dict[tuple[str, float, float, float, str], tuple[float, AccuracyReport]] = {}
_ACCURACY_TTL_S = 120.0

#: The Surface reads one ~20k-row chain session plus one OHLCV series; bronze
#: is immutable for a given as_of, so memoize on (asset, m, tenor, as_of).
_SURFACE_CACHE: dict[tuple[str, float, float, str], tuple[float, SurfaceResponse]] = {}
_SURFACE_TTL_S = 120.0
_PREVIEW_CACHE: dict[tuple[str, float, float, str], tuple[float, StrikePreview]] = {}
#: As the Surface: both read one chain session that changes once a day.
_PREVIEW_TTL_S = 120.0

#: The Book blends three programs at eleven hedge ratios over two windows
#: and three dividend yields -- a few seconds of pandas that depends only on
#: the as-of date, so it shares the replication cache's TTL.
_OVERLAY_CACHE: dict[str, tuple[float, HedgeOverlayResponse]] = {}

#: The contributions plan re-runs every rolling start at four yields (~2 s on
#: PPUT's 40 years); it depends only on its parameters and the as-of date.
#: Both plan endpoints share it; the key leads with the endpoint's name.
_PLAN_CACHE: dict[tuple[object, ...], tuple[float, Any]] = {}
#: Plan inputs are free-form numbers, so unlike the as-of-keyed caches these
#: could grow per keystroke; past this many entries the cache starts over.
_PLAN_CACHE_MAX = 256


def _plan_cache_get(key: tuple[object, ...]) -> Any | None:
    hit = _PLAN_CACHE.get(key)
    return hit[1] if hit is not None and hit[0] > time.monotonic() else None


def _plan_cache_put(key: tuple[object, ...], response: object) -> None:
    if len(_PLAN_CACHE) >= _PLAN_CACHE_MAX:
        _PLAN_CACHE.clear()
    _PLAN_CACHE[key] = (time.monotonic() + _REPLICATION_TTL_S, response)


def _plan_outcome_fields(plan: Any) -> dict[str, object]:
    """The window and rolling fields both plan endpoints log."""
    w, r = plan.window, plan.rolling
    return {
        "dividend_yield": plan.dividend_yield,
        "refusal": plan.refusal or "none",
        "window": f"{w.start}..{w.end}" if w else "none",
        "hedged_irr": w.hedged.irr if w else "none",
        "comparator_irr": w.comparator.irr if w else "none",
        "rolling_starts": r.n_starts if r else "none",
        "share_ahead": r.share_ahead if r else "none",
        "median_gap": r.median_gap if r else "none",
    }


#: A plan amount past this is a typo or an overflow probe, not a plan; it is
#: refused (422) before ``inf`` can reach the IRR solver.
MAX_PLAN_AMOUNT = 1e9


@lru_cache(maxsize=1)
def get_lake_store() -> LakeStore:
    # Overridden in tests via dependency_overrides (which bypasses this call).
    return _get_configured_lake_store()


def _resolve_as_of(as_of: dt.date | None) -> dt.date:
    return as_of or dt.datetime.now(dt.UTC).date()


def _snapshot(store: LakeStore, dataset: str, as_of: dt.date) -> str | None:
    """The bronze snapshot id used, for the run log — ``None`` (dropped from
    the line) if it can't be resolved, so logging never fails a request."""
    try:
        return store.bronze_snapshot_id(dataset, as_of)
    except LookupError:
        return None


def _rule(
    kind: Literal["moneyness", "delta"], moneyness_pct: float, target_delta: float
) -> StrikeRule:
    """The strike rule a request asks for. Only the active rule's parameter is
    read OR validated: the UI keeps both values, so a leftover one always
    arrives, and an out-of-range leftover must not refuse a valid request."""
    if kind == "moneyness":
        if not 0.0 < moneyness_pct < 100.0:
            raise HTTPException(status_code=422, detail="moneyness_pct must be in (0, 100)")
        return ByMoneyness(moneyness_pct)
    if not MIN_TARGET_DELTA <= target_delta <= MAX_TARGET_DELTA:
        raise HTTPException(
            status_code=422,
            detail=f"target_delta must be in [{MIN_TARGET_DELTA}, {MAX_TARGET_DELTA}]",
        )
    return ByDelta(target_delta)


def _cited_dividend_snapshot(ranking: UniverseRanking) -> str | None:
    """The dividend snapshot a ranking cites in its own ``snapshot_ids``."""
    return next((s for s in ranking.snapshot_ids if s.startswith("tiingo_eod@")), None)


def _log_run(
    store: LakeStore,
    event: str,
    *,
    asset: str,
    as_of: dt.date,
    params: Mapping[str, object],
    outputs: Mapping[str, object],
    extra_snapshots: Sequence[tuple[str, str]] = (),
) -> None:
    """Emit the §f structured run line for a backtest/mart build: identity
    (asset, as-of, bronze snapshot ids, code SHA), parameters, and headline
    outputs (docs/STANDARDS.md §f)."""
    snaps: dict[str, str | None] = {
        "ohlcv_snapshot": _snapshot(store, dataset_id(asset), as_of),
        # The dividend basis every model price used (None before the first
        # Tiingo ingest, when every q was an unknown 0).
        "dividend_snapshot": dividend_snapshot_id(store, as_of),
    }
    for field, ds in extra_snapshots:
        snaps[field] = _snapshot(store, ds, as_of)
    log_event(
        logger,
        event,
        asset=asset,
        as_of=as_of,
        **params,
        **snaps,
        code_sha=get_settings().code_sha,
        **outputs,
    )


@router.get("/api/putlab/backtest")
def putlab_backtest(
    *,
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    notional: float = Query(default=1000.0, gt=0, le=1_000_000),
    moneyness_pct: float = Query(
        default=5.0, description="Percent below spot for strike_rule=moneyness (0-100)."
    ),
    strike_rule: Literal["moneyness", "delta"] = Query(
        default="moneyness", description="Pick strikes by distance below spot or by put delta."
    ),
    target_delta: float = Query(
        default=0.10,
        description="Absolute put delta for strike_rule=delta (model delta at realised vol).",
    ),
    tenor_weeks: float = Query(default=4.0, gt=0, le=52),
    years: float = Query(default=4.0, gt=0, le=20, description="Lookback window."),
    as_of: dt.date | None = Query(default=None, description="Simulation date; defaults to today."),
    store: LakeStore = Depends(get_lake_store),
) -> PutBacktestResult:
    resolved = _resolve_as_of(as_of)
    rule = _rule(strike_rule, moneyness_pct, target_delta)
    cache_key = (asset, notional, rule, tenor_weeks, years, resolved.isoformat())
    hit = _BACKTEST_CACHE.get(cache_key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    try:
        result = compute_put_backtest(
            store,
            asset=asset,
            as_of=resolved,
            notional=notional,
            rule=rule,
            tenor_weeks=tenor_weeks,
            lookback_years=years,
        )
    except LookupError as exc:
        log_event(logger, "putlab.backtest.miss", asset=asset, as_of=resolved, reason=str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # The S&P 500 buy-and-hold hurdle over the same window, so the tape can draw
    # the horizon at which the annualized-so-far line crossed the market. The
    # pure engine has no benchmark; the route fills it before caching.
    _, result.benchmark_annualized = _benchmark_buy_and_hold(store, resolved, years)
    _BACKTEST_CACHE[cache_key] = (time.monotonic() + _BACKTEST_TTL_S, result)
    _log_run(
        store,
        "putlab.backtest",
        asset=asset,
        as_of=resolved,
        params={
            "moneyness_pct": rule.pct if isinstance(rule, ByMoneyness) else None,
            "strike_rule": rule.kind,
            "target_delta": rule.target if isinstance(rule, ByDelta) else None,
            "tenor_weeks": tenor_weeks,
            "years": years,
            "notional": notional,
        },
        outputs={
            "n_cycles": result.n_cycles,
            "roi_on_premium": result.roi_on_premium,
            "net_pnl": result.net_pnl,
            "q_source": result.q_source,
            "sharpe_ratio": result.sharpe_ratio,
            "benchmark_annualized": result.benchmark_annualized,
        },
    )
    return result


def _benchmark_buy_and_hold(
    store: LakeStore, as_of: dt.date, years: float
) -> tuple[float | None, float | None]:
    """S&P 500 buy-and-hold ``(total, annualized)`` over the sweep's window.

    Reads ``BENCHMARK``'s as-of price path, takes the trailing ``round(years*252)``
    daily closes (the same lookback the roll trades over), and returns
    ``last/first - 1`` and its geometric annualization. ``(None, None)`` if the
    benchmark's history is missing or too short — the sweep still succeeds, the
    heatmap just falls back to a two-way split at 0.
    """
    try:
        prices, _ = load_asof_series(store, BENCHMARK, as_of)
    except LookupError:
        return None, None
    window = prices.iloc[-round(years * 252) :]
    if len(window) < 2:
        return None, None
    first = float(window.iloc[0])
    last = float(window.iloc[-1])
    if first <= 0.0:
        return None, None
    total = last / first - 1.0
    return total, annualized_return(total, years)


@router.get("/api/putlab/sweep")
def putlab_sweep(
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    notional: float = Query(default=1000.0, gt=0, le=1_000_000),
    years: float = Query(default=4.0, gt=0, le=20),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> SweepResponse:
    resolved = _resolve_as_of(as_of)
    cache_key = (asset, notional, years, resolved.isoformat())
    hit = _SWEEP_CACHE.get(cache_key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    # Read the as-of price path ONCE, then roll every cell over it (one lake
    # read for the whole grid) instead of re-reading per cell.
    try:
        prices, realized_vol_proxy = load_asof_series(store, asset, resolved)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    cells = run_sweep(
        prices,
        realized_vol_proxy,
        asset=asset,
        as_of=resolved,
        notional=notional,
        years=years,
        dividends=dividend_lookup(store, asset, resolved).lookup,
    )
    if not cells:
        raise HTTPException(status_code=404, detail=f"no scorable window for {asset}")
    # The S&P 500 hurdle the heatmap colours against: buy-and-hold over the same
    # window, read once (not per cell). None if SPY history is missing as-of.
    bench_total, bench_annualized = _benchmark_buy_and_hold(store, resolved, years)
    _log_run(
        store,
        "putlab.sweep",
        asset=asset,
        as_of=resolved,
        params={"notional": notional, "years": years},
        outputs={"n_cells": len(cells), "benchmark_annualized": bench_annualized},
    )
    response = SweepResponse(
        asset=asset,
        as_of=resolved,
        notional=notional,
        lookback_years=years,
        cells=cells,
        benchmark_symbol=BENCHMARK,
        benchmark_annualized=bench_annualized,
        benchmark_total=bench_total,
        model_priced_max_moneyness_pct=MODEL_PRICED_MAX_MONEYNESS_PCT,
    )
    _SWEEP_CACHE[cache_key] = (time.monotonic() + _SWEEP_TTL_S, response)
    return response


@router.get("/api/putlab/regime-verdict")
def putlab_regime_verdict(
    *,
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    notional: float = Query(default=1000.0, gt=0, le=1_000_000),
    moneyness_pct: float = Query(
        default=5.0, description="Percent below spot for strike_rule=moneyness (0-100)."
    ),
    strike_rule: Literal["moneyness", "delta"] = Query(
        default="moneyness", description="Pick strikes by distance below spot or by put delta."
    ),
    target_delta: float = Query(
        default=0.10,
        description="Absolute put delta for strike_rule=delta (model delta at realised vol).",
    ),
    tenor_weeks: float = Query(default=4.0, gt=0, le=52),
    years: float = Query(default=4.0, gt=0, le=20),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> RegimeVerdict:
    resolved = _resolve_as_of(as_of)
    rule = _rule(strike_rule, moneyness_pct, target_delta)
    cache_key = (asset, notional, rule, tenor_weeks, years, resolved.isoformat())
    hit = _REGIME_VERDICT_CACHE.get(cache_key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    try:
        result = compute_regime_verdict(
            store,
            asset=asset,
            as_of=resolved,
            notional=notional,
            rule=rule,
            tenor_weeks=tenor_weeks,
            years=years,
        )
    except LookupError as exc:
        log_event(
            logger, "putlab.regime_verdict.miss", asset=asset, as_of=resolved, reason=str(exc)
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _REGIME_VERDICT_CACHE[cache_key] = (time.monotonic() + _REGIME_VERDICT_TTL_S, result)
    _log_run(
        store,
        "putlab.regime_verdict",
        asset=asset,
        as_of=resolved,
        params={
            "moneyness_pct": rule.pct if isinstance(rule, ByMoneyness) else None,
            "strike_rule": rule.kind,
            "target_delta": rule.target if isinstance(rule, ByDelta) else None,
            "tenor_weeks": tenor_weeks,
            "years": years,
            "notional": notional,
        },
        outputs={
            "verdict": result.verdict,
            "rule_hash": result.rule_hash,
            "n_slices": len(result.slices),
        },
        extra_snapshots=[("vix_snapshot", "vix")],
    )
    return result


@router.get("/api/putlab/cadence")
def putlab_cadence(
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> OptionsCadence:
    return resolve_cadence(store, asset, as_of=_resolve_as_of(as_of))


@router.get("/api/putlab/regimes")
def putlab_regimes(
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> RegimeTimelineView:
    """The market-regime history for the cockpit panel — the current regime
    plus run-length-encoded calm/elevated/crisis bands. VIX-based, escalated
    by credit-spread stress wherever a credit snapshot is also available
    (`research/regimes/timeline.py:compute_regime_timeline`)."""
    try:
        return compute_regime_view(store, as_of=_resolve_as_of(as_of))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/putlab/data-quality")
def putlab_data_quality(
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> DataQualityReport:
    """Flag bad ticks (spike-and-revert) and stale runs in the asset's price
    history, so a print error can't silently skew a verdict."""
    try:
        return assess_asset_quality(store, asset=asset, as_of=_resolve_as_of(as_of))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _cached_replications(store: LakeStore, as_of: dt.date) -> list[IndexReplicationResult]:
    """The reference replications for ``as_of``, memoized — including an empty
    list when the lake has none.

    A lake with no Cboe snapshot is a legitimate state (a fresh local dev
    environment), and replaying hundreds of rolls on every request just to
    rediscover that would be the slowest possible way to serve a null, so the
    empty result is cached with the same TTL as a hit.
    """
    key = as_of.isoformat()
    hit = _REPLICATION_CACHE.get(key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    results: list[IndexReplicationResult] = []
    for symbol in RESIDUAL_PROGRAMS:
        try:
            results.append(compute_index_replication(store, index_symbol=symbol, as_of=as_of))
        except (LookupError, KeyError) as exc:
            log_event(
                logger,
                "putlab.accuracy.no_residual",
                program=symbol,
                as_of=as_of,
                reason=str(exc),
            )
    _REPLICATION_CACHE[key] = (time.monotonic() + _REPLICATION_TTL_S, results)
    return results


@router.get("/api/putlab/accuracy")
def putlab_accuracy(
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    moneyness_pct: float = Query(default=5.0, gt=0, lt=100),
    tenor_weeks: float = Query(default=4.0, gt=0, le=52),
    years: float = Query(default=4.0, gt=0, le=20, description="Lookback window."),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> AccuracyReport:
    """How wrong the matching backtest is likely to be.

    The constitution requires a result to be shown with the known size of its
    error (README, "Accuracy is surfaced, not filed"), so this endpoint is the
    companion of ``/api/putlab/backtest`` and is never optional on the surface.
    It **does not 404**: every component degrades independently and a missing
    input is reported as a missing input, because a silent accuracy panel is
    indistinguishable from an accurate result.
    """
    resolved = _resolve_as_of(as_of)
    key = (asset, moneyness_pct, tenor_weeks, years, resolved.isoformat())
    hit = _ACCURACY_CACHE.get(key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]

    report = compute_accuracy_report(
        store,
        asset=asset,
        as_of=resolved,
        years=years,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        replications=_cached_replications(store, resolved),
    )
    _ACCURACY_CACHE[key] = (time.monotonic() + _ACCURACY_TTL_S, report)
    _log_run(
        store,
        "putlab.accuracy",
        asset=asset,
        as_of=resolved,
        params={"moneyness_pct": moneyness_pct, "tenor_weeks": tenor_weeks, "years": years},
        outputs={
            "expected_optimism": report.model.expected_optimism,
            "reference": report.model.reference,
            "applicability": report.model.applicability,
            "n_benchmarks": len(report.benchmarks),
            "data_quality_flags": report.data_quality_flags,
        },
    )
    return report


@router.post("/api/putlab/portfolio")
def putlab_portfolio(
    body: PortfolioRequest,
    store: LakeStore = Depends(get_lake_store),
) -> PortfolioResult:
    """Backtest a weighted mix of OOM-put legs as one blended hedge."""
    resolved = _resolve_as_of(body.as_of)
    try:
        result = run_portfolio(
            store,
            legs=body.legs,
            as_of=resolved,
            notional=body.notional,
            years=body.years,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    log_event(
        logger,
        "putlab.portfolio",
        dividend_snapshot=dividend_snapshot_id(store, resolved),
        as_of=resolved,
        n_legs=len(body.legs),
        notional=body.notional,
        years=body.years,
        code_sha=get_settings().code_sha,
        verdict=result.verdict,
        roi_on_premium=result.roi_on_premium,
        snapshots=";".join(result.snapshot_ids),
    )
    return result


@router.get("/api/putlab/universe")
def putlab_universe() -> list[OptionsCadence]:
    """The screening universe — every name the Put Lab covers, with its display
    name and listing cadence (the dropdown's source of truth)."""
    return screening_universe()


def _rank_cached(
    store: LakeStore, *, moneyness_pct: float, tenor_weeks: float, years: float, resolved: dt.date
) -> UniverseRanking:
    """The universe ranking, memoized. Shared by the leaderboard endpoint and
    the roll schedule so asking for the schedule never re-screens a universe
    the leaderboard just screened."""
    cache_key = (moneyness_pct, tenor_weeks, years, resolved.isoformat())
    try:
        return _LEADERBOARD_CACHE.get(cache_key, _screen(store, cache_key))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def warm_leaderboard(store: LakeStore) -> None:
    """Start screening the app's opening ranking in the background, so the
    first visitor after a deploy does not wait for it."""
    key = (*_WARM_SCREEN, _resolve_as_of(None).isoformat())
    _LEADERBOARD_CACHE.warm(key, _screen(store, key))


#: How long after midnight UTC the next day's ranking is warmed: past the date
#: change, so ``_resolve_as_of`` already answers the new day.
_WARM_AFTER_MIDNIGHT = dt.timedelta(minutes=1)


def warm_leaderboard_daily(
    store: LakeStore,
    *,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    sleep: Callable[[float], None] = time.sleep,
    rounds: int | None = None,
) -> None:
    """Warm the opening ranking now and again just after each UTC midnight.

    The as-of date is in the memo key, so without this the first visitor of
    every day would wait for the cold screen. Runs in a daemon thread for the
    life of the process; ``rounds`` bounds it for tests.
    """
    done = 0
    while rounds is None or done < rounds:
        warm_leaderboard(store)
        current = now()
        next_warm = (current + dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + _WARM_AFTER_MIDNIGHT
        log_event(logger, "putlab.leaderboard_warm_scheduled", next_warm=next_warm.isoformat())
        done += 1
        sleep((next_warm - current).total_seconds())


def _screen(
    store: LakeStore, key: tuple[float, float, float, str]
) -> Callable[[], UniverseRanking]:
    """The universe screen a memo key stands for, as a deferred compute."""
    moneyness_pct, tenor_weeks, years, as_of = key
    return lambda: rank_universe(
        store,
        symbols=universe_symbols(),
        as_of=dt.date.fromisoformat(as_of),
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
    )


@router.get("/api/putlab/roll-schedule/marked")
def putlab_roll_schedule_marked(
    moneyness_pct: float = Query(default=5.0, gt=0, lt=100),
    tenor_weeks: float = Query(default=4.0, gt=0, le=52),
    years: float = Query(default=4.0, gt=0, le=20),
    notional: float = Query(default=1000.0, gt=0, le=1_000_000),
    top_k: int = Query(default=10, ge=1, le=50),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> MarkedSchedule:
    """The same order intent, priced against the **listed** chain.

    ``/roll-schedule`` states what the screen concluded in the screen's own
    units: a strike at an exact real number no board lists, an expiry counted
    in trading days, and a Black-Scholes premium at a flat vol. This route adds
    what the market says about that same recommendation -- the listed strike
    and expiry it snaps to, the real bid/ask, the contract count the premium
    budget actually buys at the offer, and the market/model ratio.

    It also *refuses*. A leg with no bid, too little open interest, or a spread
    wider than half its mid comes back ``illiquid`` and carries no contract
    count, because the screen ranks on fragility and has never once asked
    whether anyone trades the contract it names.

    Coverage is partial on purpose (``docs/adr/0020`` collects 24 of the 70
    screened names), so a leg may come back ``not_collected``. That is a gap in
    the data, not a verdict on the trade, and the two are deliberately
    distinguishable.
    """
    schedule = putlab_roll_schedule(
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
        notional=notional,
        top_k=top_k,
        as_of=as_of,
        store=store,
    )
    resolved = _resolve_as_of(as_of)
    try:
        chain = store.read_bronze_as_of(OPTION_CHAIN_DATASET, resolved)
    except LookupError:
        # Before the first sweep, or as-of a date that predates it. Every leg
        # marks as not_collected, which is the honest answer.
        chain = pd.DataFrame()
    marked = mark_schedule(schedule, chain)
    log_event(
        logger,
        "api.putlab.roll_schedule_marked",
        as_of=resolved,
        schedule_id=marked.schedule_id,
        legs=len(marked.legs),
        quoted=marked.quoted_legs,
        quote_session=marked.quote_session,
    )
    return marked


@router.get("/api/putlab/roll-schedule")
def putlab_roll_schedule(
    moneyness_pct: float = Query(default=5.0, gt=0, lt=100),
    tenor_weeks: float = Query(default=4.0, gt=0, le=52),
    years: float = Query(default=4.0, gt=0, le=20),
    notional: float = Query(default=1000.0, gt=0, le=1_000_000),
    top_k: int = Query(default=10, ge=1, le=50),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> RollSchedule:
    """The top strategies as placeable order intent.

    This is the artefact that crosses ``docs/adr/0007``'s wall: it states what
    the screen concluded in units an executor can act on, and holds no
    credential and places no order. Read-only, like every other route here.

    Strikes and expiries are TARGETS -- the backtest strikes at an exact real
    number no chain lists and counts trading days rather than resolving a listed
    expiry -- and sizing is a premium budget rather than a contract count,
    because the model's premium is not the market's. The response says all of
    this in ``execution_notes`` so a consumer that never reads this docstring
    still cannot get it wrong.
    """
    resolved = _resolve_as_of(as_of)
    ranking = _rank_cached(
        store,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
        resolved=resolved,
    )

    # The model's own premium, for the top K only: one lake read per leg, so a
    # schedule costs a handful of reads on top of the (cached) screen rather
    # than one per universe member.
    wanted = sorted(
        (r for r in ranking.ranked if r.best_annualized is not None),
        key=lambda r: -(r.best_annualized or 0.0),
    )[:top_k]
    sigma_by_asset: dict[str, float] = {}
    q_by_asset: dict[str, float] = {}
    for row in wanted:
        try:
            _, realized_vol_proxy = load_asof_series(store, row.asset, resolved)
        except LookupError:  # pragma: no cover - it ranked, so it has data
            continue
        trailing = realized_vol_proxy.dropna()
        if not trailing.empty:
            sigma_by_asset[row.asset] = float(trailing.iloc[-1])
        # Read fresh, while `ranking` may be the memo's copy: for up to one memo
        # refresh (15 min) after a weekly Tiingo ingest, the quoted premium uses
        # the new q and the ranking that picked the leg the old one. Accepted:
        # q moves by basis points week to week, and the next refresh realigns.
        q_by_asset[row.asset] = dividend_lookup(store, row.asset, resolved).lookup(resolved).q

    schedule = build_roll_schedule(
        ranking.ranked,
        as_of=resolved,
        notional=notional,
        top_k=top_k,
        sigma_by_asset=sigma_by_asset,
        screen_moneyness_pct=moneyness_pct,
        screen_tenor_weeks=tenor_weeks,
        lookback_years=years,
        q_by_asset=q_by_asset,
    )
    log_event(
        logger,
        "putlab.roll_schedule",
        # Two bases can differ for one memo refresh after a weekly ingest: the
        # (possibly memoised) ranking that picked the legs, and the fresh read
        # the premiums were quoted on. Log both rather than claim one.
        ranking_dividend_snapshot=_cited_dividend_snapshot(ranking),
        premium_dividend_snapshot=dividend_snapshot_id(store, resolved),
        as_of=resolved,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
        notional=notional,
        code_sha=get_settings().code_sha,
        schedule_id=schedule.schedule_id,
        n_legs=len(schedule.legs),
    )
    return schedule


@router.get("/api/putlab/leaderboard")
def putlab_leaderboard(
    moneyness_pct: float = Query(default=5.0, gt=0, lt=100),
    tenor_weeks: float = Query(default=4.0, gt=0, le=52),
    years: float = Query(default=4.0, gt=0, le=20),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> UniverseRanking:
    """Rank the whole universe by return on premium at this strike/tenor —
    "which names' OOM puts got the best results" — each tagged with its
    cross-regime verdict."""
    resolved = _resolve_as_of(as_of)
    # Ranking the universe is a strike x tenor sweep per name, minutes cold;
    # the memo (stale-while-revalidate, single flight) keeps visits from
    # waiting on it. Shared with the roll-schedule route.
    ranking = _rank_cached(
        store,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
        resolved=resolved,
    )
    # `code_sha` is a deploy identity, not a screen result, so it is stamped
    # onto the (cached) ranking here rather than threaded into `rank_universe`
    # -- `research/` may not import `config` and the cache key must stay the
    # backtest params, not the running binary's SHA.
    code_sha = get_settings().code_sha
    ranking = ranking.model_copy(update={"code_sha": code_sha})
    log_event(
        logger,
        "putlab.leaderboard",
        # What THIS ranking priced with -- it may be the memo's copy, so a
        # fresh read could name a snapshot it never used.
        dividend_snapshot=_cited_dividend_snapshot(ranking),
        as_of=resolved,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
        code_sha=code_sha,
        n_ranked=len(ranking.ranked),
    )
    return ranking


@router.get("/api/putlab/metric-screen")
def putlab_metric_screen(
    moneyness_pct: float = Query(
        default=10.0, description="Percent below spot for strike_rule=moneyness (0-100)."
    ),
    strike_rule: Literal["moneyness", "delta"] = Query(
        default="moneyness", description="Pick strikes by distance below spot or by put delta."
    ),
    target_delta: float = Query(
        default=0.10,
        description="Absolute put delta for strike_rule=delta (model delta at realised vol).",
    ),
    tenor_weeks: float = Query(default=4.0, gt=0, le=52),
    years: float = Query(default=4.0, gt=0, le=20),
    top_k: int = Query(default=5, ge=2, le=15),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> MetricScreenComparison:
    """The metric bake-off (``docs/END_STATE.md`` §4 Q1) — which fragility
    metric best sorted realized OOM-put payoffs over the lookback. An in-sample
    cross-sectional association (a screen chooser), not a forward backtest."""
    resolved = _resolve_as_of(as_of)
    # ~35 backtests; deterministic given the immutable bronze, so a short TTL
    # cache makes repeat clicks instant (mirrors the leaderboard).
    rule = _rule(strike_rule, moneyness_pct, target_delta)
    cache_key = (rule, tenor_weeks, years, top_k, resolved.isoformat())
    hit = _METRIC_SCREEN_CACHE.get(cache_key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    try:
        comparison = compare_metric_screens(
            store,
            symbols=universe_symbols(),
            as_of=resolved,
            rule=rule,
            tenor_weeks=tenor_weeks,
            years=years,
            top_k=top_k,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _METRIC_SCREEN_CACHE[cache_key] = (time.monotonic() + _METRIC_SCREEN_TTL_S, comparison)
    winner = comparison.entries[0].metric if comparison.entries else None
    log_event(
        logger,
        "putlab.metric_screen",
        dividend_snapshot=dividend_snapshot_id(store, resolved),
        as_of=resolved,
        moneyness_pct=rule.pct if isinstance(rule, ByMoneyness) else None,
        strike_rule=rule.kind,
        target_delta=rule.target if isinstance(rule, ByDelta) else None,
        tenor_weeks=tenor_weeks,
        years=years,
        top_k=top_k,
        code_sha=get_settings().code_sha,
        n_entries=len(comparison.entries),
        winning_metric=winner,
    )
    return comparison


@router.get("/api/putlab/surface")
def putlab_surface(
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    moneyness_pct: float = Query(default=5.0, gt=0, lt=100),
    tenor_days: float = Query(default=30.0, gt=0, le=180),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> SurfaceResponse:
    """The Paretan Surface: the implied tail index at three anchors beside the
    realised one, the ladder, and the survival curves behind them.

    Reads ONLY ``option_chain_snapshot`` (one session) and ``ohlcv_<asset>``;
    never the optionsDX panel, whose load would exhaust the 1 GB VM. Anchors are
    picked by fixed moneyness (the payload says so); every refusal is a value.
    """
    resolved = _resolve_as_of(as_of)
    cache_key = (asset.lower(), moneyness_pct, tenor_days, resolved.isoformat())
    hit = _SURFACE_CACHE.get(cache_key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    try:
        chain = store.read_bronze_as_of(OPTION_CHAIN_DATASET, resolved)
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail="no option chain known as of that date"
        ) from exc
    try:
        prices: pd.Series | None = load_asof_series(store, asset, resolved)[0]
    except LookupError:
        prices = None
    # The name's own measured yield; without one, the old flat index-like
    # yield, labelled "assumed" so the page can say it is wrong for income names.
    dividend = dividend_lookup(store, asset, resolved).lookup(resolved)
    measured = dividend.source != "unknown"
    reading = read_surface(
        chain,
        prices,
        underlying=asset,
        moneyness_pct=moneyness_pct,
        tenor_days=tenor_days,
        r=DEFAULT_RATE,
        q=dividend.q if measured else DEFAULT_DIVIDEND_YIELD,
        q_source=dividend.source if measured else "assumed",
    )
    if reading is None:
        raise HTTPException(status_code=404, detail=f"no listed expiry for {asset} in the chain")
    code_sha = get_settings().code_sha
    chain_snapshot = _snapshot(store, OPTION_CHAIN_DATASET, resolved)
    ohlcv_snapshot = _snapshot(store, dataset_id(asset), resolved)
    log_event(
        logger,
        "api.putlab.surface",
        dividend_snapshot=dividend_snapshot_id(store, resolved),
        asset=asset,
        as_of=resolved,
        moneyness_pct=moneyness_pct,
        tenor_days=tenor_days,
        expiration=reading.expiration,
        chain_snapshot=chain_snapshot,
        ohlcv_snapshot=ohlcv_snapshot,
        code_sha=code_sha,
        anchors=len(reading.anchors.readings),
        dispersion=reading.anchors.dispersion,
        realised_alpha=reading.realised.alpha if reading.realised else None,
        alpha_gap=reading.alpha_gap,
    )
    response = SurfaceResponse(
        asset=asset,
        as_of=resolved,
        chain_snapshot=chain_snapshot,
        ohlcv_snapshot=ohlcv_snapshot,
        code_sha=code_sha,
        surface=reading,
    )
    _SURFACE_CACHE[cache_key] = (time.monotonic() + _SURFACE_TTL_S, response)
    return response


@router.get("/api/putlab/strike-preview")
def putlab_strike_preview(
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    target_delta: float = Query(default=0.10, ge=MIN_TARGET_DELTA, le=MAX_TARGET_DELTA),
    tenor_weeks: float = Query(default=4.0, gt=0, le=52),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> StrikePreview:
    """Today's strike for a delta target, at realised vol (what the backtest
    picks) beside the market's (our convention on the chain's implied vol).

    Reads ``ohlcv_<asset>``, the ``tiingo_eod`` dividend basis and, when the
    asset is collected, ``option_chain_snapshot`` (one session) -- never the
    optionsDX panel (ADR 0027 §5). A missing chain is a status, not an error.
    """
    resolved = _resolve_as_of(as_of)
    # One spelling of the name for the key AND the answer, so "SPY" after
    # "spy" is served exactly what a fresh "SPY" would be.
    asset = asset.lower()
    cache_key = (asset, target_delta, tenor_weeks, resolved.isoformat())
    hit = _PREVIEW_CACHE.get(cache_key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    try:
        prices, realized_vol = load_asof_series(store, asset, resolved)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=f"no price data for {asset}") from exc
    try:
        chain = store.read_bronze_as_of(OPTION_CHAIN_DATASET, resolved)
    except LookupError:
        chain = pd.DataFrame()
    dividend = dividend_lookup(store, asset, resolved).lookup(resolved)
    preview = preview_strikes(
        chain,
        prices,
        realized_vol,
        asset=asset,
        target_delta=target_delta,
        tenor_weeks=tenor_weeks,
        r=DEFAULT_RATE,
        q=dividend.q,
        q_source=dividend.source,
    )
    log_event(
        logger,
        "api.putlab.strike_preview",
        asset=asset,
        as_of=resolved,
        target_delta=target_delta,
        tenor_weeks=tenor_weeks,
        ohlcv_snapshot=_snapshot(store, dataset_id(asset), resolved),
        chain_snapshot=_snapshot(store, OPTION_CHAIN_DATASET, resolved)
        if not chain.empty
        else None,
        dividend_snapshot=dividend_snapshot_id(store, resolved),
        model_strike=preview.model.strike if preview.model else None,
        market_strike=preview.market.strike if preview.market else None,
        market_status=preview.market_status,
    )
    _PREVIEW_CACHE[cache_key] = (time.monotonic() + _PREVIEW_TTL_S, preview)
    return preview


@router.get("/api/putlab/hedge-overlay")
def putlab_hedge_overlay(
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> HedgeOverlayResponse:
    """The Book: Rodman's Paradox, tested: S&P 500 total return blended with each hedged
    Cboe program at every hedge ratio, and whether any mix out-grows both ends.

    Reads ONLY the ``cboe_strategy`` snapshot. 404 when the lake has none.
    """
    resolved = _resolve_as_of(as_of)
    key = resolved.isoformat()
    hit = _OVERLAY_CACHE.get(key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    try:
        overlay = compute_hedge_overlay(store, as_of=resolved)
    except OverlayDataMissing as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response = HedgeOverlayResponse(
        cboe_snapshot=_snapshot(store, CBOE_STRATEGY_DATASET, resolved),
        code_sha=get_settings().code_sha,
        overlay=overlay,
    )
    log_event(
        logger,
        "api.putlab.hedge_overlay",
        as_of=resolved,
        cboe_snapshot=response.cboe_snapshot,
        code_sha=response.code_sha,
        dividend_yield=overlay.dividend_yield,
        # Flat k=v fields, one set per program and window, so the log line
        # stays greppable: ``PPUT_cole_outcome=fails``.
        **{
            f"{p.index_symbol}_{w.key}_{field}": value
            for p in overlay.programs
            for w in p.windows
            for field, value in (
                ("start", w.start),
                ("end", w.end),
                ("clipped", w.clipped),
                ("requested", f"{w.requested_start}..{w.requested_end}"),
                ("best_weight", w.best_weight),
                ("margin", w.margin),
                ("outcome", w.outcome),
                # log_event drops None; "undefined" keeps the key on the line.
                ("outcome_risk_adjusted", w.outcome_risk_adjusted or "undefined"),
                (
                    "best_weight_risk_adjusted",
                    "undefined"
                    if w.best_weight_risk_adjusted is None
                    else w.best_weight_risk_adjusted,
                ),
                # The verdict at each assumed yield: "0.014:holds:0.000819,...".
                (
                    "sensitivity",
                    ",".join(
                        f"{r.dividend_yield}:{r.outcome}:{r.margin:.6f}" for r in w.sensitivity
                    ),
                ),
            )
        },
        # A window the lake could not cover is a result too (refusals are values).
        **{
            f"{p.index_symbol}_{key}_unavailable": reason
            for p in overlay.programs
            for key, reason in p.unavailable.items()
        },
        **{f"{symbol}_missing": reason for symbol, reason in overlay.missing.items()},
    )
    _OVERLAY_CACHE[key] = (time.monotonic() + _REPLICATION_TTL_S, response)
    return response


@router.get("/api/putlab/book-plan")
def putlab_book_plan(
    *,
    program: str = Query(default="PPUT", description="PPUT, PPUT3M or VXTH."),
    hedge_ratio: float = Query(default=0.5, ge=0, le=1),
    e0: float = Query(default=10_000.0, ge=0, le=MAX_PLAN_AMOUNT, description="Starting book."),
    monthly: float = Query(default=500.0, ge=0, le=MAX_PLAN_AMOUNT, description="Paid in monthly."),
    comparator: str = Query(default="spx"),
    horizon_years: int = Query(default=10, ge=1, le=30),
    start: dt.date | None = Query(default=None, description="Illustrated window start."),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> BookPlanResponse:
    """The Book in contributions mode (docs/adr/0027 §3): the same monthly
    cash into a self-financed hedged book or a comparator, with the
    rolling-start verdict. 404 only when the lake cannot answer at all; a
    comparator it cannot offer is a refusal inside a 200."""
    if program not in OVERLAY_PROGRAMS:
        raise HTTPException(status_code=422, detail=f"program must be one of {OVERLAY_PROGRAMS}")
    if comparator not in COMPARATOR_KEYS:
        raise HTTPException(status_code=422, detail=f"comparator must be one of {COMPARATOR_KEYS}")
    if e0 + monthly <= 0:
        raise HTTPException(status_code=422, detail="a plan needs E0 or a monthly amount")
    resolved = _resolve_as_of(as_of)
    request = PlanRequest(program, hedge_ratio, e0, monthly, comparator, horizon_years, start)
    key = ("book", request, resolved.isoformat())
    cached = _plan_cache_get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    try:
        plan = compute_book_plan(store, request, as_of=resolved)
    except OverlayDataMissing as exc:
        log_event(
            logger,
            "api.putlab.book_plan",
            as_of=resolved,
            program=program,
            hedge_ratio=hedge_ratio,
            e0=e0,
            monthly=monthly,
            comparator=comparator,
            horizon_years=horizon_years,
            start=start or "default",
            refused_404=str(exc),
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response = BookPlanResponse(
        cboe_snapshot=_snapshot(store, CBOE_STRATEGY_DATASET, resolved),
        rates_snapshot=_snapshot(store, RATES_DATASET, resolved),
        code_sha=get_settings().code_sha,
        plan=plan,
    )
    log_event(
        logger,
        "api.putlab.book_plan",
        as_of=resolved,
        cboe_snapshot=response.cboe_snapshot,
        rates_snapshot=response.rates_snapshot or "none",
        code_sha=response.code_sha,
        program=program,
        hedge_ratio=hedge_ratio,
        e0=e0,
        monthly=monthly,
        comparator=comparator,
        horizon_years=horizon_years,
        start=start or "default",
        **_plan_outcome_fields(plan),
        window_outcome=plan.window.outcome if plan.window else "none",
        by_yield=",".join(f"{y.dividend_yield}:{y.share_ahead:.4f}" for y in plan.by_yield)
        or "none",
    )
    _plan_cache_put(key, response)
    return response


@router.get("/api/putlab/book-plan/model")
def putlab_book_plan_model(
    *,
    moneyness_pct: float = Query(default=5.0, description="5 or 10: the measured depths."),
    e0: float = Query(default=10_000.0, gt=0, le=MAX_PLAN_AMOUNT, description="Starting book."),
    monthly: float = Query(default=500.0, gt=0, le=MAX_PLAN_AMOUNT, description="Paid in monthly."),
    put_share: float = Query(default=1.0, gt=0, le=1, description="Share of X spent on puts."),
    horizon_years: int = Query(default=10, ge=1, le=30),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> ModelPlanResponse:
    """The Book's model-priced, contribution-funded plan (docs/adr/0027 §2-3):
    a share of each month's X buys S&P 500 puts priced by Black-Scholes at the
    VIX plus the measured skew gap. 404 when SPX or the VIX is absent."""
    if moneyness_pct not in MEASURED_VOL_GAP:
        raise HTTPException(
            status_code=422, detail=f"moneyness_pct must be one of {sorted(MEASURED_VOL_GAP)}"
        )
    resolved = _resolve_as_of(as_of)
    key = ("model", moneyness_pct, e0, monthly, put_share, horizon_years, resolved.isoformat())
    cached = _plan_cache_get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    try:
        plan = compute_model_plan(
            store,
            as_of=resolved,
            moneyness_pct=moneyness_pct,
            e0=e0,
            monthly=monthly,
            horizon_years=horizon_years,
            put_share=put_share,
        )
    except OverlayDataMissing as exc:
        log_event(
            logger,
            "api.putlab.book_plan_model",
            as_of=resolved,
            moneyness_pct=moneyness_pct,
            e0=e0,
            monthly=monthly,
            put_share=put_share,
            horizon_years=horizon_years,
            refused_404=str(exc),
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response = ModelPlanResponse(
        cboe_snapshot=_snapshot(store, CBOE_STRATEGY_DATASET, resolved),
        vix_snapshot=_snapshot(store, "vix", resolved),
        code_sha=get_settings().code_sha,
        plan=plan,
    )
    log_event(
        logger,
        "api.putlab.book_plan_model",
        as_of=resolved,
        cboe_snapshot=response.cboe_snapshot,
        vix_snapshot=response.vix_snapshot,
        code_sha=response.code_sha,
        moneyness_pct=moneyness_pct,
        vol_gap=plan.accuracy.vol_gap,
        e0=e0,
        monthly=monthly,
        put_share=put_share,
        horizon_years=horizon_years,
        **_plan_outcome_fields(plan),
    )
    _plan_cache_put(key, response)
    return response
