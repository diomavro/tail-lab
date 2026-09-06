from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.earnings import (
    ingest_earnings_calendar,
    parse_earnings_calendar_json,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore

_EVENT_DATE = dt.date(2026, 9, 8)


def test_parse_against_real_earnings_fixture(nasdaq_earnings_sample: dict[str, Any]) -> None:
    df = parse_earnings_calendar_json(nasdaq_earnings_sample, _EVENT_DATE)

    assert list(df.columns) == [
        "event_id",
        "event_type",
        "event_date",
        "symbol",
        "description",
        "source_id",
    ]
    assert len(df) == 29
    assert (df["event_type"] == "EARNINGS").all()
    assert (df["source_id"] == "nasdaq_earnings").all()
    assert (df["event_date"] == pd.Timestamp(_EVENT_DATE)).all()
    assert df["symbol"].notna().all()
    assert df["event_id"].is_unique
    assert "CASY" in set(df["symbol"])


def test_parse_labels_pre_market_and_after_hours_in_the_description(
    nasdaq_earnings_sample: dict[str, Any],
) -> None:
    df = parse_earnings_calendar_json(nasdaq_earnings_sample, _EVENT_DATE)

    casy = df.loc[df["symbol"] == "CASY"].iloc[0]  # time-after-hours in the fixture
    assert "after market close" in casy["description"]

    abm = df.loc[df["symbol"] == "ABM"].iloc[0]  # time-pre-market in the fixture
    assert "before market open" in abm["description"]

    gme = df.loc[df["symbol"] == "GME"].iloc[0]  # time-not-supplied in the fixture
    assert "before market open" not in gme["description"]
    assert "after market close" not in gme["description"]


def test_parse_no_earnings_date_returns_typed_empty_frame(
    nasdaq_earnings_empty_sample: dict[str, Any],
) -> None:
    """A weekend/holiday date comes back with ``rows: null``, not ``[]`` --
    must not crash and must not be mistaken for a fetch failure."""
    df = parse_earnings_calendar_json(nasdaq_earnings_empty_sample, dt.date(2026, 9, 6))

    assert df.empty
    assert list(df.columns) == [
        "event_id",
        "event_type",
        "event_date",
        "symbol",
        "description",
        "source_id",
    ]


def test_parse_row_missing_symbol_survives_as_malformed_for_quarantine() -> None:
    """A row Nasdaq serves with no symbol has no permanent identifier to key
    on -- must not be silently dropped, must reach quarantine instead."""
    raw = {"data": {"rows": [{"name": "Mystery Corp", "time": "time-not-supplied"}]}}
    df = parse_earnings_calendar_json(raw, _EVENT_DATE)

    assert len(df) == 1
    assert df["symbol"].iloc[0] is None


def test_validate_and_quarantine_splits_rows_missing_a_symbol() -> None:
    df = pd.DataFrame(
        {
            "event_id": ["earnings_AAPL_2026-09-08", "earnings_unparsed_2026-09-08_0"],
            "event_type": ["EARNINGS", "EARNINGS"],
            "event_date": [pd.Timestamp("2026-09-08"), pd.Timestamp("2026-09-08")],
            "symbol": ["AAPL", None],
            "description": ["Apple Inc. earnings", "unparsed earnings row: missing symbol"],
            "source_id": ["nasdaq_earnings", "nasdaq_earnings"],
        }
    )
    df["announced_at"] = pd.Timestamp("2026-09-06")
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 1
    assert valid["symbol"].iloc[0] == "AAPL"
    assert len(quarantined) == 1
    assert quarantined["event_id"].iloc[0] == "earnings_unparsed_2026-09-08_0"


def test_ingest_commits_a_bronze_snapshot(
    tmp_path: Any,
    nasdaq_earnings_sample: dict[str, Any],
    nasdaq_earnings_empty_sample: dict[str, Any],
) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 9, 6)
    dates = [dt.date(2026, 9, 6), dt.date(2026, 9, 8)]
    payloads = {dates[0]: nasdaq_earnings_empty_sample, dates[1]: nasdaq_earnings_sample}

    result = ingest_earnings_calendar(
        store, ingest_date=ingest_date, dates=dates, fetch=lambda d: payloads[d]
    )

    assert result.valid_rows == 29
    assert result.quarantined_rows == 0
    assert result.dates_fetched == 2
    assert result.dates_failed == 0

    bronze = store.read_bronze_as_of("event_calendar", ingest_date)
    assert len(bronze) == 29
    assert (bronze["announced_at"] == pd.Timestamp("2026-09-06")).all()
    assert (bronze["source_id"] == "nasdaq_earnings").all()


def test_ingest_announced_at_never_predates_the_actual_scrape(
    tmp_path: Any, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    """Positive control mirroring the FOMC adapter's: an as-of read before
    this ingest must see none of these rows, even though Nasdaq lists a
    future report date -- conservative, never a look-ahead leak."""
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 9, 6)
    ingest_earnings_calendar(
        store, ingest_date=ingest_date, dates=[_EVENT_DATE], fetch=lambda d: nasdaq_earnings_sample
    )

    bronze = store.read_bronze_as_of("event_calendar", ingest_date)
    simulated_asof = pd.Timestamp("2026-09-05")
    assert (bronze["announced_at"] > simulated_asof).all()


