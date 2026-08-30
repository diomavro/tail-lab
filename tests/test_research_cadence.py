"""Tests for ``research/cadence.py`` — live-derived listing cadence with a
fallback to the hand-maintained table (``contracts/options_calendar.py``)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.contracts.options_expiry import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.cadence import resolve_cadence

AS_OF = dt.date(2026, 1, 5)


def _write_expiry(
    store: DeltaLakeStore, symbol: str, dates: list[dt.date], *, ingest: dt.date = AS_OF
) -> None:
    df = pd.DataFrame(
        {
            "symbol": pd.Series([symbol.upper()] * len(dates), dtype="object"),
            "expiration_date": pd.Series(pd.to_datetime(dates), dtype="datetime64[ns]"),
        }
    )
    store.write_bronze(dataset_id(symbol), ingest, df)


def test_falls_back_to_static_table_when_no_snapshot_exists(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    result = resolve_cadence(store, "spy", as_of=AS_OF)
    assert result.cadence == "weekly"
    assert result.avg_gap_days == pytest.approx(2.5)  # the static table's SPY entry
    assert "live" not in result.label.lower()


def test_falls_back_to_the_flagged_default_for_an_unknown_symbol(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    result = resolve_cadence(store, "zzz", as_of=AS_OF)
    assert result.symbol == "ZZZ"
    assert "assumed" in result.label.lower()


def test_falls_back_when_too_few_near_term_expirations_to_classify(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    # A single far-dated LEAPS expiry: classify_cadence needs >= 2 within its
    # near-term window, so this must fall back rather than propagate a raise.
    _write_expiry(store, "spy", [AS_OF + dt.timedelta(days=400)])
    result = resolve_cadence(store, "spy", as_of=AS_OF)
    assert result.avg_gap_days == pytest.approx(2.5)
    assert "live" not in result.label.lower()


def test_live_snapshot_confirms_a_weekly_cadence(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    dates = [AS_OF + dt.timedelta(days=d) for d in range(0, 90, 7)]
    _write_expiry(store, "spy", dates)

    result = resolve_cadence(store, "spy", as_of=AS_OF)
    assert result.cadence == "weekly"
    assert result.avg_gap_days == pytest.approx(7.0)
    assert result.label == "Weeklies (live)"
    assert "listed chain" in result.detail
    assert result.symbol == "SPY"  # display identity kept from the static table


def test_live_snapshot_can_reclassify_a_symbol_to_monthly(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    # IWM is "weekly" in the static table; a live chain listing only monthlies
    # must override that, not defer to it.
    dates = [AS_OF + dt.timedelta(days=d) for d in range(0, 90, 30)]
    _write_expiry(store, "iwm", dates)

    result = resolve_cadence(store, "iwm", as_of=AS_OF)
    assert result.cadence == "monthly"
    assert result.avg_gap_days == pytest.approx(30.0)
    assert result.label == "Monthlies (live)"


def test_point_in_time_a_later_snapshot_does_not_leak_backward(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    dates = [AS_OF + dt.timedelta(days=d) for d in range(0, 90, 7)]
    _write_expiry(store, "spy", dates, ingest=AS_OF + dt.timedelta(days=30))

    result = resolve_cadence(store, "spy", as_of=AS_OF)
    assert "live" not in result.label.lower()  # nothing known yet as of AS_OF
