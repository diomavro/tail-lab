"""Typed API request/response models."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from tail_lab.feedback.store import FeedbackKind, FeedbackRecord
from tail_lab.memory.store import RegimeOutcome
from tail_lab.research.backtest.portfolio import PortfolioLeg
from tail_lab.research.backtest.sweep import SweepPoint


class HealthResponse(BaseModel):
    status: str


class VixStretchResponse(BaseModel):
    date: dt.date
    close: float
    rolling_mean_20d: float
    rolling_std_20d: float
    z_score: float


class SweepResponse(BaseModel):
    """The strike x tenor sweep plus the S&P 500 hurdle it's judged against.

    ``benchmark_*`` describe a buy-and-hold of ``benchmark_symbol`` over the
    same lookback window (return on capital, not on premium — a rough hurdle,
    not a like-for-like base). ``None`` when the benchmark's price history is
    missing as of ``as_of``."""

    asset: str
    as_of: dt.date
    notional: float
    lookback_years: float
    cells: list[SweepPoint]
    benchmark_symbol: str
    benchmark_annualized: float | None
    benchmark_total: float | None
    #: The strike depth past which this platform's premium stops being a price
    #: (research/backtest/sweep.py). The grid deliberately runs deeper, so the
    #: client needs the threshold to mark those cells -- served rather than
    #: hard-coded client-side so the two copies cannot drift. See docs/adr/0018.
    model_priced_max_moneyness_pct: float


class PortfolioRequest(BaseModel):
    """POST body for /api/putlab/portfolio: a weighted mix of OOM-put legs."""

    legs: list[PortfolioLeg] = Field(min_length=1)
    notional: float = Field(default=10000.0, gt=0, le=10_000_000)
    years: float = Field(default=4.0, gt=0, le=20)
    as_of: dt.date | None = None


class RecordedRegime(BaseModel):
    regime: str
    run_count: int


class MemoryRecordResponse(BaseModel):
    """What POST /api/putlab/memory/record recorded for one rule."""

    rule_hash: str
    verdict: str
    recorded: list[RecordedRegime]


class MemoryPriorArt(BaseModel):
    """What GET /api/putlab/memory knows about a rule — the aggregate verdict
    and every stored per-regime outcome (empty ``outcomes`` => untested)."""

    rule_hash: str
    verdict: str
    outcomes: list[RegimeOutcome]


class FeedbackCreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    kind: FeedbackKind


class FeedbackListResponse(BaseModel):
    """The daily agent's ``GET /api/feedback`` view: standing (``big_picture``)
    directives it honors every run, and transient ``issue``s it can pick up
    and resolve. Both lists are open records only — see
    ``tail_lab.feedback.store.FeedbackStore.list_open``."""

    standing: list[FeedbackRecord]
    issues: list[FeedbackRecord]
