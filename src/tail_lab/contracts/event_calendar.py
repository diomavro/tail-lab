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

from typing import ClassVar, cast

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors
from pandera.typing import Series

#: The event types this schema accepts. ``EARNINGS`` requires ``symbol``
#: while every other type leaves it null -- enforced below by
#: ``earnings_rows_carry_a_symbol`` (``ingestion/earnings.py``, the first
#: EARNINGS producer, made this real; it used to be a documented-but-unchecked
#: aspiration, design review PR #63).
EVENT_TYPES: tuple[str, ...] = ("FOMC", "CPI", "EARNINGS", "MANUAL")

#: Which producer wrote a given row. One value per adapter plus the manual
#: table, so a consumer can trace any row back to its source.
SOURCE_IDS: tuple[str, ...] = ("fed_calendar", "bls_calendar", "manual", "nasdaq_earnings")

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

    @pa.dataframe_check
    @classmethod
    def earnings_rows_carry_a_symbol(cls, df: pd.DataFrame) -> Series[bool]:
        """Every ``EARNINGS`` row must name the company it's about -- a
        symbol-less earnings row is not a legitimate event, it's a parse
        failure that belongs in quarantine, not bronze."""
        mask = (df["event_type"] != "EARNINGS") | df["symbol"].notna()
        return cast(Series[bool], mask)


EventSchema = EventRowSchema.to_schema()


def empty_event_frame() -> pd.DataFrame:
    """A correctly-typed zero-row frame, so an empty source still validates.

    Shared by every ``event_calendar`` producer (``ingestion/fomc.py``,
    ``ingestion/earnings.py``, ...) -- they all validate against the same
    ``EventSchema``, so a per-producer copy would just be this frame typed
    out again.
    """
    return pd.DataFrame(
        {
            "event_id": pd.Series([], dtype="object"),
            "event_type": pd.Series([], dtype="object"),
            "event_date": pd.Series([], dtype="datetime64[ns]"),
            "symbol": pd.Series([], dtype="object"),
            "description": pd.Series([], dtype="object"),
            "source_id": pd.Series([], dtype="object"),
        }
    )


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the event-calendar contract.

    Bad rows are never silently dropped: they come back in the second frame
    so the caller can persist them for inspection. Shared by every
    ``event_calendar`` producer -- they all validate against the same
    ``EventSchema``.
    """
    try:
        valid = EventSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = EventSchema.validate(valid, lazy=True)
        return valid, quarantined
