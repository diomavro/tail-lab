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
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from tail_lab.api.schemas import PortfolioRequest, SweepResponse
from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.config import get_settings
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.contracts.option_chain import DATASET as OPTION_CHAIN_DATASET
from tail_lab.contracts.options_calendar import (
    OptionsCadence,
    screening_universe,
    universe_symbols,
)
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event
from tail_lab.research.accuracy import (
    RESIDUAL_PROGRAMS,
    AccuracyReport,
    compute_accuracy_report,
)
from tail_lab.research.backtest.index_replication import (
    IndexReplicationResult,
    compute_index_replication,
)
from tail_lab.research.backtest.marks import MarkedSchedule, mark_schedule
from tail_lab.research.backtest.metric_screen import (
    MetricScreenComparison,
    compare_metric_screens,
)
from tail_lab.research.backtest.portfolio import PortfolioResult, run_portfolio
from tail_lab.research.backtest.put_roll import (
    PutBacktestResult,
    annualized_return,
    compute_put_backtest,
    load_asof_series,
)
from tail_lab.research.backtest.ranking import BENCHMARK, UniverseRanking, rank_universe
from tail_lab.research.backtest.regime_verdict import RegimeVerdict, compute_regime_verdict
from tail_lab.research.backtest.roll_schedule import RollSchedule, build_roll_schedule
from tail_lab.research.backtest.sweep import MODEL_PRICED_MAX_MONEYNESS_PCT, run_sweep
from tail_lab.research.cadence import resolve_cadence
from tail_lab.research.data_quality import DataQualityReport, assess_asset_quality
from tail_lab.research.regimes.timeline import RegimeTimelineView, compute_regime_view

router = APIRouter()
logger = logging.getLogger("tail_lab.api.putlab")

#: Short-TTL memo of the (35-backtest) leaderboard, keyed by
#: (moneyness, tenor, years, as_of); the read cache keeps it fresh underneath.
_LEADERBOARD_CACHE: dict[tuple[float, float, float, str], tuple[float, UniverseRanking]] = {}
_LEADERBOARD_TTL_S = 120.0

#: Short-TTL memos of the single-name reads the Backtest cockpit refetches on
#: every OOM/tenor click. Bronze is immutable for a given as_of, so caching a
#: computed combo makes repeat/preset clicks instant server-side too. Keyed by
#: the endpoint's params + resolved as_of (mirrors the leaderboard pattern).
_BACKTEST_CACHE: dict[
    tuple[str, float, float, float, float, str], tuple[float, PutBacktestResult]
] = {}
_BACKTEST_TTL_S = 120.0
_SWEEP_CACHE: dict[tuple[str, float, float, str], tuple[float, SweepResponse]] = {}
_SWEEP_TTL_S = 120.0
_REGIME_VERDICT_CACHE: dict[
    tuple[str, float, float, float, float, str], tuple[float, RegimeVerdict]
] = {}
_REGIME_VERDICT_TTL_S = 120.0

