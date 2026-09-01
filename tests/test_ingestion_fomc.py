from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.fomc import (
    ingest_fomc_calendar,
    parse_fomc_calendar_html,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore

_YEAR_PANEL = (
    '<div class="panel panel-default"><div class="panel-heading">'
    '<h4><a id="1">{year} FOMC Meetings</a></h4></div>{rows}</div>'
)


def _row(month: str, date: str) -> str:
    return (
        '<div class="row fomc-meeting">'
        f'<div class="fomc-meeting__month"><strong>{month}</strong></div>'
        f'<div class="fomc-meeting__date">{date}</div>'
        "</div>"
    )


def test_parse_against_real_fomc_fixture(fomc_calendar_sample: str) -> None:
    df = parse_fomc_calendar_html(fomc_calendar_sample)

    assert list(df.columns) == [
        "event_id",
        "event_type",
        "event_date",
        "symbol",
        "description",
        "source_id",
    ]
    assert len(df) == 8
    assert (df["event_type"] == "FOMC").all()
    assert (df["source_id"] == "fed_calendar").all()
    assert df["symbol"].isna().all()
    assert df["event_date"].is_monotonic_increasing
    assert df["event_id"].is_unique


def test_parse_picks_the_second_day_as_the_event_date(fomc_calendar_sample: str) -> None:
    """The statement/decision -- what moves markets -- lands on the meeting's
    last day, matching the date in the Fed's own press-release URLs
    (e.g. monetary20240131a.htm for the Jan 30-31, 2024 meeting)."""
    df = parse_fomc_calendar_html(fomc_calendar_sample)

    jan = df.loc[df["event_id"] == "fomc_2024-01-31"]
    assert len(jan) == 1


def test_parse_handles_a_month_spanning_meeting(fomc_calendar_sample: str) -> None:
    """The load-bearing edge case: 'Apr/May' + '30-1' means April 30 - May 1,
    so the event date must land in May, not April -- getting this wrong
    would shift a whole meeting's event_date by a month."""
    df = parse_fomc_calendar_html(fomc_calendar_sample)

    assert (df["event_id"] == "fomc_2024-05-01").any()
    assert not (df["event_id"] == "fomc_2024-04-30").any()


def test_parse_flags_sep_meetings_in_the_description(fomc_calendar_sample: str) -> None:
    """The '*' suffix on a date cell marks a Summary of Economic Projections
    meeting -- the fixture's March, June, September and December rows."""
    df = parse_fomc_calendar_html(fomc_calendar_sample)

    sep_row = df.loc[df["event_id"] == "fomc_2024-03-20"].iloc[0]
    assert "Summary of Economic Projections" in sep_row["description"]

    plain_row = df.loc[df["event_id"] == "fomc_2024-01-31"].iloc[0]
    assert "Summary of Economic Projections" not in plain_row["description"]


def test_parse_handles_year_boundaries_across_two_panels() -> None:
    html = _YEAR_PANEL.format(year=2025, rows=_row("December", "9-10*")) + _YEAR_PANEL.format(
        year=2026, rows=_row("January", "27-28")
    )
    df = parse_fomc_calendar_html(html)

    assert set(df["event_id"]) == {"fomc_2025-12-10", "fomc_2026-01-28"}


def test_parse_unrecognized_month_survives_as_malformed_for_quarantine() -> None:
    """A row the parser can't make sense of must not be silently dropped --
    it needs to reach quarantine, not vanish."""
    html = _YEAR_PANEL.format(year=2026, rows=_row("Xyzember", "1-2"))
    df = parse_fomc_calendar_html(html)

    assert len(df) == 1
    assert pd.isna(df["event_date"].iloc[0])


def test_parse_empty_source_returns_typed_empty_frame() -> None:
    df = parse_fomc_calendar_html("<html><body>no meetings here</body></html>")

    assert df.empty
    assert list(df.columns) == [
        "event_id",
        "event_type",
        "event_date",
        "symbol",
        "description",
        "source_id",
    ]


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "event_id": ["fomc_2026-01-28", "fomc_unparsed_x_y"],
            "event_type": ["FOMC", "FOMC"],
            "event_date": [pd.Timestamp("2026-01-28"), pd.NaT],
            "symbol": [None, None],
            "description": ["FOMC meeting", "unparsed FOMC row"],
            "source_id": ["fed_calendar", "fed_calendar"],
        }
    )
    df["announced_at"] = pd.Timestamp("2026-01-01")
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 1
    assert len(quarantined) == 1
    assert quarantined["event_id"].iloc[0] == "fomc_unparsed_x_y"


def test_ingest_commits_a_bronze_snapshot(tmp_path: Any, fomc_calendar_sample: str) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 9, 1)
    result = ingest_fomc_calendar(store, ingest_date=ingest_date, raw=fomc_calendar_sample)

    assert result.valid_rows == 8
    assert result.quarantined_rows == 0
    assert result.quarantine_path is None

    bronze = store.read_bronze_as_of("event_calendar", ingest_date)
    assert len(bronze) == 8
    assert (bronze["announced_at"] == pd.Timestamp("2026-09-01")).all()


def test_ingest_announced_at_never_predates_the_actual_scrape(
    tmp_path: Any, fomc_calendar_sample: str
) -> None:
    """Adversarial positive control for the point-in-time limitation this
    adapter documents: every row's announced_at must equal the ingest
    timestamp, so an as-of read for any date before this ingest sees none
    of these events -- conservative, never a look-ahead leak, even though
    the Fed actually published this schedule long before the scrape."""
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 9, 1)
    ingest_fomc_calendar(store, ingest_date=ingest_date, raw=fomc_calendar_sample)

    bronze = store.read_bronze_as_of("event_calendar", ingest_date)
    simulated_asof = pd.Timestamp("2026-08-31")
    assert (bronze["announced_at"] > simulated_asof).all()


def test_ingest_quarantines_unparsed_rows_without_losing_good_ones(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    html = _YEAR_PANEL.format(
        year=2026,
        rows=_row("January", "27-28") + _row("Xyzember", "1-2"),
    )
    ingest_date = dt.date(2026, 9, 1)
    result = ingest_fomc_calendar(store, ingest_date=ingest_date, raw=html)

    assert result.valid_rows == 1
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None

    quarantined = store.read_bronze_as_of("event_calendar__quarantine", ingest_date)
    assert len(quarantined) == 1


def test_ingest_logs_the_full_run_surface(
    tmp_path: Any, caplog: pytest.LogCaptureFixture, fomc_calendar_sample: str
) -> None:
    """docs/STANDARDS.md §f: an automated action without a runtime record is
    below the bar, so the run line is part of the contract, not decoration."""
    store = DeltaLakeStore(tmp_path)
    with caplog.at_level("INFO", logger="tail_lab.ingestion.fomc"):
        ingest_fomc_calendar(store, ingest_date=dt.date(2026, 9, 1), raw=fomc_calendar_sample)

    line = "\n".join(caplog.messages)
    assert "event=ingest.fomc" in line
    assert "dataset=event_calendar" in line
    assert "source=fed_calendar" in line
    assert "valid_rows=8" in line
    assert "quarantined_rows=0" in line
    assert "first_event_date=2024-01-31" in line
    assert "last_event_date=2024-12-18" in line
