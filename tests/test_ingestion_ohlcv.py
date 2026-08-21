from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.ohlcv import (
    dataset_id,
    ingest_ohlcv,
    parse_nasdaq_historical,
    parse_yahoo_chart_ohlcv,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore


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
    store = DeltaLakeStore(tmp_path)
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

    bronze = store.read_bronze_as_of("ohlcv_aapl", ingest_date)
    assert len(bronze) == 2
    assert -5.0 not in bronze["open"].tolist()

    # Quarantined rows are committed through the store (backend-agnostic —
    # never a raw filesystem write), readable back through the same abstraction.
    quarantined = store.read_bronze_as_of("ohlcv_aapl__quarantine", ingest_date)
    assert len(quarantined) == 1
    assert -5.0 in quarantined["open"].tolist()


def test_ingest_ohlcv_keeps_symbols_in_separate_datasets(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
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


# --- Nasdaq primary source (2026-08-21: Yahoo demoted to fallback) -------


def test_parse_nasdaq_against_real_fixture(nasdaq_ohlcv_sample: dict[str, Any]) -> None:
    df = parse_nasdaq_historical("SPY", nasdaq_ohlcv_sample)

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
    assert (df["symbol"] == "SPY").all()
    assert df["trade_date"].is_monotonic_increasing
    assert df["trade_date"].is_unique
    assert (df["volume"] > 0).all()
    # Pinned against the real row: 08/20/2026 close 762.60, volume 45,520,300.
    row = df.loc[df["trade_date"] == pd.Timestamp("2026-08-20")]
    assert len(row) == 1
    assert row["close"].iloc[0] == pytest.approx(762.60)
    assert int(row["volume"].iloc[0]) == 45_520_300


def test_parse_nasdaq_strips_dollar_signs_and_separators() -> None:
    """Nasdaq renders stock prices as ``$345.13`` and volume as
    ``30,766,360``. Feeding those to float() raises, so the stripping is
    load-bearing, not cosmetic."""
    raw = {
        "data": {
            "tradesTable": {
                "rows": [
                    {
                        "date": "08/20/2026",
                        "close": "$345.13",
                        "volume": "30,766,360",
                        "open": "$346.20",
                        "high": "$347.50",
                        "low": "$338.96",
                    }
                ]
            }
        }
    }
    df = parse_nasdaq_historical("TSLA", raw)

    assert len(df) == 1
    assert df["close"].iloc[0] == pytest.approx(345.13)
    assert df["open"].iloc[0] == pytest.approx(346.20)
    assert int(df["volume"].iloc[0]) == 30_766_360


def test_parse_nasdaq_sets_adj_close_equal_to_close() -> None:
    """Nasdaq gives no dividend-adjusted series. Setting adj_close = close is
    a deliberate, documented limitation (splits only); inventing a dividend
    adjustment would be a lie in the data. The basis is recorded on the
    IngestResult so a consumer can tell the two apart."""
    raw = {
        "data": {
            "tradesTable": {
                "rows": [
                    {
                        "date": "08/20/2026",
                        "close": "762.60",
                        "volume": "1",
                        "open": "760",
                        "high": "765",
                        "low": "759",
                    }
                ]
            }
        }
    }
    df = parse_nasdaq_historical("SPY", raw)
    assert df["adj_close"].iloc[0] == df["close"].iloc[0]


def test_parse_nasdaq_drops_rows_missing_a_field() -> None:
    raw = {
        "data": {
            "tradesTable": {
                "rows": [
                    {
                        "date": "08/20/2026",
                        "close": "762.60",
                        "volume": "1",
                        "open": "760",
                        "high": "765",
                        "low": "759",
                    },
                    {
                        "date": "08/19/2026",
                        "close": "--",
                        "volume": "1",
                        "open": "760",
                        "high": "765",
                        "low": "759",
                    },
                ]
            }
        }
    }
    df = parse_nasdaq_historical("SPY", raw)
    assert len(df) == 1


def test_parse_nasdaq_empty_payload_returns_typed_empty_frame() -> None:
    df = parse_nasdaq_historical("SPY", {"data": {"tradesTable": {"rows": []}}})
    assert df.empty
    assert "adj_close" in df.columns


def test_ingest_prefers_nasdaq_and_records_source_and_basis(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    nasdaq_raw = {
        "data": {
            "tradesTable": {
                "rows": [
                    {
                        "date": "01/02/2026",
                        "close": "100.0",
                        "volume": "5",
                        "open": "99",
                        "high": "101",
                        "low": "98",
                    }
                ]
            }
        }
    }
    result = ingest_ohlcv(store, "SPY", ingest_date=dt.date(2026, 1, 6), nasdaq_raw=nasdaq_raw)

    assert result.source_id == "nasdaq"
    assert result.adjustment_basis == "splits_only"
    assert result.valid_rows == 1


def test_ingest_falls_back_to_yahoo_and_flags_the_richer_basis(tmp_path: Any) -> None:
    """When Nasdaq yields nothing the chain must degrade to Yahoo -- and the
    recorded adjustment basis must change with it, because the two are not
    on the same footing."""
    store = DeltaLakeStore(tmp_path)
    yahoo_raw = {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": 0},
                    "timestamp": [1767312000],
                    "indicators": {
                        "quote": [
                            {
                                "open": [99.0],
                                "high": [101.0],
                                "low": [98.0],
                                "close": [100.0],
                                "volume": [5],
                            }
                        ],
                        "adjclose": [{"adjclose": [97.5]}],
                    },
                }
            ]
        }
    }
    result = ingest_ohlcv(
        store,
        "SPY",
        ingest_date=dt.date(2026, 1, 6),
        nasdaq_raw={"data": {"tradesTable": {"rows": []}}},
        raw=yahoo_raw,
    )

    assert result.source_id == "yahoo"
    assert result.adjustment_basis == "splits_and_dividends"
    bronze = store.read_bronze_as_of("ohlcv_spy", dt.date(2026, 1, 6))
    assert bronze["adj_close"].iloc[0] == pytest.approx(97.5)


def test_injecting_one_source_never_reaches_the_network_for_the_other(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard. The first cut of the source chain let a caller who
    injected only the Yahoo payload still perform a live Nasdaq fetch --
    breaking docs/STANDARDS.md's "tests never touch the network" silently.
    Injection must be hermetic."""
    import tail_lab.ingestion.ohlcv as mod

    def _explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("network fetch attempted during an injected run")

    monkeypatch.setattr(mod, "fetch_nasdaq_raw", _explode)
    monkeypatch.setattr(mod, "fetch_ohlcv_raw", _explode)

    store = DeltaLakeStore(tmp_path)
    yahoo_raw = {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": 0},
                    "timestamp": [1767312000],
                    "indicators": {
                        "quote": [
                            {
                                "open": [99.0],
                                "high": [101.0],
                                "low": [98.0],
                                "close": [100.0],
                                "volume": [5],
                            }
                        ],
                        "adjclose": [{"adjclose": [97.5]}],
                    },
                }
            ]
        }
    }
    result = ingest_ohlcv(store, "SPY", ingest_date=dt.date(2026, 1, 6), raw=yahoo_raw)
    assert result.source_id == "yahoo"
