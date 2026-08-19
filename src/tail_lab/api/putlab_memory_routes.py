"""Hypothesis-memory routes for the Put Lab (``docs/adr/0015``).

- ``POST /api/putlab/memory/record`` — token-gated (the daily agent's write
  path; the agent has no lake credentials, so it records through the live app
  exactly as it pulls feedback). Computes the authoritative regime verdict
  server-side and records each per-regime outcome, so the agent can't fabricate
  a verdict — it only asks "record what this rule actually did."
- ``GET /api/putlab/memory`` — public read: the stored prior art for a rule
  (aggregate verdict + every per-regime outcome, with ``run_count``), or an
  empty ``outcomes`` list when the exact rule was never recorded.

The token check mirrors ``feedback_routes`` (unset -> 404, wrong -> 401,
constant-time compare); it reuses ``feedback_token`` since a single operator
holds one token for both write paths.
"""

from __future__ import annotations

import datetime as dt
import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from tail_lab.api.schemas import MemoryPriorArt, MemoryRecordResponse, RecordedRegime
from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.config import get_memory_store as _get_configured_memory_store
from tail_lab.config import get_settings
from tail_lab.contracts.hypothesis import RuleSpec
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import LakeStore
from tail_lab.memory.store import HypothesisMemory
from tail_lab.observability import log_event
from tail_lab.research.backtest.regime_verdict import compute_regime_verdict

router = APIRouter()
logger = logging.getLogger("tail_lab.api.putlab.memory")

#: Fixed notional for recorded verdicts — the verdict (regime_only/confirmed)
#: is scale-invariant, so a canonical budget keeps one rule_hash per
#: asset/strike/tenor/lookback regardless of position size.
RECORD_NOTIONAL = 1000.0


def get_lake_store() -> LakeStore:
    return _get_configured_lake_store()


def get_memory_store() -> HypothesisMemory:
    return _get_configured_memory_store()


def _require_token(authorization: str | None) -> None:
    configured = get_settings().feedback_token
    if not configured:
        raise HTTPException(status_code=404, detail="not found")
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[len("Bearer ") :].strip()
    if not presented or not secrets.compare_digest(presented, configured):
        raise HTTPException(status_code=401, detail="unauthorized")


def _resolve_as_of(as_of: dt.date | None) -> dt.date:
    return as_of or dt.datetime.now(dt.UTC).date()


@router.post("/api/putlab/memory/record")
def record_verdict(
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    moneyness_pct: float = Query(gt=0, lt=100),
    tenor_weeks: float = Query(gt=0, le=52),
    years: float = Query(gt=0, le=20),
    as_of: dt.date | None = Query(default=None),
    authorization: str | None = Header(default=None),
    store: LakeStore = Depends(get_lake_store),
    memory: HypothesisMemory = Depends(get_memory_store),
) -> MemoryRecordResponse:
    _require_token(authorization)
    resolved = _resolve_as_of(as_of)
    try:
        verdict = compute_regime_verdict(
            store,
            asset=asset,
            as_of=resolved,
            notional=RECORD_NOTIONAL,
            moneyness_pct=moneyness_pct,
            tenor_weeks=tenor_weeks,
            years=years,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    spec = RuleSpec(
        asset=asset,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=round(years),
    )
    # run_id ties this recording to the exact data version it saw (§f provenance).
    try:
        ohlcv_snap = store.bronze_snapshot_id(dataset_id(asset), resolved)
    except LookupError:
        ohlcv_snap = "unknown"
    run_id = f"{resolved.isoformat()}#{ohlcv_snap}"

    recorded: list[RecordedRegime] = []
    for sl in verdict.slices:
        outcome = memory.record(
            spec,
            sl.regime,
            roi_on_premium=sl.roi_on_premium,
            n_cycles=sl.n_cycles,
            hit_rate=sl.hit_rate,
            biggest_payoff_mult=sl.biggest_payoff_mult,
            run_id=run_id,
        )
        recorded.append(RecordedRegime(regime=sl.regime, run_count=outcome.run_count))

    log_event(
        logger,
        "putlab.memory.record",
        asset=asset,
        as_of=resolved,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
        rule_hash=spec.rule_hash(),
        ohlcv_snapshot=ohlcv_snap,
        code_sha=get_settings().code_sha,
        verdict=verdict.verdict,
        n_recorded=len(recorded),
    )
    return MemoryRecordResponse(
        rule_hash=spec.rule_hash(), verdict=verdict.verdict, recorded=recorded
    )


@router.get("/api/putlab/memory")
def prior_art(
    asset: str = Query(description="Underlying ticker, e.g. spy."),
    moneyness_pct: float = Query(gt=0, lt=100),
    tenor_weeks: float = Query(gt=0, le=52),
    years: float = Query(gt=0, le=20),
    memory: HypothesisMemory = Depends(get_memory_store),
) -> MemoryPriorArt:
    spec = RuleSpec(
        asset=asset,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=round(years),
    )
    rule_hash = spec.rule_hash()
    return MemoryPriorArt(
        rule_hash=rule_hash,
        verdict=memory.verdict_for_rule(rule_hash),
        outcomes=memory.outcomes_for_rule(rule_hash),
    )
