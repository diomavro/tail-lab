from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.cboe_strategy import (
    ingest_cboe_strategy,
    parse_index_history_csv,
    ticker_labels,
    validate_and_quarantine,
)
from tail_lab.ingestion.sources import SourceBehindMarket
from tail_lab.lake.store import DeltaLakeStore


def test_parse_against_real_cboe_fixture(cboe_pput_sample: str) -> None:
    df = parse_index_history_csv("PPUT", cboe_pput_sample)

    assert list(df.columns) == ["index_symbol", "trade_date", "close"]
    assert (df["index_symbol"] == "PPUT").all()
    assert df["trade_date"].is_monotonic_increasing
    assert df["trade_date"].is_unique
    assert df["close"].notna().all()
    # The index is rebased to 100 at its 1986-06-30 inception -- pinning this
    # exact pair proves the MM/DD/YYYY parse landed on the right day, which a
    # row count alone would not.
    first = df.iloc[0]
    assert first["trade_date"] == pd.Timestamp("1986-06-30")
    assert first["close"] == pytest.approx(100.0)


def test_parse_reads_us_date_format_not_day_first(cboe_pput_sample: str) -> None:
    """The load-bearing ambiguity in this source: Cboe writes MM/DD/YYYY, and
    an inferred parse would read 10/01/2008 as 10 January. A benchmark series
    silently shifted by months around a crash is worse than a loud failure,
    so the format is asserted, not assumed."""
    df = parse_index_history_csv("PPUT", cboe_pput_sample)
    oct_first = df.loc[df["trade_date"] == pd.Timestamp("2008-10-01")]

    assert len(oct_first) == 1
    assert oct_first["close"].iloc[0] == pytest.approx(369.19)
    # 2008-01-10 would be the day-first misreading of the same row.
    assert df.loc[df["trade_date"] == pd.Timestamp("2008-01-10")].empty


def test_parse_prefers_close_column_when_source_has_ohlc() -> None:
    """Some indices on the same CDN host serve DATE,OPEN,HIGH,LOW,CLOSE. The
    parser must take CLOSE, not the second column (which would be OPEN)."""
    raw = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2026,10.0,12.0,9.0,11.0\n"
    df = parse_index_history_csv("VIX", raw)

    assert len(df) == 1
    assert df["close"].iloc[0] == pytest.approx(11.0)


def test_parse_skips_preamble_above_the_header() -> None:
    raw = 'Cboe index history\n"disclaimer line"\n\nDATE,PPUT\n01/02/2026,101.5\n'
    df = parse_index_history_csv("PPUT", raw)

    assert len(df) == 1
    assert df["close"].iloc[0] == pytest.approx(101.5)


def test_parse_drops_blank_values_but_keeps_malformed_ones() -> None:
    """A blank cell is Cboe leaving a holiday empty -- "not a row". A garbled
    date or a non-numeric level is a *bad* row and must survive parsing so
    quarantine can see it; dropping both alike would hide feed corruption."""
    raw = "DATE,PPUT\n01/02/2026,101.5\n01/05/2026,\n99/99/2026,103.0\n01/07/2026,not-a-number\n"
    df = parse_index_history_csv("PPUT", raw)

    # The blank row is gone; the garbled date and the non-numeric level remain.
    assert len(df) == 3
    assert df["trade_date"].isna().sum() == 1
    assert df["close"].isna().sum() == 1


def test_parse_empty_source_returns_typed_empty_frame() -> None:
    df = parse_index_history_csv("PPUT", "DATE,PPUT\n")

    assert df.empty
    assert list(df.columns) == ["index_symbol", "trade_date", "close"]


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "index_symbol": ["PPUT", "PPUT", "PPUT"],
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
            "close": [101.5, -3.0, 102.0],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert len(quarantined) == 1
    assert quarantined["close"].iloc[0] == -3.0


