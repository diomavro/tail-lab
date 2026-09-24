from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.earnings import DEFAULT_LOOKAHEAD_DAYS
from tail_lab.ingestion.event_calendar import IncompleteCalendarError, ingest_event_calendar
from tail_lab.lake.store import DeltaLakeStore

_INGEST = dt.date(2026, 9, 6)
_EVENT_DATE = dt.date(2026, 9, 8)

_YEAR_PANEL = (
    '<div class="panel panel-default"><div class="panel-heading">'
    '<h4><a id="1">{year} FOMC Meetings</a></h4></div>{rows}</div>'
)


def _meeting(month: str, date: str) -> str:
    return (
        '<div class="row fomc-meeting">'
        f'<div class="fomc-meeting__month"><strong>{month}</strong></div>'
        f'<div class="fomc-meeting__date">{date}</div>'
        "</div>"
    )


def _ingest(store: DeltaLakeStore, fomc_html: str, earnings: dict[str, Any], **kw: Any) -> Any:
    kw.setdefault("ingest_date", _INGEST)
    kw.setdefault("earnings_dates", [_EVENT_DATE])
    return ingest_event_calendar(
        store, fomc_html=fomc_html, earnings_fetch=kw.pop("fetch", lambda _d: earnings), **kw
    )


# ---- the reason this module exists -------------------------------------------


