from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.contracts.options_expiry import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.cadence import live_cadence_for


def _seed_expiry(
    store: DeltaLakeStore, symbol: str, ingest_date: dt.date, expirations: list[str]
) -> None:
    df = pd.DataFrame({"symbol": symbol.upper(), "expiration_date": pd.to_datetime(expirations)})
    store.write_bronze(dataset_id(symbol), ingest_date, df)


def test_live_cadence_for_weekly_chain(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    as_of = dt.date(2026, 1, 1)
    _seed_expiry(store, "spy", as_of, ["2026-01-08", "2026-01-15", "2026-01-22"])

    result = live_cadence_for(store, "spy", as_of=as_of)

    assert result.symbol == "SPY"
    assert result.cadence == "weekly"
    assert result.avg_gap_days == pytest.approx(7.0)
    assert "live" in result.label.lower()
    # Display name is reused from the curated catalogue, not fabricated.
    assert result.name == "S&P 500 (SPY)"


def test_live_cadence_for_monthly_chain(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    as_of = dt.date(2026, 1, 1)
    _seed_expiry(store, "xlu", as_of, ["2026-01-31", "2026-03-02", "2026-04-01"])

    result = live_cadence_for(store, "xlu", as_of=as_of)

    assert result.cadence == "monthly"
    assert result.avg_gap_days == pytest.approx(30.0)
    assert "live" in result.label.lower()


def test_live_cadence_for_unlisted_symbol_falls_back_to_default_name(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    as_of = dt.date(2026, 1, 1)
    _seed_expiry(store, "zzz", as_of, ["2026-01-08", "2026-01-15"])

    result = live_cadence_for(store, "zzz", as_of=as_of)

    assert result.symbol == "ZZZ"
    assert result.cadence == "weekly"
    assert "assumed" in result.name.lower() or result.name == "ZZZ"


def test_live_cadence_for_raises_lookup_error_when_no_bronze_snapshot(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)

    with pytest.raises(LookupError):
        live_cadence_for(store, "spy", as_of=dt.date(2026, 1, 1))


def test_live_cadence_for_raises_value_error_on_too_few_near_term_expirations(
    tmp_path: Path,
) -> None:
    store = DeltaLakeStore(tmp_path)
    as_of = dt.date(2026, 1, 1)
    _seed_expiry(store, "spy", as_of, ["2026-01-08"])

    with pytest.raises(ValueError, match="need at least 2"):
        live_cadence_for(store, "spy", as_of=as_of)


def test_live_cadence_for_respects_no_look_ahead(tmp_path: Path) -> None:
    """Point-in-time: an as-of read of an earlier ingest date must never
    reflect a later ingest's chain (docs/adr/0009)."""
    store = DeltaLakeStore(tmp_path)
    day1 = dt.date(2026, 1, 1)
    day2 = dt.date(2026, 1, 2)

    _seed_expiry(store, "spy", day1, ["2026-01-08", "2026-01-15", "2026-01-22"])
    result_day1 = live_cadence_for(store, "spy", as_of=day1)
    assert result_day1.cadence == "weekly"

    # A later snapshot restates the chain into a monthly-only listing --
    # this must not leak into the day1 read.
    _seed_expiry(store, "spy", day2, ["2026-01-31", "2026-03-02", "2026-04-01"])
    result_day1_again = live_cadence_for(store, "spy", as_of=day1)

    assert result_day1_again == result_day1
