from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from tail_lab.ingestion.ohlcv import (
    dataset_id,
    ingest_ohlcv,
    parse_yahoo_chart_ohlcv,
    validate_and_quarantine,
)
from tail_lab.lake.store import LocalParquetLakeStore


def _synthetic_raw(
    *,
    opens: list[float | None],
    highs: list[float | None],
    lows: list[float | None],
    closes: list[float | None],
    volumes: list[int | None],
    adjcloses: list[float | None],
    gmtoffset: int = 0,
) -> dict[str, Any]:
    n = len(closes)
    base_ts = 1767312000  # 2026-01-02T00:00:00Z
    timestamps = [base_ts + i * 86400 for i in range(n)]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": gmtoffset},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": opens,
                                "high": highs,
                                "low": lows,
                                "close": closes,
                                "volume": volumes,
                            }
                        ],
                        "adjclose": [{"adjclose": adjcloses}],
                    },
                }
            ]
        }
    }


def test_parse_yahoo_chart_ohlcv_against_fixture(ohlcv_yahoo_sample: dict[str, Any]) -> None:
    df = parse_yahoo_chart_ohlcv("AAPL", ohlcv_yahoo_sample)

    assert list(df.columns) == [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adj_close",
    ]
    assert len(df) > 0
    assert (df["symbol"] == "AAPL").all()
    # The fixture has one null bar injected — it must be dropped, not NaN.
    assert df["close"].notna().all()
    assert df["trade_date"].is_monotonic_increasing
    assert df["trade_date"].is_unique


def test_parse_yahoo_chart_ohlcv_drops_null_bars() -> None:
    raw = _synthetic_raw(
        opens=[100.0, None, 102.0],
        highs=[105.0, None, 106.0],
        lows=[99.0, None, 101.0],
        closes=[104.0, None, 105.0],
        volumes=[1_000, None, 1_200],
        adjcloses=[104.0, None, 105.0],
    )
    df = parse_yahoo_chart_ohlcv("aapl", raw)
    assert len(df) == 2
    assert df["close"].tolist() == [104.0, 105.0]
    # symbol is upper-cased regardless of the case passed in.
    assert (df["symbol"] == "AAPL").all()


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "AAPL"],
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
            "open": [100.0, -5.0, 102.0],
            "high": [105.0, 106.0, 106.0],
            "low": [99.0, 100.0, 101.0],
            "close": [104.0, 105.0, 105.0],
            "volume": [1_000, 1_100, 1_200],
            "adj_close": [104.0, 105.0, 105.0],
        }
    )
    valid, quarantined = validate_and_quarantine(df)
    assert len(valid) == 2
    assert len(quarantined) == 1
    assert quarantined["open"].iloc[0] == -5.0
    assert set(valid["open"]) == {100.0, 102.0}


def test_dataset_id_is_one_per_symbol() -> None:
    assert dataset_id("AAPL") == "ohlcv_aapl"
    assert dataset_id("msft") == "ohlcv_msft"


def test_ingest_ohlcv_commits_bronze_and_quarantines_bad_rows(tmp_path: Any) -> None:
    store = LocalParquetLakeStore(tmp_path)
    raw = _synthetic_raw(
        opens=[100.0, -5.0, 102.0],
        highs=[105.0, 106.0, 106.0],
        lows=[99.0, 100.0, 101.0],
        closes=[104.0, 105.0, 105.0],
        volumes=[1_000, 1_100, 1_200],
        adjcloses=[104.0, 105.0, 105.0],
    )
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_ohlcv(store, "AAPL", ingest_date=ingest_date, raw=raw)

    assert result.valid_rows == 2
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None
    assert result.quarantine_path.exists()

    bronze = store.read_bronze_as_of("ohlcv_aapl", ingest_date)
    assert len(bronze) == 2
    assert -5.0 not in bronze["open"].tolist()


def test_ingest_ohlcv_keeps_symbols_in_separate_datasets(tmp_path: Any) -> None:
    store = LocalParquetLakeStore(tmp_path)
    raw = _synthetic_raw(
        opens=[100.0],
        highs=[105.0],
        lows=[99.0],
        closes=[104.0],
        volumes=[1_000],
        adjcloses=[104.0],
    )
    ingest_date = dt.date(2026, 1, 6)
    ingest_ohlcv(store, "AAPL", ingest_date=ingest_date, raw=raw)
    ingest_ohlcv(store, "MSFT", ingest_date=ingest_date, raw=raw)

    aapl = store.read_bronze_as_of("ohlcv_aapl", ingest_date)
    msft = store.read_bronze_as_of("ohlcv_msft", ingest_date)
    assert aapl["symbol"].tolist() == ["AAPL"]
    assert msft["symbol"].tolist() == ["MSFT"]
