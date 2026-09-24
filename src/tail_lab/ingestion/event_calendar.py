"""The ONE producer of the shared ``event_calendar`` bronze dataset
(``docs/DATA_CONTRACTS.md`` #5).

``event_calendar`` is one immutable snapshot per ``ingest_date``, keyed on the
date and not on who wrote it (``docs/adr/0013``). It used to have two writers,
``ingestion/fomc.py`` and ``ingestion/earnings.py``, and whichever ran first
claimed the day: FOMC then lost its rows silently, and earnings refused
loudly. Measured 2026-09-21 -- running both in one daily pass failed every
time, so the daily refresh had to skip the whole dataset rather than choose
which half of the calendar existed on each date.

So the two source modules now only fetch and parse, and this module commits
both halves as ONE snapshot. The next source (BLS CPI) joins this write.

**It refuses to write half a calendar.** If the FOMC page cannot be fetched or
yields no valid meetings, or more than half the earnings dates fail, nothing
is written: as-of reads take the LATEST partition, so a half-calendar
partition would shadow yesterday's complete one -- the silent-truncation
failure ``ingestion/sources.py`` describes. A few failed earnings dates are
still tolerated ("retry tomorrow"). What it does NOT catch is a source that
answers with a PARTIAL set -- e.g. a page redesign that leaves one year panel
parseable -- because nothing here compares against the previous partition
(queued in ``AGENT_TODO.md``).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from tail_lab.contracts.event_calendar import DATASET, validate_and_quarantine
from tail_lab.ingestion.earnings import (
    DEFAULT_LOOKAHEAD_DAYS,
    collect_earnings_window,
    earnings_window,
)
from tail_lab.ingestion.fomc import SOURCE_ID as FOMC_SOURCE_ID
from tail_lab.ingestion.fomc import fetch_fomc_calendar_raw, parse_fomc_calendar_html
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = ["IncompleteCalendarError", "IngestResult", "ingest_event_calendar"]

_LOGGER = logging.getLogger(__name__)

QUARANTINE_DATASET = f"{DATASET}__quarantine"


class IncompleteCalendarError(RuntimeError):
    """A whole source failed, so the snapshot would be half a calendar.
    Raised INSTEAD of writing; a re-run once the source answers can still
    claim the day."""


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    #: False when the day's partition already existed and nothing was written.
    committed: bool
    earnings_dates_fetched: int
    earnings_dates_failed: int
    rows_by_source: Mapping[str, int] = field(default_factory=dict)


def ingest_event_calendar(
    store: LakeStore,
    *,
    ingest_date: dt.date | None = None,
    fomc_html: str | None = None,
    earnings_dates: Sequence[dt.date] | None = None,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    earnings_fetch: Callable[[dt.date], Any] | None = None,
) -> IngestResult:
    """Fetch both halves (or use the injected ones), validate, commit ONE snapshot.

    ``fomc_html`` and ``earnings_fetch`` let tests stand in for the network;
    ``earnings_dates`` overrides the default forward window.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()

    fomc = parse_fomc_calendar_html(
        fomc_html if fomc_html is not None else fetch_fomc_calendar_raw()
    )
    window = (
        list(earnings_dates)
        if earnings_dates is not None
        else earnings_window(ingest_date, lookahead_days)
    )
    earnings, failed = collect_earnings_window(window, earnings_fetch)
    if failed * 2 > len(window):
        raise IncompleteCalendarError(
            f"{failed} of {len(window)} earnings-calendar dates failed; refusing to write "
            "a calendar missing most of its earnings over the last complete one"
        )

    parsed = pd.concat([fomc, earnings], ignore_index=True)
    # Every row gets the same conservative announced_at (the ingestion
    # timestamp): neither source says when an event became public, so this
    # never claims knowledge earlier than the scrape can prove (both source
    # modules' docstrings).
    parsed["announced_at"] = pd.Timestamp(dt.datetime.combine(ingest_date, dt.time.min))

    valid, quarantined = validate_and_quarantine(parsed)
    # Counted AFTER validation: the parser turns every row it cannot read into
    # a malformed row for quarantine, so a redesigned page yields a non-empty
    # parse with zero valid meetings.
    if not (valid["source_id"] == FOMC_SOURCE_ID).any():
        raise IncompleteCalendarError(
            "the FOMC page yielded no valid meetings -- its layout has likely changed; "
            "refusing to write an earnings-only calendar over the last complete one"
        )
    committed = not store.bronze_partition_exists(DATASET, ingest_date)
    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    quarantine_path: str | None = None
    if not quarantined.empty:
        # Diagnostic only, and the snapshot is already committed above -- a
        # failed quarantine write must not fail the run (same rule as the
        # chain sweep, whose quarantine table once broke on a stale column).
        try:
            quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)
        except Exception:
            _LOGGER.exception(
                "event=ingest.event_calendar.quarantine_write_failed ingest_date=%s rows=%d",
                ingest_date.isoformat(),
                len(quarantined),
            )

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        committed=committed,
        earnings_dates_fetched=len(window) - failed,
        earnings_dates_failed=failed,
        rows_by_source={str(k): int(v) for k, v in valid["source_id"].value_counts().items()},
    )
    _log_run(result, ingest_date, valid)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date, valid: pd.DataFrame) -> None:
    """Emit the run's full surface (``docs/STANDARDS.md`` §f)."""
    log_event(
        _LOGGER,
        "ingest.event_calendar",
        dataset=DATASET,
        ingest_date=ingest_date.isoformat(),
        committed=result.committed,
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
        earnings_dates_fetched=result.earnings_dates_fetched,
        earnings_dates_failed=result.earnings_dates_failed,
        rows_by_source=",".join(f"{k}:{v}" for k, v in sorted(result.rows_by_source.items())),
        earnings_symbols=int(valid["symbol"].nunique()),
        first_event_date=_edge(valid, "min"),
        last_event_date=_edge(valid, "max"),
    )


def _edge(valid: pd.DataFrame, which: str) -> str | None:
    """Window boundary of the committed rows, for the run log. ``None`` on an
    empty frame so the log line drops the field rather than printing "NaT"."""
    if valid.empty:
        return None
    stamp = valid["event_date"].min() if which == "min" else valid["event_date"].max()
    return str(pd.Timestamp(stamp).date())
