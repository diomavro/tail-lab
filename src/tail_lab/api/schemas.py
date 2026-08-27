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


class OptionChainSnapshotRow(BaseModel):
    """One sliced put quote in transit from the scheduled sweep to bronze.

    Mirrors ``contracts/option_chain.OptionChainSnapshotSchema`` field for
    field. The three greek columns are optional because Cboe zero-fills what
    it cannot compute and the adapter maps that to a typed absence — an
    omitted ``iv`` here means "the exchange did not publish one", never zero.
    """

    underlying: str
    quote_date: dt.date
    expiration: dt.date
    strike: float
    bid: float
    ask: float
    volume: int
    open_interest: int
    spot: float
    iv: float | None = None
    delta: float | None = None
    theo: float | None = None


class OptionChainSnapshotRequest(BaseModel):
    """One day's sweep. ``ingest_date`` is the bronze partition (defaults to
    today on the server); ``quote_date`` on each row is the session the
    quotes belong to, and the two differ whenever a sweep runs after
    midnight UTC."""

    rows: list[OptionChainSnapshotRow]
    ingest_date: dt.date | None = None


class OptionChainSnapshotResponse(BaseModel):
    """What landed, echoed back so the workflow can assert on it rather than
    trusting a 200."""

    dataset: str
    ingest_date: dt.date
    rows: int
    quarantined: int = 0
    symbols: int
    quote_date: dt.date
    bronze_path: str


class OptionChainSnapshotStatus(BaseModel):
    """Freshness of the forward collection. ``last_quote_date`` is ``None``
    only before the very first sweep; after that, ``stale_days`` climbing
    past a long weekend means sessions are being lost permanently."""

    dataset: str
    last_quote_date: dt.date | None
    rows: int
    symbols: int
    stale_days: int | None
