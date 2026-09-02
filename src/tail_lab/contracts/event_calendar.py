"""Pandera schema for the event calendar dataset (`docs/DATA_CONTRACTS.md` #5).

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

Drives the cockpit's proximity flags (`docs/END_STATE.md` §1.4) and is a
required input to research questions 3/5 (IV behavior around FOMC/CPI/crises).
Two independent producers write into the one ``event_calendar`` dataset —
scheduled adapters (FOMC today, CPI/earnings later) and a hand-maintained
manual table for unscheduled events — sharing this schema so a consumer reads
one dataset regardless of source.

``announced_at`` is the point-in-time field, deliberately distinct from
``event_date``: a scheduled FOMC meeting six months out is legitimately
"known" today, so ``announced_at`` can sit well before ``event_date``, while a
genuine surprise is known only at or after its own ``event_date``. A
consumer filtering "what was known as of simulation date D" must filter on
``announced_at``, never on ``event_date`` — the sharpest look-ahead trap this
project has documented (`docs/DATA_CONTRACTS.md` #5).
"""

from __future__ import annotations

from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series

#: The event types this schema accepts. ``EARNINGS`` is meant to require
#: ``symbol`` while every other type leaves it null -- a cross-column
#: conditional pandera cannot express as a single ``Field`` constraint.
#:
#: **Nothing enforces that today.** The rule was written as "enforced by the
#: ingestion adapter", but no EARNINGS producer has shipped, so there is no
#: adapter to enforce it and the sentence described a guarantee that did not
#: exist (design review, PR #63). A documented invariant nobody checks is worse
#: than an absent one, because the next reader budgets for a safety that is not
#: there. **The first EARNINGS adapter owns making this true**, and should
#: delete this note when it does.
EVENT_TYPES: tuple[str, ...] = ("FOMC", "CPI", "EARNINGS", "MANUAL")

#: Which producer wrote a given row. One value per adapter plus the manual
#: table, so a consumer can trace any row back to its source.
SOURCE_IDS: tuple[str, ...] = ("fed_calendar", "bls_calendar", "manual")

#: The single bronze dataset every event producer commits into.
DATASET = "event_calendar"


class EventRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver event-calendar rows."""

    event_id: Series[str] = pa.Field(nullable=False)
    event_type: Series[str] = pa.Field(nullable=False, isin=EVENT_TYPES)
    event_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    announced_at: Series[pa.Timestamp] = pa.Field(nullable=False)
    symbol: Series[str] = pa.Field(nullable=True)
    description: Series[str] = pa.Field(nullable=False, str_length={"min_value": 1})
    source_id: Series[str] = pa.Field(nullable=False, isin=SOURCE_IDS)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["event_id"]


EventSchema = EventRowSchema.to_schema()
