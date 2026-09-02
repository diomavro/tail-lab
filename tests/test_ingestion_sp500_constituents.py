from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.sp500_constituents import (
    ingest_sp500_constituents,
    parse_constituents_csv,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore


def test_parse_against_real_source_fixture(sp500_constituents_sample: str) -> None:
    df = parse_constituents_csv(sp500_constituents_sample)

    assert list(df.columns) == ["obs_date", "tickers"]
    assert df["obs_date"].is_monotonic_increasing
    assert df["obs_date"].is_unique
    assert df["tickers"].notna().all()
    assert len(df) == 9


def test_parse_keeps_the_full_membership_list_as_one_string(
    sp500_constituents_sample: str,
) -> None:
    """The contract keeps `tickers` as the source's own comma-joined shape
    (see the contract module docstring) -- pinning a real row proves a name
    that later delisted survives in the historical record, which is the
    whole point of a point-in-time constituents dataset (`docs/adr/0010`):
    a "current constituents" universe would never show ABKFQ (Ambac
    Financial, later bankrupt) at all."""
    df = parse_constituents_csv(sp500_constituents_sample)

    row = df.loc[df["obs_date"] == pd.Timestamp("2007-12-04")]
    assert len(row) == 1
    tickers = row["tickers"].iloc[0].split(",")
    assert "ABKFQ" in tickers
    assert "AAPL" in tickers
    assert len(tickers) == 497


def test_parse_reflects_a_real_membership_change(sp500_constituents_sample: str) -> None:
    """Two adjacent 2026 observation dates in the fixture carry different
    member counts -- proof the parser is not silently collapsing genuine
    changes rather than a coincidence of the sample chosen."""
    df = parse_constituents_csv(sp500_constituents_sample)

    before = df.loc[df["obs_date"] == pd.Timestamp("2026-06-24"), "tickers"].iloc[0]
    after = df.loc[df["obs_date"] == pd.Timestamp("2026-06-29"), "tickers"].iloc[0]
    assert before != after


def test_parse_drops_blank_ticker_rows_but_keeps_malformed_dates() -> None:
    raw = 'date,tickers\n2026-01-02,"AAPL,MSFT"\n2026-01-05,\nnot-a-date,"AAPL"\n'
    df = parse_constituents_csv(raw)

    # The blank-tickers row is gone; the garbled date remains for quarantine.
    assert len(df) == 2
    assert df["obs_date"].isna().sum() == 1


def test_parse_raises_when_the_source_header_changes_shape() -> None:
    """The earlier version fell back to the first and last columns when it
    could not find `date`/`tickers`. That is right for today's header by
    coincidence and silently wrong the moment the source adds or reorders a
    column -- it would parse some other column as dates, on the one dataset
    whose whole purpose is knowing WHICH names were in the index WHEN
    (docs/adr/0010). A survivorship panel quietly off by a column is worse
    than no panel, so this fails loudly instead."""
    reordered = 'idx,date,tickers\n1,2024-01-02,"AAPL,MSFT"\n'
    parse_constituents_csv(reordered)  # still fine: both names are present

    renamed = 'observation_date,members\n2024-01-02,"AAPL,MSFT"\n'
    with pytest.raises(ValueError, match="missing required column"):
        parse_constituents_csv(renamed)


def test_parse_empty_source_returns_typed_empty_frame() -> None:
    df = parse_constituents_csv("date,tickers\n")

    assert df.empty
    assert list(df.columns) == ["obs_date", "tickers"]


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(["2026-01-02", pd.NaT, "2026-01-06"]),
            "tickers": ["AAPL,MSFT", "AAPL,MSFT", "AAPL,MSFT,SPY"],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert len(quarantined) == 1


def test_validate_and_quarantine_all_valid_quarantines_nothing() -> None:
    df = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
            "tickers": ["AAPL,MSFT", "AAPL,MSFT,SPY"],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert quarantined.empty


def test_ingest_commits_one_bronze_snapshot(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = 'date,tickers\n2026-01-02,"AAPL,MSFT"\n2026-01-05,"AAPL,MSFT,SPY"\n'
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_sp500_constituents(store, ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 2
    assert result.quarantined_rows == 0
    assert result.quarantine_path is None

    bronze = store.read_bronze_as_of("sp500_constituents", ingest_date)
    assert len(bronze) == 2


def test_ingest_quarantines_bad_rows_without_losing_good_ones(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = 'date,tickers\n2026-01-02,"AAPL,MSFT"\nnot-a-date,"AAPL"\n2026-01-06,"AAPL,MSFT,SPY"\n'
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_sp500_constituents(store, ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 2
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None

    quarantined = store.read_bronze_as_of("sp500_constituents__quarantine", ingest_date)
    assert len(quarantined) == 1


def test_ingest_all_valid_writes_no_quarantine_partition(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = 'date,tickers\n2026-01-02,"AAPL,MSFT"\n'
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_sp500_constituents(store, ingest_date=ingest_date, raw=raw)

    assert result.quarantined_rows == 0
    assert result.quarantine_path is None
    with pytest.raises(LookupError):
        store.read_bronze_as_of("sp500_constituents__quarantine", ingest_date)


def test_ingest_logs_the_full_run_surface(tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    """docs/STANDARDS.md §f: an automated action without a runtime record is
    below the bar, so the run line is part of the contract, not decoration."""
    store = DeltaLakeStore(tmp_path)
    raw = 'date,tickers\n2026-01-02,"AAPL,MSFT"\n2026-01-05,"AAPL,MSFT,SPY"\n'
    with caplog.at_level("INFO", logger="tail_lab.ingestion.sp500_constituents"):
        ingest_sp500_constituents(store, ingest_date=dt.date(2026, 1, 6), raw=raw)

    line = "\n".join(caplog.messages)
    assert "event=ingest.sp500_constituents" in line
    assert "dataset=sp500_constituents" in line
    assert "source=github_fja05680" in line
    assert "valid_rows=2" in line
    assert "quarantined_rows=0" in line
    assert "first_obs_date=2026-01-02" in line
    assert "last_obs_date=2026-01-05" in line