def test_ingest_tolerates_a_single_bad_date_without_losing_the_rest(
    tmp_path: Any, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 9, 6)
    good_date = _EVENT_DATE
    bad_date = dt.date(2026, 9, 9)

    def flaky_fetch(event_date: dt.date) -> dict[str, Any]:
        if event_date == bad_date:
            raise ConnectionError("simulated Nasdaq blip")
        return nasdaq_earnings_sample

    result = ingest_earnings_calendar(
        store, ingest_date=ingest_date, dates=[good_date, bad_date], fetch=flaky_fetch
    )

    assert result.valid_rows == 29
    assert result.dates_fetched == 1
    assert result.dates_failed == 1


def test_ingest_refuses_to_write_over_an_existing_same_day_partition(tmp_path: Any) -> None:
    """The `event_calendar` bronze collision guard: a plain second write on a
    day another producer (e.g. `ingestion/fomc.py`) already committed would
    silently no-op and lose this run's rows. Must fail loudly instead."""
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 9, 6)
    fomc_row = pd.DataFrame(
        {
            "event_id": ["fomc_2026-09-16"],
            "event_type": ["FOMC"],
            "event_date": [pd.Timestamp("2026-09-16")],
            "announced_at": [pd.Timestamp("2026-09-06")],
            "symbol": [None],
            "description": ["FOMC meeting"],
            "source_id": ["fed_calendar"],
        }
    )
    store.write_bronze("event_calendar", ingest_date, fomc_row)

    with pytest.raises(RuntimeError, match="already has a bronze partition"):
        ingest_earnings_calendar(
            store, ingest_date=ingest_date, dates=[_EVENT_DATE], fetch=lambda d: {}
        )


def test_ingest_does_not_refuse_on_an_older_unrelated_partition(
    tmp_path: Any, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    """The guard only fires for a partition dated exactly ``ingest_date`` --
    an older snapshot from a previous day must not block today's run."""
    store = DeltaLakeStore(tmp_path)
    # symbol is a real string, not None: an all-null column round-trips
    # through Delta as an untyped ``null`` column, which then conflicts
    # with the later earnings write's string-typed ``symbol`` column --
    # a pandas/Arrow schema-inference quirk unrelated to what this test
    # actually checks (that an *older* partition doesn't block today's run).
    earlier_row = pd.DataFrame(
        {
            "event_id": ["earnings_ZZZ_2026-08-01"],
            "event_type": ["EARNINGS"],
            "event_date": [pd.Timestamp("2026-08-01")],
            "announced_at": [pd.Timestamp("2026-08-01")],
            "symbol": ["ZZZ"],
            "description": ["ZZZ earnings"],
            "source_id": ["nasdaq_earnings"],
        }
    )
    store.write_bronze("event_calendar", dt.date(2026, 8, 1), earlier_row)

    result = ingest_earnings_calendar(
        store,
        ingest_date=dt.date(2026, 9, 6),
        dates=[_EVENT_DATE],
        fetch=lambda d: nasdaq_earnings_sample,
    )

    assert result.valid_rows == 29


def test_ingest_default_window_spans_lookahead_days_forward(
    nasdaq_earnings_empty_sample: dict[str, Any], tmp_path: Any
) -> None:
    """No explicit ``dates`` override -> the adapter sweeps
    ``DEFAULT_LOOKAHEAD_DAYS`` calendar days starting at ``ingest_date``."""
    store = DeltaLakeStore(tmp_path)
    seen: list[dt.date] = []

    def recording_fetch(event_date: dt.date) -> dict[str, Any]:
        seen.append(event_date)
        return nasdaq_earnings_empty_sample

    ingest_date = dt.date(2026, 9, 6)
    result = ingest_earnings_calendar(store, ingest_date=ingest_date, fetch=recording_fetch)

    assert result.dates_fetched == 30
    assert seen[0] == ingest_date
    assert seen[-1] == ingest_date + dt.timedelta(days=29)


def test_ingest_writes_quarantined_rows_as_a_sibling_bronze_snapshot(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {"data": {"rows": [{"name": "Mystery Corp", "time": "time-not-supplied"}]}}
    ingest_date = dt.date(2026, 9, 6)

    result = ingest_earnings_calendar(
        store, ingest_date=ingest_date, dates=[_EVENT_DATE], fetch=lambda d: raw
    )

    assert result.valid_rows == 0
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None

    quarantined = store.read_bronze_as_of("event_calendar__quarantine", ingest_date)
    assert len(quarantined) == 1


def test_ingest_logs_the_full_run_surface(
    tmp_path: Any, caplog: pytest.LogCaptureFixture, nasdaq_earnings_sample: dict[str, Any]
) -> None:
    """docs/STANDARDS.md §f: an automated action without a runtime record is
    below the bar, so the run line is part of the contract, not decoration."""
    store = DeltaLakeStore(tmp_path)
    with caplog.at_level("INFO", logger="tail_lab.ingestion.earnings"):
        ingest_earnings_calendar(
            store,
            ingest_date=dt.date(2026, 9, 6),
            dates=[_EVENT_DATE],
            fetch=lambda d: nasdaq_earnings_sample,
        )

    line = "\n".join(caplog.messages)
    assert "event=ingest.earnings" in line
    assert "dataset=event_calendar" in line
    assert "source=nasdaq_earnings" in line
    assert "valid_rows=29" in line
    assert "quarantined_rows=0" in line
    assert "dates_fetched=1" in line
    assert "dates_failed=0" in line
