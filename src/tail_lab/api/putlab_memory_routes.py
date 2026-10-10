"""Hypothesis-memory routes for the Put Lab (``docs/adr/0015``).

- ``POST /api/putlab/memory/record`` — token-gated (the daily agent's write
  path; the agent has no lake credentials, so it records through the live app
  exactly as it pulls feedback). Computes the authoritative regime verdict
  server-side and records each per-regime outcome, so the agent can't fabricate
  a verdict — it only asks "record what this rule actually did."
- ``GET /api/putlab/memory`` — public read: the stored prior art for a rule
  priced with measured dividends (aggregate verdict + every per-regime outcome,
  with ``run_count``; empty ``outcomes`` when no measured run was recorded),
  plus a separate ``legacy`` section for the same rule priced without dividends.
  Memory is addressed by hash only, so this is the one read path that keeps the
  pre-dividend records reachable.

The token check mirrors ``feedback_routes`` (unset -> 404, wrong -> 401,
constant-time compare); it reuses ``feedback_token`` since a single operator
holds one token for both write paths.
"""

from __future__ import annotations

import datetime as dt
import logging
from functools import partial

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from tail_lab.api.auth import require_bearer_token as _require_token
from tail_lab.api.schemas import (
    MemoryPriorArt,
    MemoryPriorArtSection,
    MemoryRecordResponse,
    RecordedRegime,
)
from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.config import get_memory_store as _get_configured_memory_store
from tail_lab.config import get_settings
from tail_lab.contracts.hypothesis import rule_spec_for
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import LakeStore
from tail_lab.memory.store import HypothesisMemory
from tail_lab.observability import log_event
from tail_lab.research.backtest.regime_verdict import compute_regime_verdict
from tail_lab.research.dividends import dividend_snapshot_id

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

    # The verdict's own spec: its dividend basis is the run's, so a run that
    # priced any roll at an unknown q = 0 is stored under the "none" hash.
    spec = verdict.rule_spec
    # run_id ties this recording to the exact data version it saw (§f provenance).
    try:
        ohlcv_snap = store.bronze_snapshot_id(dataset_id(asset), resolved)
    except LookupError:
        ohlcv_snap = "unknown"
    # ...and the dividend basis: a re-record after a new Tiingo snapshot priced
    # with a different q, so it is a different run.
    run_id = f"{resolved.isoformat()}#{ohlcv_snap}#{dividend_snapshot_id(store, resolved) or 'no-dividends'}"

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
        dividends=spec.dividends,
        ohlcv_snapshot=ohlcv_snap,
        dividend_snapshot=dividend_snapshot_id(store, resolved),
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
    spec_for = partial(
        rule_spec_for,
        asset=asset,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        years=years,
    )
    measured = spec_for(q_source="measured").rule_hash()
    legacy = spec_for(q_source=None).rule_hash()
    legacy_outcomes = memory.outcomes_for_rule(legacy)
    return MemoryPriorArt(
        rule_hash=measured,
        verdict=memory.verdict_for_rule(measured),
        outcomes=memory.outcomes_for_rule(measured),
        legacy=MemoryPriorArtSection(
            rule_hash=legacy,
            verdict=memory.verdict_for_rule(legacy),
            outcomes=legacy_outcomes,
            note="priced without dividends (q = 0)",
        )
        if legacy_outcomes
        else None,
    )
