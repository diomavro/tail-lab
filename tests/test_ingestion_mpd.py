from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.mpd import (
    ingest_mpd,
    parse_mpd_csv,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore

_CONTRACT_COLUMNS = [
    "market",
    "obs_date",
    "maturity_months",
    "mu",
    "sd",
    "skew",
    "kurt",
    "p10",
    "p50",
    "p90",
    "prob_large_decline",
    "prob_large_increase",
]


def test_parse_against_real_mpd_fixture(mpd_stats_sample: str) -> None:
    df = parse_mpd_csv(mpd_stats_sample)

    assert list(df.columns) == _CONTRACT_COLUMNS
    assert set(df["market"]) == {"sp12m", "bac", "infl1y"}
    assert df["obs_date"].notna().all()
    # sp12m's crisis-window rows: pinning one exact value proves the
    # MM/DD/YYYY parse and the prDec -> prob_large_decline rename both landed
    # on the right cell, not just that a number came through.
    sept_30 = df.loc[(df["market"] == "sp12m") & (df["obs_date"] == pd.Timestamp("2008-09-30"))]
    assert len(sept_30) == 1
    assert sept_30["prob_large_decline"].iloc[0] == pytest.approx(0.237059876322746)
    assert sept_30["skew"].iloc[0] == pytest.approx(-0.91038054227829)


def test_parse_treats_blank_maturity_target_as_null_not_a_parse_failure(
    mpd_stats_sample: str,
) -> None:
    """The source leaves ``maturity_target`` blank (``NA``) for a real subset
    of rows -- e.g. sp12m/2016-02-11 in the fixture. That is missing data,
    not a malformed row, so it must come through as null rather than
    quarantined or dropped."""
    df = parse_mpd_csv(mpd_stats_sample)
    row = df.loc[(df["market"] == "sp12m") & (df["obs_date"] == pd.Timestamp("2016-02-11"))]

    assert len(row) == 1
    assert pd.isna(row["maturity_months"].iloc[0])


def test_parse_skips_preamble_above_the_header() -> None:
    raw = (
        '"free-text preamble line"\n'
        '"a second preamble line"\n'
        '""\n'
        '"market","idt","maturity_target","mu","sd","skew","kurt","p10","p50","p90",'
        '"lg_change_decr","prDec","lg_change_incr","prInc"\n'
        '"sp12m","01/02/2026",12,0.01,0.2,-1.0,2.0,-0.2,0.05,0.2,-20,0.15,20,0.1\n'
    )
    df = parse_mpd_csv(raw)

    assert len(df) == 1
    assert df["market"].iloc[0] == "sp12m"
    assert df["prob_large_decline"].iloc[0] == pytest.approx(0.15)


def test_parse_keeps_malformed_rows_for_quarantine_to_see() -> None:
    """A garbled date or a non-numeric stat is a *bad* row, not a blank one --
    it must survive parsing so ``validate_and_quarantine`` can catch it."""
    raw = (
        '"market","idt","maturity_target","mu","sd","skew","kurt","p10","p50","p90",'
        '"lg_change_decr","prDec","lg_change_incr","prInc"\n'
        '"sp12m","99/99/2026",12,0.01,0.2,-1.0,2.0,-0.2,0.05,0.2,-20,0.15,20,0.1\n'
        '"sp12m","01/02/2026",12,not-a-number,0.2,-1.0,2.0,-0.2,0.05,0.2,-20,0.15,20,0.1\n'
    )
    df = parse_mpd_csv(raw)

    assert len(df) == 2
    assert df["obs_date"].isna().sum() == 1
    assert df["mu"].isna().sum() == 1


def test_parse_empty_source_returns_typed_empty_frame() -> None:
    raw = (
        '"market","idt","maturity_target","mu","sd","skew","kurt","p10","p50","p90",'
        '"lg_change_decr","prDec","lg_change_incr","prInc"\n'
    )
    df = parse_mpd_csv(raw)

    assert df.empty
    assert list(df.columns) == _CONTRACT_COLUMNS


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "market": ["sp12m", "sp12m"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-09"]),
            "maturity_months": [12.0, 12.0],
            "mu": [0.01, 0.02],
            "sd": [0.2, -0.1],  # negative sd is impossible -> quarantined
            "skew": [-1.0, -1.0],
            "kurt": [2.0, 2.0],
            "p10": [-0.2, -0.2],
            "p50": [0.05, 0.05],
            "p90": [0.2, 0.2],
            "prob_large_decline": [0.15, 0.15],
            "prob_large_increase": [0.1, 0.1],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 1
    assert len(quarantined) == 1
    assert quarantined["sd"].iloc[0] == pytest.approx(-0.1)


def test_validate_and_quarantine_all_valid_quarantines_nothing() -> None:
    df = pd.DataFrame(
        {
            "market": ["sp12m"],
            "obs_date": pd.to_datetime(["2026-01-02"]),
            "maturity_months": [12.0],
            "mu": [0.01],
            "sd": [0.2],
            "skew": [-1.0],
            "kurt": [2.0],
            "p10": [-0.2],
            "p50": [0.05],
            "p90": [0.2],
            "prob_large_decline": [0.15],
            "prob_large_increase": [0.1],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 1
    assert quarantined.empty


def test_same_date_across_two_markets_is_not_a_duplicate() -> None:
    """Uniqueness is on (market, obs_date), not obs_date alone -- otherwise
    ingesting the whole family together would quarantine one market on every
    shared observation date."""
    df = pd.DataFrame(
        {
            "market": ["sp12m", "bac"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "maturity_months": [12.0, 3.0],
            "mu": [0.01, -0.02],
            "sd": [0.2, 0.18],
            "skew": [-1.0, -0.4],
            "kurt": [2.0, 0.9],
            "p10": [-0.2, -0.25],
            "p50": [0.05, -0.01],
            "p90": [0.2, 0.19],
            "prob_large_decline": [0.15, 0.14],
            "prob_large_increase": [0.1, 0.09],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert quarantined.empty


def test_ingest_commits_the_whole_file_as_one_snapshot(
    tmp_path: Any, mpd_stats_sample: str
) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_mpd(store, ingest_date=ingest_date, raw=mpd_stats_sample)

    assert result.valid_rows == 7
    assert result.quarantined_rows == 0
    assert result.quarantine_path is None
    assert result.markets == ("bac", "infl1y", "sp12m")

    bronze = store.read_bronze_as_of("mpd", ingest_date)
    assert len(bronze) == 7
    assert set(bronze["market"]) == {"sp12m", "bac", "infl1y"}


def test_ingest_quarantines_bad_rows_without_losing_good_ones(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = (
        '"market","idt","maturity_target","mu","sd","skew","kurt","p10","p50","p90",'
        '"lg_change_decr","prDec","lg_change_incr","prInc"\n'
        '"sp12m","01/02/2026",12,0.01,0.2,-1.0,2.0,-0.2,0.05,0.2,-20,0.15,20,0.1\n'
        '"sp12m","01/09/2026",12,0.01,-0.2,-1.0,2.0,-0.2,0.05,0.2,-20,0.15,20,0.1\n'
    )
    ingest_date = dt.date(2026, 1, 10)
    result = ingest_mpd(store, ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 1
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None

    bronze = store.read_bronze_as_of("mpd", ingest_date)
    assert (bronze["sd"] < 0).sum() == 0

    quarantined = store.read_bronze_as_of("mpd__quarantine", ingest_date)
    assert len(quarantined) == 1


def test_ingest_all_valid_writes_no_quarantine_partition(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = (
        '"market","idt","maturity_target","mu","sd","skew","kurt","p10","p50","p90",'
        '"lg_change_decr","prDec","lg_change_incr","prInc"\n'
        '"sp12m","01/02/2026",12,0.01,0.2,-1.0,2.0,-0.2,0.05,0.2,-20,0.15,20,0.1\n'
    )
    ingest_date = dt.date(2026, 1, 10)
    result = ingest_mpd(store, ingest_date=ingest_date, raw=raw)

    assert result.quarantined_rows == 0
    assert result.quarantine_path is None
    with pytest.raises(LookupError):
        store.read_bronze_as_of("mpd__quarantine", ingest_date)


def test_ingest_logs_the_full_run_surface(tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    """docs/STANDARDS.md §f: an automated action without a runtime record is
    below the bar, so the run line is part of the contract, not decoration."""
    store = DeltaLakeStore(tmp_path)
    raw = (
        '"market","idt","maturity_target","mu","sd","skew","kurt","p10","p50","p90",'
        '"lg_change_decr","prDec","lg_change_incr","prInc"\n'
        '"sp12m","01/02/2026",12,0.01,0.2,-1.0,2.0,-0.2,0.05,0.2,-20,0.15,20,0.1\n'
    )
    with caplog.at_level("INFO", logger="tail_lab.ingestion.mpd"):
        ingest_mpd(store, ingest_date=dt.date(2026, 1, 10), raw=raw)

    line = "\n".join(caplog.messages)
    assert "event=ingest.mpd" in line
    assert "dataset=mpd" in line
    assert "source=minneapolisfed" in line
    assert "market_count=1" in line
    assert "valid_rows=1" in line
    assert "quarantined_rows=0" in line
