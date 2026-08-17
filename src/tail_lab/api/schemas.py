"""Typed API response models."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class VixStretchResponse(BaseModel):
    date: dt.date
    close: float
    rolling_mean_20d: float
    rolling_std_20d: float
    z_score: float
