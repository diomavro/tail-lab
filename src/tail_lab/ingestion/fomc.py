"""FOMC meeting calendar ingestion adapter -- bronze layer
(`docs/DATA_CONTRACTS.md` #5, scheduled half).

Source: the Federal Reserve's public meeting-calendar page,
``https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm`` -- free,
keyless, plain HTML (no ICS feed exists; confirmed 404 on the ical URL,
`docs/DATA_SOURCING.md` §2). The page renders one ``<div class="panel">`` per
year, each holding one ``<div class="row fomc-meeting">`` per two-day meeting,
and currently spans 2021-2027; ``fomc_historical.htm`` year pages reach back
to 1936 but are a separate, not-yet-built follow-up (`AGENT_TODO.md`).

**The point-in-time limitation this ships with, stated plainly.** A scheduled
FOMC meeting is legitimately "known" well before it happens -- the Fed
typically publishes a year's calendar in the summer of the year before -- but
this adapter has only ever scraped the page once, so it cannot recover the
*actual* historical announcement date for a meeting already on the page. It
therefore sets ``announced_at`` to the ingestion timestamp for every row,
past or future. This is conservative and safe (it never claims knowledge
earlier than the adapter can prove, so no as-of read can leak a future
scrape into a past simulation date) but it under-serves research question
3/5 (`docs/END_STATE.md` §4) for meetings that predate the first ingest:
a backtest simulating a date before this adapter's first run will see zero
FOMC events, not the ones that were genuinely public knowledge by then.
Backfilling real historical announcement dates (from the Fed's press-release
archive) is a follow-up, not a defect in what ships here.

Two functions, deliberately split so tests never touch the network:

- :func:`parse_fomc_calendar_html` -- pure, parses the calendar page's HTML
  into a ``(event_id, event_type, event_date, symbol, description,
  source_id)`` frame. Unit-tested against a committed fixture built from a
  real fetch of the page (``tests/fixtures/fomc_calendar_sample.html``).
- :func:`fetch_fomc_calendar_raw` -- makes the HTTP call. Exercised only by
  ``make ingest-fomc`` (human/manually run), never by CI/pytest.
- :func:`ingest_fomc_calendar` -- orchestrates fetch -> parse -> attach
  ``announced_at`` -> validate/quarantine -> commit-to-bronze, landing rows
  in the shared ``event_calendar`` dataset (`contracts/event_calendar.py`)
  that a future CPI/earnings adapter and the manual table also write into.

**Whoever builds the next producer into this dataset should read this
first.** Bronze immutability is keyed on ``(dataset, ingest_date)``, not on
which producer wrote it (`docs/adr/0013`) -- so a CPI or manual writer that
calls ``write_bronze("event_calendar", today, ...)`` independently on a day
this adapter already committed will silently no-op and lose its own rows,
not merge with them. Combine sources into one write before they land here
(the ``ingestion/cboe_strategy.py``/``ingestion/rates.py`` pattern), or this
shared-dataset design needs an ADR before a second producer ships.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass

import pandas as pd
import requests

from tail_lab.contracts.event_calendar import (
    DATASET,
    empty_event_frame,
    validate_and_quarantine,
)
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "QUARANTINE_DATASET",
    "IngestResult",
    "fetch_fomc_calendar_raw",
    "ingest_fomc_calendar",
    "parse_fomc_calendar_html",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
QUARANTINE_DATASET = f"{DATASET}__quarantine"
SOURCE_ID = "fed_calendar"

#: One panel per year: ``<h4><a id="...">2026 FOMC Meetings</a></h4>``.
_YEAR_HEADER = re.compile(r'<a id="\d+">\s*(\d{4})\s+FOMC Meetings\s*</a>')

#: One row per meeting within a year's panel. Non-greedy so it matches the
#: nearest ``fomc-meeting__date`` after a given ``fomc-meeting__month``,
#: never one belonging to the next meeting row.
_MEETING_ROW = re.compile(
    r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?"
    r"fomc-meeting__date[^>]*>\s*([^<]+?)\s*<",
    re.S,
)

#: Accepts both full ("January") and abbreviated ("Jan") month names -- the
#: page uses full names for a single-month meeting and abbreviations for a
#: month-spanning one ("Jan/Feb", "Apr/May", "Oct/Nov").
_MONTH_NUMBERS: dict[str, int] = {
    **{dt.date(2000, m, 1).strftime("%B"): m for m in range(1, 13)},
    **{dt.date(2000, m, 1).strftime("%b"): m for m in range(1, 13)},
}

#: A meeting's date cell: "27-28", "17-18*" (the ``*`` flags a Summary of
#: Economic Projections meeting), "30-1" (spans two months), or a bare "13"
#: for a one-day meeting.
_DATE_CELL = re.compile(r"^(\d{1,2})(?:-(\d{1,2}))?(\*)?$")


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None


def fetch_fomc_calendar_raw(*, timeout: float = 30.0) -> str:
    """Fetch the calendar page's HTML. Network call -- not used by tests."""
    resp = requests.get(
        FOMC_CALENDAR_URL,
        headers={"User-Agent": "Mozilla/5.0 (tail-lab ingestion bot)"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def parse_fomc_calendar_html(html: str) -> pd.DataFrame:
    """Parse the FOMC calendar page into a typed event frame.

    Pure function -- no network, no filesystem, no wall clock (``announced_at``
    is attached later by :func:`ingest_fomc_calendar`, since it depends on
    when the scrape actually happened, not on anything in the page itself).

    A meeting's ``event_date`` is its *last* day -- the day the statement and
    decision are released, matching the date embedded in the Fed's own
    ``monetary<YYYYMMDD>a.htm`` press-release URLs. A row whose month or day
    text this parser cannot recognize is dropped rather than guessed at: it
    survives with the fields it could parse set to ``pd.NaT``/``None`` so
    :func:`validate_and_quarantine` catches it, rather than a bad scrape
    silently landing a wrong date in bronze.
    """
    year_headers = list(_YEAR_HEADER.finditer(html))
    rows: list[dict[str, object]] = []
    for idx, header in enumerate(year_headers):
        year = int(header.group(1))
        segment_end = year_headers[idx + 1].start() if idx + 1 < len(year_headers) else len(html)
        segment = html[header.end() : segment_end]
        for month_text, date_text in _MEETING_ROW.findall(segment):
            rows.append(_parse_meeting(year, month_text.strip(), date_text.strip()))

    if not rows:
        return empty_event_frame()

    df = pd.DataFrame(rows)
    return (
        df.sort_values("event_date")
        .drop_duplicates(subset="event_id", keep="first")
        .reset_index(drop=True)
    )


def _parse_meeting(year: int, month_text: str, date_text: str) -> dict[str, object]:
    months = month_text.split("/")
    start_month = _MONTH_NUMBERS.get(months[0])
    end_month = _MONTH_NUMBERS.get(months[-1])

    match = _DATE_CELL.match(date_text)
    if match is None or start_month is None or end_month is None:
        return _malformed_row(month_text, date_text)

    end_day_text, is_sep = match.group(2) or match.group(1), match.group(3)
    end_year = year + 1 if end_month < start_month else year
    try:
        event_date = dt.date(end_year, end_month, int(end_day_text))
    except ValueError:
        return _malformed_row(month_text, date_text)

    description = "FOMC meeting"
    if is_sep:
        description += " (Summary of Economic Projections)"
    return {
        "event_id": f"fomc_{event_date.isoformat()}",
        "event_type": "FOMC",
        "event_date": pd.Timestamp(event_date),
        "symbol": None,
        "description": description,
        "source_id": SOURCE_ID,
    }


def _malformed_row(month_text: str, date_text: str) -> dict[str, object]:
    """A row the parser couldn't make sense of. Kept (not dropped) so it
    reaches quarantine instead of vanishing silently -- the ``event_date``
    schema field is non-nullable, so ``pd.NaT`` here guarantees this row
    fails validation rather than passing with a wrong date."""
    return {
        "event_id": f"fomc_unparsed_{month_text}_{date_text}",
        "event_type": "FOMC",
        "event_date": pd.NaT,
        "symbol": None,
        "description": f"unparsed FOMC row: {month_text} {date_text}",
        "source_id": SOURCE_ID,
    }


def ingest_fomc_calendar(
    store: LakeStore,
    *,
    ingest_date: dt.date | None = None,
    raw: str | None = None,
) -> IngestResult:
    """Fetch (or use supplied) the calendar page, validate, and commit one
    bronze snapshot to the shared ``event_calendar`` dataset.

    ``raw`` lets callers (tests) inject the page HTML instead of hitting the
    network; :func:`fetch_fomc_calendar_raw` is called only when it is absent.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    html = raw if raw is not None else fetch_fomc_calendar_raw()

    parsed = parse_fomc_calendar_html(html)
    # See the module docstring: every row gets the same conservative
    # announced_at (the ingestion timestamp) since this adapter has no way
    # to recover the true historical announcement date from a single scrape.
    announced_at = pd.Timestamp(dt.datetime.combine(ingest_date, dt.time.min))
    parsed["announced_at"] = announced_at

    valid, quarantined = validate_and_quarantine(parsed)

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    # Quarantined rows go through the store as an immutable bronze snapshot
    # under a sibling dataset name -- never a raw filesystem write (matches
    # vix.py/cboe_strategy.py; the lake is the only thing allowed to touch
    # storage).
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
    )
    _log_run(result, ingest_date, valid)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date, valid: pd.DataFrame) -> None:
    """Emit the run's full surface (`docs/STANDARDS.md` §f) -- an automated
    action without a runtime record is below the bar."""
    log_event(
        _LOGGER,
        "ingest.fomc",
        dataset=DATASET,
        source=SOURCE_ID,
        ingest_date=ingest_date.isoformat(),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
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