def test_both_halves_land_in_one_snapshot(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    """As two writers, whichever ran first claimed the day and the other
    half of the calendar was lost (measured failing every day, 2026-09-21).
    One write must carry both."""
    store = DeltaLakeStore(tmp_path)

    result = _ingest(store, fomc_calendar_sample, nasdaq_earnings_sample)

    assert result.committed is True
    assert dict(result.rows_by_source) == {"fed_calendar": 8, "nasdaq_earnings": 29}
    bronze = store.read_bronze_as_of("event_calendar", _INGEST)
    assert bronze["source_id"].value_counts().to_dict() == {
        "nasdaq_earnings": 29,
        "fed_calendar": 8,
    }


def test_a_rerun_the_same_day_is_a_reported_no_op(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    store = DeltaLakeStore(tmp_path)
    _ingest(store, fomc_calendar_sample, nasdaq_earnings_sample)

    again = _ingest(store, fomc_calendar_sample, nasdaq_earnings_sample)

    assert again.committed is False
    assert len(store.read_bronze_as_of("event_calendar", _INGEST)) == 37


# ---- refusing half a calendar -------------------------------------------------


def test_an_fomc_page_with_no_meetings_writes_nothing(
    tmp_path: Any, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    """As-of reads take the LATEST partition, so an earnings-only snapshot
    would silently shadow yesterday's complete calendar."""
    store = DeltaLakeStore(tmp_path)

    with pytest.raises(IncompleteCalendarError, match="FOMC"):
        _ingest(store, "<html>redesigned</html>", nasdaq_earnings_sample)
    assert not store.bronze_partition_exists("event_calendar", _INGEST)


def test_every_earnings_date_failing_writes_nothing(
    tmp_path: Any, fomc_calendar_sample: str
) -> None:
    store = DeltaLakeStore(tmp_path)

    def down(_d: dt.date) -> dict[str, Any]:
        raise ConnectionError("nasdaq down")

    with pytest.raises(IncompleteCalendarError, match="earnings"):
        _ingest(store, fomc_calendar_sample, {}, fetch=down, earnings_dates=[_EVENT_DATE, _INGEST])
    assert not store.bronze_partition_exists("event_calendar", _INGEST)


def test_one_bad_earnings_date_still_costs_only_itself(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    bad = dt.date(2026, 9, 9)

    def flaky(event_date: dt.date) -> dict[str, Any]:
        if event_date == bad:
            raise ConnectionError("simulated Nasdaq blip")
        return nasdaq_earnings_sample

    result = _ingest(
        DeltaLakeStore(tmp_path),
        fomc_calendar_sample,
        {},
        fetch=flaky,
        earnings_dates=[_EVENT_DATE, bad],
    )

    assert result.valid_rows == 37
    assert (result.earnings_dates_fetched, result.earnings_dates_failed) == (1, 1)


def test_an_empty_earnings_day_is_not_a_failure(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_empty_sample: dict[str, Any]
) -> None:
    """A weekend with no reports is a real answer, not a dead source."""
    result = _ingest(DeltaLakeStore(tmp_path), fomc_calendar_sample, nasdaq_earnings_empty_sample)

    assert dict(result.rows_by_source) == {"fed_calendar": 8}


# ---- carried over from the per-source adapters ---------------------------------


def test_announced_at_never_predates_the_actual_scrape(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    """Positive control for the point-in-time limitation both sources
    document: every row's announced_at equals the ingest timestamp, so an
    as-of read before this ingest sees none of these events -- conservative,
    never a look-ahead leak, though the Fed published its schedule long
    before the scrape."""
    store = DeltaLakeStore(tmp_path)
    _ingest(store, fomc_calendar_sample, nasdaq_earnings_sample)

    bronze = store.read_bronze_as_of("event_calendar", _INGEST)
    assert (bronze["announced_at"] == pd.Timestamp(_INGEST)).all()
    assert (bronze["announced_at"] > pd.Timestamp("2026-09-05")).all()


def test_the_default_window_spans_lookahead_days_forward(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_empty_sample: dict[str, Any]
) -> None:
    seen: list[dt.date] = []

    def recording(event_date: dt.date) -> dict[str, Any]:
        seen.append(event_date)
        return nasdaq_earnings_empty_sample

    ingest_event_calendar(
        DeltaLakeStore(tmp_path),
        ingest_date=_INGEST,
        fomc_html=fomc_calendar_sample,
        earnings_fetch=recording,
    )

    assert len(seen) == DEFAULT_LOOKAHEAD_DAYS
    assert seen[0] == _INGEST
    assert seen[-1] == _INGEST + dt.timedelta(days=DEFAULT_LOOKAHEAD_DAYS - 1)


def test_unparsed_rows_are_quarantined_without_losing_good_ones(
    tmp_path: Any, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    store = DeltaLakeStore(tmp_path)
    html = _YEAR_PANEL.format(
        year=2026, rows=_meeting("January", "27-28") + _meeting("Xyzember", "1-2")
    )

    result = _ingest(store, html, nasdaq_earnings_sample)

    assert result.valid_rows == 30
    assert result.quarantined_rows == 1
    assert len(store.read_bronze_as_of("event_calendar__quarantine", _INGEST)) == 1


def test_a_failed_quarantine_write_does_not_fail_the_snapshot(
    tmp_path: Any, nasdaq_earnings_sample: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DeltaLakeStore(tmp_path)
    real = store.write_bronze

    def write(dataset: str, day: dt.date, frame: Any) -> str:
        if dataset.endswith("__quarantine"):
            raise RuntimeError("SchemaMismatchError")
        return real(dataset, day, frame)

    monkeypatch.setattr(store, "write_bronze", write)
    html = _YEAR_PANEL.format(
        year=2026, rows=_meeting("January", "27-28") + _meeting("Xyzember", "1-2")
    )

    result = _ingest(store, html, nasdaq_earnings_sample)

    assert result.committed is True
    assert result.quarantine_path is None
    assert len(store.read_bronze_as_of("event_calendar", _INGEST)) == 30


def test_the_run_is_logged_with_its_full_surface(
    tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
    fomc_calendar_sample: str,
    nasdaq_earnings_sample: dict[str, Any],
) -> None:
    """docs/STANDARDS.md §f: an automated action without a runtime record is
    below the bar."""
    with caplog.at_level("INFO", logger="tail_lab.ingestion.event_calendar"):
        _ingest(DeltaLakeStore(tmp_path), fomc_calendar_sample, nasdaq_earnings_sample)

    line = "\n".join(caplog.messages)
    for field in (
        "event=ingest.event_calendar",
        "dataset=event_calendar",
        "valid_rows=37",
        "quarantined_rows=0",
        "earnings_dates_fetched=1",
        "earnings_dates_failed=0",
        "earnings_symbols=29",
        "rows_by_source=fed_calendar:8,nasdaq_earnings:29",
        "first_event_date=",
        "last_event_date=",
    ):
        assert field in line, field
    assert "committed=true" in line.lower()


def test_a_page_whose_every_meeting_fails_to_parse_writes_nothing(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    """The parser turns every unreadable row into a malformed row for
    quarantine, so a redesigned month format parses NON-empty with zero valid
    meetings. Checking the parse instead of the validated rows let an
    earnings-only calendar through (adversarial review, 2026-09-24)."""
    store = DeltaLakeStore(tmp_path)
    redesigned = re.sub(
        r"(fomc-meeting__month[^>]*><strong>)[^<]*(</strong>)", r"\1Foo\2", fomc_calendar_sample
    )
    assert "Foo" in redesigned

    with pytest.raises(IncompleteCalendarError, match="FOMC"):
        _ingest(store, redesigned, nasdaq_earnings_sample)
    assert not store.bronze_partition_exists("event_calendar", _INGEST)


def test_nasdaq_errors_served_as_http_200_count_as_failures(
    tmp_path: Any, fomc_calendar_sample: str
) -> None:
    """Measured live: a rejected request answers HTTP 200 with
    ``data: null`` and ``status.rCode: 400`` -- indistinguishable from "no
    earnings that day" unless the status is read."""
    store = DeltaLakeStore(tmp_path)
    rejected = {"data": None, "status": {"rCode": 400, "bCodeMessage": [{"code": 1001}]}}

    with pytest.raises(IncompleteCalendarError, match="earnings"):
        _ingest(store, fomc_calendar_sample, rejected, earnings_dates=[_EVENT_DATE, _INGEST])
    assert not store.bronze_partition_exists("event_calendar", _INGEST)


def test_most_earnings_dates_failing_writes_nothing(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    """29 of 30 dates failing is a dead source, not a few blips -- writing it
    would shadow the last complete calendar with one day of earnings."""
    dates = [_INGEST + dt.timedelta(days=i) for i in range(30)]

    def mostly_down(event_date: dt.date) -> dict[str, Any]:
        if event_date != _EVENT_DATE:
            raise ConnectionError("nasdaq down")
        return nasdaq_earnings_sample

    with pytest.raises(IncompleteCalendarError, match="29 of 30"):
        _ingest(
            DeltaLakeStore(tmp_path),
            fomc_calendar_sample,
            {},
            fetch=mostly_down,
            earnings_dates=dates,
        )


def test_a_malformed_page_costs_only_its_own_date(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    """A block page served as text used to raise out of the parser and, now
    that FOMC shares the write, would have cost the whole calendar."""
    bad = dt.date(2026, 9, 9)

    def fetch(event_date: dt.date) -> Any:
        return "<html>blocked</html>" if event_date == bad else nasdaq_earnings_sample

    result = _ingest(
        DeltaLakeStore(tmp_path),
        fomc_calendar_sample,
        {},
        fetch=fetch,
        earnings_dates=[_EVENT_DATE, bad, dt.date(2026, 9, 10)],
    )

    assert result.earnings_dates_failed == 1
    assert dict(result.rows_by_source)["nasdaq_earnings"] == 2 * 29  # the two good dates


def test_lookahead_days_sets_the_window(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_empty_sample: dict[str, Any]
) -> None:
    seen: list[dt.date] = []

    def recording(event_date: dt.date) -> dict[str, Any]:
        seen.append(event_date)
        return nasdaq_earnings_empty_sample

    ingest_event_calendar(
        DeltaLakeStore(tmp_path),
        ingest_date=_INGEST,
        fomc_html=fomc_calendar_sample,
        earnings_fetch=recording,
        lookahead_days=5,
    )

    assert seen == [_INGEST + dt.timedelta(days=i) for i in range(5)]


def test_a_page_the_parser_chokes_on_costs_only_its_own_date(
    tmp_path: Any, fomc_calendar_sample: str, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    """A well-formed envelope around a malformed row crashes the parser
    itself (``rows: [null]``), not the API-status check -- so parsing has to
    sit inside the per-date try as well."""
    bad = dt.date(2026, 9, 9)

    def fetch(event_date: dt.date) -> Any:
        if event_date == bad:
            return {"data": {"rows": [None]}, "status": {"rCode": 200}}
        return nasdaq_earnings_sample

    result = _ingest(
        DeltaLakeStore(tmp_path),
        fomc_calendar_sample,
        {},
        fetch=fetch,
        earnings_dates=[_EVENT_DATE, bad, dt.date(2026, 9, 10)],
    )

    assert result.earnings_dates_failed == 1