def test_validate_and_quarantine_all_valid_quarantines_nothing() -> None:
    """The happy-path branch returns before the SchemaErrors split logic runs."""
    df = pd.DataFrame(
        {
            "index_symbol": ["PPUT", "PPUT"],
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
            "close": [101.5, 102.0],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert quarantined.empty


def test_same_date_across_two_indices_is_not_a_duplicate() -> None:
    """The family shares one dataset, so uniqueness is on (index_symbol,
    trade_date). If it were on trade_date alone, ingesting PPUT and SPX
    together would quarantine one of them on every shared trading day --
    i.e. the panel this dataset exists to build could never be written."""
    df = pd.DataFrame(
        {
            "index_symbol": ["PPUT", "SPX"],
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "close": [101.5, 5900.0],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert quarantined.empty


def test_ingest_commits_whole_family_as_one_snapshot(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {
        "PPUT": "DATE,PPUT\n01/02/2026,101.5\n01/05/2026,102.0\n",
        "SPX": "DATE,SPX\n01/02/2026,5900.0\n01/05/2026,5925.0\n",
    }
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_cboe_strategy(store, ["PPUT", "SPX"], ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 4
    assert result.quarantined_rows == 0
    assert result.quarantine_path is None
    assert result.tickers == ("PPUT", "SPX")

    bronze = store.read_bronze_as_of("cboe_strategy", ingest_date)
    assert len(bronze) == 4
    assert set(bronze["index_symbol"]) == {"PPUT", "SPX"}


def test_ingest_quarantines_bad_rows_without_losing_good_ones(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {"PPUT": "DATE,PPUT\n01/02/2026,101.5\n01/05/2026,-3.0\n01/06/2026,102.0\n"}
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_cboe_strategy(store, ["PPUT"], ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 2
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None

    bronze = store.read_bronze_as_of("cboe_strategy", ingest_date)
    assert -3.0 not in bronze["close"].tolist()

    quarantined = store.read_bronze_as_of("cboe_strategy__quarantine", ingest_date)
    assert len(quarantined) == 1
    assert quarantined["close"].iloc[0] == -3.0


def test_ingest_all_valid_writes_no_quarantine_partition(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {"PPUT": "DATE,PPUT\n01/02/2026,101.5\n"}
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_cboe_strategy(store, ["PPUT"], ingest_date=ingest_date, raw=raw)

    assert result.quarantined_rows == 0
    assert result.quarantine_path is None
    with pytest.raises(LookupError):
        store.read_bronze_as_of("cboe_strategy__quarantine", ingest_date)


def test_ingest_uppercases_requested_tickers(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {"PPUT": "DATE,PPUT\n01/02/2026,101.5\n"}
    result = ingest_cboe_strategy(store, ["pput"], ingest_date=dt.date(2026, 1, 6), raw=raw)

    assert result.tickers == ("PPUT",)
    assert result.valid_rows == 1


def test_ingest_logs_the_full_run_surface(tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    """docs/STANDARDS.md §f: an automated action without a runtime record is
    below the bar, so the run line is part of the contract, not decoration."""
    store = DeltaLakeStore(tmp_path)
    raw = {"PPUT": "DATE,PPUT\n01/02/2026,101.5\n01/05/2026,102.0\n"}
    with caplog.at_level("INFO", logger="tail_lab.ingestion.cboe_strategy"):
        ingest_cboe_strategy(store, ["PPUT"], ingest_date=dt.date(2026, 1, 6), raw=raw)

    line = "\n".join(caplog.messages)
    assert "event=ingest.cboe_strategy" in line
    assert "dataset=cboe_strategy" in line
    assert "source=cboe_cdn" in line
    assert "tickers=PPUT" in line
    assert "valid_rows=2" in line
    assert "quarantined_rows=0" in line
    assert "first_trade_date=2026-01-02" in line
    assert "last_trade_date=2026-01-05" in line


def test_ticker_labels_uppercases_and_dedupes_preserving_order() -> None:
    assert ticker_labels(["pput", "SPX", "PPUT", "vxth"]) == ["PPUT", "SPX", "VXTH"]


def test_one_index_behind_the_market_refuses_the_whole_family(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    raw = {
        "PPUT": "DATE,PPUT\n09/22/2026,101.5\n",
        "SPX": "DATE,SPX\n09/22/2026,5900.0\n09/23/2026,5925.0\n",
    }

    with pytest.raises(SourceBehindMarket, match=r"PPUT ends 2026-09-22"):
        ingest_cboe_strategy(
            store,
            ["PPUT", "SPX"],
            ingest_date=dt.date(2026, 9, 24),
            raw=raw,
            market_session=dt.date(2026, 9, 23),
        )
    assert not store.bronze_partition_exists("cboe_strategy", dt.date(2026, 9, 24))


def test_an_index_with_no_rows_counts_as_behind(tmp_path: Any) -> None:
    """A header-only CSV -- or one whose every row was quarantined -- is the
    half-broken feed this check exists for. Only looking at the names that
    DID arrive let it through and claimed the day's partition."""
    store = DeltaLakeStore(tmp_path)
    raw = {"PPUT": "DATE,PPUT\n", "SPX": "DATE,SPX\n09/22/2026,5900.0\n09/23/2026,5925.0\n"}

    with pytest.raises(SourceBehindMarket, match="PPUT has no rows"):
        ingest_cboe_strategy(
            store,
            ["PPUT", "SPX"],
            ingest_date=dt.date(2026, 9, 24),
            raw=raw,
            market_session=dt.date(2026, 9, 23),
        )
    assert not store.bronze_partition_exists("cboe_strategy", dt.date(2026, 9, 24))


def test_a_wholly_dead_feed_is_refused_not_written_empty(tmp_path: Any) -> None:
    """Every CSV answers with headers and no rows: the frame is empty, and an
    early "nothing to check" return let the run print "committed 0 rows" and
    exit 0 -- a dead feed reported as a healthy day. (No partition is claimed:
    an empty append creates none, so the harm is the silent success.)"""
    store = DeltaLakeStore(tmp_path)
    raw = {"PPUT": "DATE,PPUT\n", "SPX": "DATE,SPX\n"}

    with pytest.raises(SourceBehindMarket, match="PPUT has no rows, SPX has no rows"):
        ingest_cboe_strategy(
            store,
            ["PPUT", "SPX"],
            ingest_date=dt.date(2026, 9, 24),
            raw=raw,
            market_session=dt.date(2026, 9, 23),
        )
