"""Typed API request/response models."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from tail_lab.feedback.store import FeedbackKind, FeedbackRecord


class HealthResponse(BaseModel):
    status: str


class VixStretchResponse(BaseModel):
    date: dt.date
    close: float
    rolling_mean_20d: float
    rolling_std_20d: float
    z_score: float


class SweepCell(BaseModel):
    """One (moneyness, tenor) combination's total return on premium — a cell
    of the Put Lab strike x tenor heatmap."""

    moneyness_pct: float
    tenor_weeks: float
    roi_on_premium: float
    n_cycles: int


class SweepResponse(BaseModel):
    asset: str
    as_of: dt.date
    notional: float
    lookback_years: float
    cells: list[SweepCell]


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