#: Short-TTL memo of the (~35-backtest) metric bake-off, keyed by
#: (moneyness, tenor, years, top_k, as_of).
_METRIC_SCREEN_CACHE: dict[
    tuple[float, float, float, int, str], tuple[float, MetricScreenComparison]
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


def _log_run(
    store: LakeStore,
    event: str,
    *,
    asset: str,
    as_of: dt.date,
    params: Mapping[str, Any],
    outputs: Mapping[str, Any],
    extra_snapshots: Sequence[tuple[str, str]] = (),
) -> None:
    """Emit the §f structured run line for a backtest/mart build: identity
    (asset, as-of, bronze snapshot ids, code SHA), parameters, and headline
    outputs (docs/STANDARDS.md §f)."""
    snaps: dict[str, str | None] = {"ohlcv_snapshot": _snapshot(store, dataset_id(asset), as_of)}
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
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    notional: float = Query(default=1000.0, gt=0, le=1_000_000),
    moneyness_pct: float = Query(default=5.0, gt=0, lt=100),
    tenor_weeks: float = Query(default=4.0, gt=0, le=52),
    years: float = Query(default=4.0, gt=0, le=20, description="Lookback window."),
    as_of: dt.date | None = Query(default=None, description="Simulation date; defaults to today."),
    store: LakeStore = Depends(get_lake_store),
) -> PutBacktestResult:
    resolved = _resolve_as_of(as_of)
    cache_key = (asset, notional, moneyness_pct, tenor_weeks, years, resolved.isoformat())
    hit = _BACKTEST_CACHE.get(cache_key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    try:
        result = compute_put_backtest(
            store,
            asset=asset,
            as_of=resolved,
            notional=notional,
            moneyness_pct=moneyness_pct,
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
            "moneyness_pct": moneyness_pct,
            "tenor_weeks": tenor_weeks,
            "years": years,
            "notional": notional,
        },
        outputs={
            "n_cycles": result.n_cycles,
            "roi_on_premium": result.roi_on_premium,
            "net_pnl": result.net_pnl,
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
        prices, iv_proxy = load_asof_series(store, asset, resolved)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    cells = run_sweep(prices, iv_proxy, asset=asset, as_of=resolved, notional=notional, years=years)
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
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    notional: float = Query(default=1000.0, gt=0, le=1_000_000),
    moneyness_pct: float = Query(default=5.0, gt=0, lt=100),
    tenor_weeks: float = Query(default=4.0, gt=0, le=52),
    years: float = Query(default=4.0, gt=0, le=20),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> RegimeVerdict:
    resolved = _resolve_as_of(as_of)
    cache_key = (asset, notional, moneyness_pct, tenor_weeks, years, resolved.isoformat())
    hit = _REGIME_VERDICT_CACHE.get(cache_key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    try:
        result = compute_regime_verdict(
            store,
            asset=asset,
            as_of=resolved,
            notional=notional,
            moneyness_pct=moneyness_pct,
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
            "moneyness_pct": moneyness_pct,
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
    """The market-regime history (VIX-based) for the cockpit panel — the
    current regime plus run-length-encoded calm/elevated/crisis bands."""
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
    hit = _LEADERBOARD_CACHE.get(cache_key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    try:
        ranking = rank_universe(
            store,
            symbols=universe_symbols(),
            as_of=resolved,
            moneyness_pct=moneyness_pct,
            tenor_weeks=tenor_weeks,
            years=years,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _LEADERBOARD_CACHE[cache_key] = (time.monotonic() + _LEADERBOARD_TTL_S, ranking)
    return ranking


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
    for row in wanted:
        try:
            _, iv_proxy = load_asof_series(store, row.asset, resolved)
        except LookupError:  # pragma: no cover - it ranked, so it has data
            continue
        trailing = iv_proxy.dropna()
        if not trailing.empty:
            sigma_by_asset[row.asset] = float(trailing.iloc[-1])

    schedule = build_roll_schedule(
        ranking.ranked,
        as_of=resolved,
        notional=notional,
        top_k=top_k,
        sigma_by_asset=sigma_by_asset,
        screen_moneyness_pct=moneyness_pct,
        screen_tenor_weeks=tenor_weeks,
        lookback_years=years,
    )
    log_event(
        logger,
        "putlab.roll_schedule",
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
    # Ranking the universe is a strike x tenor sweep per name; the result is
    # deterministic given the (immutable) bronze, so a short TTL cache makes
    # repeat clicks instant. Shared with the roll-schedule route.
    ranking = _rank_cached(
        store,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
        resolved=resolved,
    )
    log_event(
        logger,
        "putlab.leaderboard",
        as_of=resolved,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
        code_sha=get_settings().code_sha,
        n_ranked=len(ranking.ranked),
    )
    return ranking


@router.get("/api/putlab/metric-screen")
def putlab_metric_screen(
    moneyness_pct: float = Query(default=10.0, gt=0, lt=100),
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
    cache_key = (moneyness_pct, tenor_weeks, years, top_k, resolved.isoformat())
    hit = _METRIC_SCREEN_CACHE.get(cache_key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    try:
        comparison = compare_metric_screens(
            store,
            symbols=universe_symbols(),
            as_of=resolved,
            moneyness_pct=moneyness_pct,
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
        as_of=resolved,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
        top_k=top_k,
        code_sha=get_settings().code_sha,
        n_entries=len(comparison.entries),
        winning_metric=winner,
    )
    return comparison
