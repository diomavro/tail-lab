"""Nasdaq earnings-calendar ingestion adapter -- bronze layer
(`docs/DATA_CONTRACTS.md` #5, EARNINGS half).

Source: Nasdaq's public (unofficial) calendar endpoint,
``https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`` -- free,
keyless, one JSON page per calendar date (browser ``User-Agent`` + JSON
``Accept`` header required, same requirement as ``ingestion/ohlcv.py``'s
Nasdaq source). Verified live back to 2010 and for upcoming dates
(`docs/DATA_SOURCING.md` "Free win #2"), but this adapter deliberately
ingests only a forward-looking window each run -- see below.

**Scoped narrower than the source.** The endpoint answers one calendar date
per call, so a deep historical backfill (2010 -> today) would be thousands
of sequential requests to an endpoint the sourcing note already flags as
"unofficial: pace requests and cache". This adapter instead sweeps a rolling
forward window (``DEFAULT_LOOKAHEAD_DAYS`` calendar days starting at
``ingest_date``), which is what the cockpit's proximity flags
(`docs/END_STATE.md` §1.4) actually need -- what is coming up, not a decade
of history. A historical backfill for research questions 3/5 is a follow-up,
tracked in ``AGENT_TODO.md``, not a defect in what ships here.

**The point-in-time limitation is the same one `ingestion/fomc.py`
documents, for the same reason.** An earnings date Nasdaq lists today is
being learned about *today*, by this scrape -- this adapter has no way to
recover when a company's report date actually became public, so
``announced_at`` is set to the ingestion timestamp for every row. That is
conservative (never a look-ahead leak: no as-of read before this adapter's
first run will ever see one of these rows) but under-informative for a
simulation date before this adapter's first run.

**This module does not write.** `event_calendar` bronze is one immutable
snapshot per ``ingest_date`` shared across every producer
(`docs/DATA_CONTRACTS.md` #5), so a second per-source write on a day the
first already has silently no-ops and loses its rows. It used to guard that
by refusing; now the write lives in ``ingestion/event_calendar.py``, which
commits this and the FOMC half as ONE snapshot.

Functions split so tests never touch the network:

- :func:`parse_earnings_calendar_json` -- pure, parses one date's JSON page
  into event rows. Pinned against a real fetched fixture
  (``tests/fixtures/nasdaq_earnings_sample.json``, live-fetched 2026-09-06)
  and a real "no earnings that date" weekend response
  (``tests/fixtures/nasdaq_earnings_empty_sample.json``).
- :func:`fetch_earnings_calendar_raw` -- the HTTP call, exercised only by
  ``make ingest-event-calendar``, never by CI/pytest.
- :func:`collect_earnings_window` -- fetch + parse a window of dates. A
  single date's fetch failure is caught and skipped (logged, not fatal) --
  "retry tomorrow", per the sourcing note -- so one Nasdaq blip does not
  cost the whole window.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Sequence
from typing import Any

import pandas as pd
import requests

from tail_lab.contracts.event_calendar import (
    DATASET,
    empty_event_frame,
    validate_and_quarantine,
)

__all__ = [
    "DATASET",
    "DEFAULT_LOOKAHEAD_DAYS",
    "QUARANTINE_DATASET",
    "collect_earnings_window",
    "earnings_window",
    "fetch_earnings_calendar_raw",
    "parse_earnings_calendar_json",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

EARNINGS_CALENDAR_URL = "https://api.nasdaq.com/api/calendar/earnings"
QUARANTINE_DATASET = f"{DATASET}__quarantine"
SOURCE_ID = "nasdaq_earnings"
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

#: How many calendar days forward (from ``ingest_date``, inclusive) each run
#: sweeps -- long enough to span one earnings season, short enough that a
#: run stays a few dozen requests to an unofficial endpoint, not thousands.
DEFAULT_LOOKAHEAD_DAYS = 30

#: Nasdaq's ``time`` field values that are worth surfacing in the
#: description; ``time-not-supplied`` (the common case) adds nothing.
_TIME_SUFFIXES: dict[str, str] = {
    "time-pre-market": " (before market open)",
    "time-after-hours": " (after market close)",
}


def fetch_earnings_calendar_raw(event_date: dt.date, *, timeout: float = 15.0) -> Any:
    """Fetch one date's earnings-calendar JSON. Network call -- not used by tests."""
    resp = requests.get(
        EARNINGS_CALENDAR_URL,
        params={"date": event_date.isoformat()},
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload: Any = resp.json()
    return payload


def parse_earnings_calendar_json(raw: Any, event_date: dt.date) -> pd.DataFrame:
    """Parse one date's Nasdaq earnings JSON into a typed event frame.

    Pure function -- no network, no filesystem, no wall clock (``announced_at``
    is attached later, by ``ingestion/event_calendar``, since it depends
    on when the scrape happened, not on anything in the page). A row missing
    a ``symbol`` -- Nasdaq's only identifier for the report -- keeps a
    placeholder ``event_id`` and a ``None`` symbol so
    :func:`validate_and_quarantine` catches it (the schema requires a symbol
    on every ``EARNINGS`` row) rather than guessing one or dropping the row
    silently. A date with no scheduled earnings (a weekend, a market
    holiday) comes back from Nasdaq with ``rows: null``, which parses to a
    correctly-typed empty frame, not an error.
    """
    rows = ((raw or {}).get("data") or {}).get("rows") or []
    if not rows:
        return empty_event_frame()

    records = [_parse_row(row, event_date, idx) for idx, row in enumerate(rows)]
    return (
        pd.DataFrame(records)
        .drop_duplicates(subset="event_id", keep="first")
        .reset_index(drop=True)
    )


def _parse_row(row: dict[str, Any], event_date: dt.date, idx: int) -> dict[str, object]:
    symbol = str(row.get("symbol") or "").strip() or None
    if symbol is None:
        return {
            "event_id": f"earnings_unparsed_{event_date.isoformat()}_{idx}",
            "event_type": "EARNINGS",
            "event_date": pd.Timestamp(event_date),
            "symbol": None,
            "description": "unparsed earnings row: missing symbol",
            "source_id": SOURCE_ID,
        }
    name = str(row.get("name") or symbol).strip()
    suffix = _TIME_SUFFIXES.get(str(row.get("time")), "")
    return {
        "event_id": f"earnings_{symbol}_{event_date.isoformat()}",
        "event_type": "EARNINGS",
        "event_date": pd.Timestamp(event_date),
        "symbol": symbol,
        "description": f"{name} earnings{suffix}",
        "source_id": SOURCE_ID,
    }


def _raise_on_api_error(raw: Any) -> None:
    """Nasdaq reports request errors INSIDE an HTTP 200: measured 2026-09-24,
    a bad date returns ``{"data": null, "status": {"rCode": 400, ...}}``, which
    parses exactly like a day with no earnings. A genuinely empty day carries
    ``rCode`` 200, so anything else is a failed date, not an empty one."""
    if not isinstance(raw, dict):
        raise ValueError(f"earnings payload is {type(raw).__name__}, not a JSON object")
    code = (raw.get("status") or {}).get("rCode", 200)
    if code != 200:
        raise ValueError(f"Nasdaq earnings API answered rCode={code}")


def earnings_window(ingest_date: dt.date, lookahead_days: int) -> list[dt.date]:
    """``lookahead_days`` calendar days forward from ``ingest_date``, inclusive."""
    return [ingest_date + dt.timedelta(days=i) for i in range(lookahead_days)]


def collect_earnings_window(
    window: Sequence[dt.date], fetch: Callable[[dt.date], Any] | None = None
) -> tuple[pd.DataFrame, int]:
    """Fetch and parse every date in ``window``; return ``(events, failed_dates)``.

    A single date's fetch failure is caught and counted (logged, not fatal)
    -- "retry tomorrow", per the sourcing note -- so one Nasdaq blip does not
    cost the whole window. ``fetch`` lets tests stand in for
    :func:`fetch_earnings_calendar_raw` without a socket.
    """
    fetch = fetch or fetch_earnings_calendar_raw
    frames: list[pd.DataFrame] = []
    failed = 0
    for event_date in window:
        # Parse inside the try too: now that FOMC shares this write, one
        # malformed page must cost its own date, not the whole calendar.
        try:
            raw = fetch(event_date)
            _raise_on_api_error(raw)
            frames.append(parse_earnings_calendar_json(raw, event_date))
        except Exception:
            failed += 1
            _LOGGER.warning("earnings calendar fetch failed for %s", event_date.isoformat())
    events = pd.concat(frames, ignore_index=True) if frames else empty_event_frame()
    return events, failed
