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

**Bronze collision with the FOMC producer, and how this one avoids it.**
`event_calendar` bronze is one immutable snapshot per ``ingest_date`` shared
across every producer (`docs/DATA_CONTRACTS.md` #5) -- a second producer
that calls ``write_bronze`` on a day the first already has silently no-ops
and *loses* its own rows rather than merging them
(``ingestion/fomc.py``'s docstring flagged this risk before a second
producer existed). Rather than risk that silently,
:func:`ingest_earnings_calendar` refuses to write (raises ``RuntimeError``)
if the dataset already carries a snapshot dated exactly ``ingest_date`` --
loud failure over silent data loss, the same stance `docs/adr/0020`'s daily
chain sweep takes. The next producer into this dataset (BLS CPI) should
carry the same guard until the sources are combined into one write.

Functions split so tests never touch the network:

- :func:`parse_earnings_calendar_json` -- pure, parses one date's JSON page
  into event rows. Pinned against a real fetched fixture
  (``tests/fixtures/nasdaq_earnings_sample.json``, live-fetched 2026-09-06)
  and a real "no earnings that date" weekend response
  (``tests/fixtures/nasdaq_earnings_empty_sample.json``).
- :func:`fetch_earnings_calendar_raw` -- the HTTP call, exercised only by
  ``make ingest-earnings``, never by CI/pytest.
- :func:`ingest_earnings_calendar` -- orchestrates the window fetch ->
  parse -> attach ``announced_at`` -> validate/quarantine ->
  commit-to-bronze. A single date's fetch failure is caught and skipped
  (logged, not fatal) -- "retry tomorrow", per the sourcing note -- so one
  Nasdaq blip does not cost the whole window.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.event_calendar import DATASET, EventSchema
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "DEFAULT_LOOKAHEAD_DAYS",
    "QUARANTINE_DATASET",
    "IngestResult",
    "fetch_earnings_calendar_raw",
    "ingest_earnings_calendar",
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


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    dates_fetched: int
    dates_failed: int


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
    is attached later, by :func:`ingest_earnings_calendar`, since it depends
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
        return _empty_frame()

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


def _empty_frame() -> pd.DataFrame:
    """A correctly-typed zero-row frame, so a no-earnings date still validates."""
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
    so the caller can persist them for inspection.
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


def _default_window(ingest_date: dt.date, lookahead_days: int) -> list[dt.date]:
    return [ingest_date + dt.timedelta(days=i) for i in range(lookahead_days)]


def _refuse_if_already_written_today(store: LakeStore, ingest_date: dt.date) -> None:
    """See the module docstring's "Bronze collision" section. Fails loudly
    rather than letting ``write_bronze`` silently no-op and drop this run's
    rows on a day another producer already committed."""
    try:
        snapshot_id = store.bronze_snapshot_id(DATASET, ingest_date)
    except LookupError:
        return
    resolved_date = snapshot_id.split("@", 1)[1].split("#", 1)[0]
    if resolved_date == ingest_date.isoformat():
        raise RuntimeError(
            f"{DATASET!r} already has a bronze partition for {ingest_date.isoformat()} "
            "(from another producer, e.g. ingestion/fomc.py) -- combine sources into "
            "one write before ingesting earnings for this date; see the module docstring."
        )


def ingest_earnings_calendar(
    store: LakeStore,
    *,
    ingest_date: dt.date | None = None,
    dates: Sequence[dt.date] | None = None,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    fetch: Callable[[dt.date], Any] | None = None,
) -> IngestResult:
    """Fetch (or use the injected ``fetch``) a rolling window of earnings-
    calendar dates, validate, and commit one bronze snapshot to the shared
    ``event_calendar`` dataset.

    ``dates`` overrides the default forward window (mainly for tests);
    ``fetch`` lets callers (tests) inject a stand-in for
    :func:`fetch_earnings_calendar_raw` instead of hitting the network, and
    lets it raise per-date to exercise the "one bad date does not cost the
    whole window" tolerance below.
    """
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    _refuse_if_already_written_today(store, ingest_date)
    fetch = fetch or fetch_earnings_calendar_raw
    window = list(dates) if dates is not None else _default_window(ingest_date, lookahead_days)

    frames: list[pd.DataFrame] = []
    failed = 0
    for event_date in window:
        try:
            raw = fetch(event_date)
        except Exception:
            failed += 1
            _LOGGER.warning("earnings calendar fetch failed for %s", event_date.isoformat())
            continue
        frames.append(parse_earnings_calendar_json(raw, event_date))

    parsed = pd.concat(frames, ignore_index=True) if frames else _empty_frame()
    # See the module docstring: every row gets the same conservative
    # announced_at (the ingestion timestamp) since this adapter has no way
    # to recover the true historical announcement date from a single scrape.
    announced_at = pd.Timestamp(dt.datetime.combine(ingest_date, dt.time.min))
    parsed["announced_at"] = announced_at

    valid, quarantined = validate_and_quarantine(parsed)

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        dates_fetched=len(window) - failed,
        dates_failed=failed,
    )
    _log_run(result, ingest_date, valid)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date, valid: pd.DataFrame) -> None:
    """Emit the run's full surface (`docs/STANDARDS.md` §f) -- an automated
    action without a runtime record is below the bar."""
    log_event(
        _LOGGER,
        "ingest.earnings",
        dataset=DATASET,
        source=SOURCE_ID,
        ingest_date=ingest_date.isoformat(),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
        dates_fetched=result.dates_fetched,
        dates_failed=result.dates_failed,
        symbols=int(valid["symbol"].nunique()) if not valid.empty else 0,
    )
