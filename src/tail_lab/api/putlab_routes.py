"""HTTP routes for the Put Lab (``docs/END_STATE.md`` §1.2/§1.5). All public
GET, like the other cockpit reads — no auth to gate, model-priced data.

Three endpoints, all thin wrappers that resolve the as-of clock in UTC (so it
agrees with the ingest clock, as ``/api/vix/stretch`` does) and delegate to
``research.backtest.put_roll``:

- ``GET /api/putlab/backtest`` — one parameter set -> full equity curve,
  per-cycle P&L, and headline stats.
- ``GET /api/putlab/sweep`` — the strike x tenor grid of total return, one
  backtest per cell (the heatmap).
- ``GET /api/putlab/cadence`` — the static options-listing cadence for an
  asset (``contracts/options_calendar.py``).
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query

from tail_lab.api.schemas import SweepCell, SweepResponse
from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.contracts.options_calendar import OptionsCadence, cadence_for
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.put_roll import (
    PutBacktestResult,
    compute_put_backtest,
    load_asof_series,
    run_put_roll,
)

router = APIRouter()

#: The heatmap axes — kept in one place so the sweep endpoint and (later) the
#: frontend agree on the grid. Moneyness in % OOM, tenor in weeks.
SWEEP_MONEYNESS: tuple[float, ...] = (2, 4, 6, 8, 10, 12, 15, 18, 22)
SWEEP_TENORS_WEEKS: tuple[float, ...] = (1, 2, 4, 8, 12)


@lru_cache(maxsize=1)
def get_lake_store() -> LakeStore:
    # Overridden in tests via dependency_overrides (which bypasses this call).
    return _get_configured_lake_store()


def _resolve_as_of(as_of: dt.date | None) -> dt.date:
    return as_of or dt.datetime.now(dt.UTC).date()


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
    try:
        return compute_put_backtest(
            store,
            asset=asset,
            as_of=_resolve_as_of(as_of),
            notional=notional,
            moneyness_pct=moneyness_pct,
            tenor_weeks=tenor_weeks,
            lookback_years=years,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/putlab/sweep")
def putlab_sweep(
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    notional: float = Query(default=1000.0, gt=0, le=1_000_000),
    years: float = Query(default=4.0, gt=0, le=20),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> SweepResponse:
    resolved = _resolve_as_of(as_of)
    # Read the as-of price path ONCE, then roll every cell over it (45 model
    # backtests, a single lake read) instead of re-reading per cell.
    try:
        prices, iv_proxy = load_asof_series(store, asset, resolved)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    cells: list[SweepCell] = []
    for tenor in SWEEP_TENORS_WEEKS:
        for moneyness in SWEEP_MONEYNESS:
            try:
                res = run_put_roll(
                    prices,
                    iv_proxy,
                    asset=asset,
                    as_of=resolved,
                    notional=notional,
                    moneyness_pct=moneyness,
                    tenor_weeks=tenor,
                    lookback_years=years,
                )
            except LookupError:
                # A tenor too long for the available window yields no cell,
                # rather than failing the whole grid.
                continue
            cells.append(
                SweepCell(
                    moneyness_pct=moneyness,
                    tenor_weeks=tenor,
                    roi_on_premium=res.roi_on_premium,
                    n_cycles=res.n_cycles,
                )
            )
    if not cells:
        raise HTTPException(status_code=404, detail=f"no scorable window for {asset}")
    return SweepResponse(
        asset=asset, as_of=resolved, notional=notional, lookback_years=years, cells=cells
    )


@router.get("/api/putlab/cadence")
def putlab_cadence(
    asset: str = Query(description="Underlying ticker, e.g. spy."),
) -> OptionsCadence:
    return cadence_for(asset)
