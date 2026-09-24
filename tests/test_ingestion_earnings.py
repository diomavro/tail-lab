from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from tail_lab.ingestion.earnings import (
    parse_earnings_calendar_json,
    validate_and_quarantine,
)

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
