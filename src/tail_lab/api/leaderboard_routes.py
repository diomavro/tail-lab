"""HTTP route for the sensitivity leaderboard (``docs/END_STATE.md`` §1.1).
Public GET, like the other cockpit reads — no auth to gate, model-free
ranking data.

``GET /api/leaderboard`` — the screening universe ranked by downside beta
against SPY, most-sensitive first (``research/leaderboard.py``).
"""

from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query

from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.config import get_settings
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event
from tail_lab.research.leaderboard import (
    DEFAULT_BENCHMARK,
    SensitivityLeaderboardResult,
    compute_sensitivity_leaderboard,
)

router = APIRouter()
logger = logging.getLogger("tail_lab.api.leaderboard")


@lru_cache(maxsize=1)
def get_lake_store() -> LakeStore:
    # Overridden in tests via dependency_overrides (which bypasses this call).
    return _get_configured_lake_store()


def _resolve_as_of(as_of: dt.date | None) -> dt.date:
    return as_of or dt.datetime.now(dt.UTC).date()


@router.get("/api/leaderboard")
def leaderboard(
    as_of: dt.date | None = Query(default=None, description="Simulation date; defaults to today."),
    store: LakeStore = Depends(get_lake_store),
) -> SensitivityLeaderboardResult:
    resolved = _resolve_as_of(as_of)
    try:
        result = compute_sensitivity_leaderboard(store, as_of=resolved)
    except LookupError as exc:
        log_event(logger, "leaderboard.miss", as_of=resolved, reason=str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        benchmark_snapshot = store.bronze_snapshot_id(dataset_id(DEFAULT_BENCHMARK), resolved)
    except LookupError:
        benchmark_snapshot = None
    log_event(
        logger,
        "leaderboard.build",
        as_of=resolved,
        metric=result.metric,
        benchmark=result.benchmark,
        benchmark_snapshot=benchmark_snapshot,
        code_sha=get_settings().code_sha,
        n_rows=len(result.rows),
    )
    return result
