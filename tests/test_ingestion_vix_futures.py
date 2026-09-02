from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from tail_lab.ingestion.vix_futures import (
    MODERN_URL_START,
    compute_vx_expiry,
    default_expiries,
    ingest_vix_futures,
    parse_vx_futures_csv,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore

# Four real contracts fetched live from Cboe's CDN on 2026-09-02 (HTTP 200),
# spanning the full width of the modern URL pattern -- pins the expiry rule
# against actual settlement dates, not just the formula that produced them.
_VERIFIED_LIVE_EXPIRIES = {
    (2013, 1): dt.date(2013, 1, 16),  # the earliest date the modern URL serves
    (2020, 3): dt.date(2020, 3, 18),
    (2026, 9): dt.date(2026, 9, 16),
    (2026, 10): dt.date(2026, 10, 21),
}


@pytest.mark.parametrize(
    ("year", "month", "expected"), [(*k, v) for k, v in _VERIFIED_LIVE_EXPIRIES.items()]
)
def test_compute_vx_expiry_matches_real_contracts(year: int, month: int, expected: dt.date) -> None:
    assert compute_vx_expiry(year, month) == expected


def test_compute_vx_expiry_rolls_december_into_next_year() -> None:
    """The December contract settles off the following January's third
    Friday -- the one month where "next month" also means "next year"."""
    assert compute_vx_expiry(2026, 12) == dt.date(2026, 12, 16)


def test_modern_url_start_matches_the_verified_cutover() -> None:
    assert dt.date(2013, 1, 16) == MODERN_URL_START


def test_default_expiries_walks_forward_from_as_of() -> None:
    got = default_expiries(dt.date(2026, 9, 2), n_months=4)
    assert got == [
        dt.date(2026, 9, 16),
        dt.date(2026, 10, 21),
        dt.date(2026, 11, 18),
        dt.date(2026, 12, 16),
    ]


def test_default_expiries_skips_an_already_expired_current_month() -> None:
    """Asking on the day after a contract expired must not return a dead
    contract as one of the "current" months."""
    got = default_expiries(dt.date(2026, 9, 17), n_months=1)
    assert got == [dt.date(2026, 10, 21)]


def test_default_expiries_includes_expiry_day_itself() -> None:
    got = default_expiries(dt.date(2026, 9, 16), n_months=1)
    assert got == [dt.date(2026, 9, 16)]


def test_parse_against_real_cboe_fixture(vix_futures_h2020_sample: str) -> None:
    expiry = dt.date(2020, 3, 18)
    df = parse_vx_futures_csv(expiry, vix_futures_h2020_sample)

    assert list(df.columns) == [
        "contract_expiry",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "settle",
        "volume",
        "open_interest",
    ]
    assert (df["contract_expiry"] == pd.Timestamp(expiry)).all()
    assert df["trade_date"].is_monotonic_increasing
    assert df["trade_date"].is_unique
    assert df["settle"].notna().all()

    last = df.iloc[-1]
    assert last["trade_date"] == pd.Timestamp("2020-03-18")
    assert last["settle"] == pytest.approx(69.76)
    assert last["open"] == pytest.approx(70.55)


def test_parse_maps_zero_fill_ohlc_to_null_but_keeps_real_settle(
    vix_futures_h2020_sample: str,
) -> None:
    """2019-06-24 in the fixture is a genuine no-trade day: OHLC all zero,
    settle a real, non-zero exchange-computed price."""
    df = parse_vx_futures_csv(dt.date(2020, 3, 18), vix_futures_h2020_sample)
    no_trade_day = df.loc[df["trade_date"] == pd.Timestamp("2019-06-24")].iloc[0]

    assert pd.isna(no_trade_day["open"])
    assert pd.isna(no_trade_day["high"])
    assert pd.isna(no_trade_day["low"])
    assert pd.isna(no_trade_day["close"])
    assert no_trade_day["settle"] == pytest.approx(17.7)


def test_parse_a_partial_zero_fill_nulls_only_the_zero_fields(
    vix_futures_h2020_sample: str,
) -> None:
    """2019-06-25: Open/Close are still zero-filled (no print at either) but
    High/Low carry real intraday prints -- only the zero cells should turn
    into null, not the whole row."""
    df = parse_vx_futures_csv(dt.date(2020, 3, 18), vix_futures_h2020_sample)
    row = df.loc[df["trade_date"] == pd.Timestamp("2019-06-25")].iloc[0]

    assert pd.isna(row["open"])
    assert pd.isna(row["close"])
    assert row["high"] == pytest.approx(17.3)
    assert row["low"] == pytest.approx(18.85)


def test_parse_empty_source_returns_typed_empty_frame() -> None:
    header = "Trade Date,Futures,Open,High,Low,Close,Settle,Change,Total Volume,EFP,Open Interest\n"
    df = parse_vx_futures_csv(dt.date(2020, 3, 18), header)

    assert df.empty
    assert list(df.columns) == [
        "contract_expiry",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "settle",
        "volume",
        "open_interest",
    ]


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "contract_expiry": pd.to_datetime(["2020-03-18"] * 3),
            "trade_date": pd.to_datetime(["2020-03-16", "2020-03-17", "2020-03-18"]),
            "open": [54.0, 72.5, 70.55],
            "high": [76.25, 79.05, 82.0],
            "low": [54.0, 64.9, 70.25],
            "close": [72.05, 70.55, 81.95],
            "settle": [72.625, -1.0, 69.76],
            "volume": [73205, 72592, 902],
            "open_interest": [75724, 65191, 61878],
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert len(quarantined) == 1
    assert quarantined["settle"].iloc[0] == -1.0


def test_ingest_commits_the_whole_curve_as_one_snapshot(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    header = "Trade Date,Futures,Open,High,Low,Close,Settle,Change,Total Volume,EFP,Open Interest\n"
    raw = {
        dt.date(2026, 9, 16): header
        + "2026-09-01,U (Sep 2026),18.4,18.95,18.37,18.81,18.85,0,71864,0,124065\n",
        dt.date(2026, 10, 21): header
        + "2026-09-01,V (Oct 2026),19.0,19.5,18.9,19.2,19.25,0,40000,0,90000\n",
    }
    ingest_date = dt.date(2026, 9, 2)
    result = ingest_vix_futures(
        store, [dt.date(2026, 9, 16), dt.date(2026, 10, 21)], ingest_date=ingest_date, raw=raw
    )

    assert result.valid_rows == 2
    assert result.quarantined_rows == 0
    assert result.expiries == (dt.date(2026, 9, 16), dt.date(2026, 10, 21))

    bronze = store.read_bronze_as_of("vix_futures", ingest_date)
    assert len(bronze) == 2
    assert set(pd.to_datetime(bronze["contract_expiry"]).dt.date) == {
        dt.date(2026, 9, 16),
        dt.date(2026, 10, 21),
    }


def test_ingest_uses_default_expiries_when_none_given(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    header = "Trade Date,Futures,Open,High,Low,Close,Settle,Change,Total Volume,EFP,Open Interest\n"
    ingest_date = dt.date(2026, 9, 2)
    expected = default_expiries(ingest_date)
    raw = {
        e: header + f"{ingest_date.isoformat()},X,18.4,18.95,18.37,18.81,18.85,0,1000,0,2000\n"
        for e in expected
    }

    result = ingest_vix_futures(store, ingest_date=ingest_date, raw=raw)

    assert result.expiries == tuple(expected)
    assert result.valid_rows == len(expected)


def test_ingest_quarantines_bad_rows_without_losing_good_ones(tmp_path: Any) -> None:
    store = DeltaLakeStore(tmp_path)
    header = "Trade Date,Futures,Open,High,Low,Close,Settle,Change,Total Volume,EFP,Open Interest\n"
    body = (
        "2026-08-31,U (Sep 2026),18.75,19.05,18.4,18.42,18.44,0,45999,0,110883\n"
        "2026-09-01,U (Sep 2026),18.42,18.95,18.37,18.81,-1.0,0,71864,0,124065\n"
    )
    ingest_date = dt.date(2026, 9, 2)
    result = ingest_vix_futures(
        store,
        [dt.date(2026, 9, 16)],
        ingest_date=ingest_date,
        raw={dt.date(2026, 9, 16): header + body},
    )

    assert result.valid_rows == 1
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None

    quarantined = store.read_bronze_as_of("vix_futures__quarantine", ingest_date)
    assert quarantined["settle"].iloc[0] == -1.0


def test_ingest_logs_the_full_run_surface(tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    """docs/STANDARDS.md §f: an automated action without a runtime record is
    below the bar, so the run line is part of the contract, not decoration."""
    store = DeltaLakeStore(tmp_path)
    header = "Trade Date,Futures,Open,High,Low,Close,Settle,Change,Total Volume,EFP,Open Interest\n"
    raw = {
        dt.date(2026, 9, 16): header
        + "2026-09-01,U (Sep 2026),18.4,18.95,18.37,18.81,18.85,0,71864,0,124065\n"
    }
    with caplog.at_level("INFO", logger="tail_lab.ingestion.vix_futures"):
        ingest_vix_futures(store, [dt.date(2026, 9, 16)], ingest_date=dt.date(2026, 9, 2), raw=raw)

    line = "\n".join(caplog.messages)
    assert "event=ingest.vix_futures" in line
    assert "dataset=vix_futures" in line
    assert "source=cboe_cdn" in line
    assert "contract_count=1" in line
    assert "nearest_expiry=2026-09-16" in line
    assert "farthest_expiry=2026-09-16" in line
    assert "valid_rows=1" in line
    assert "quarantined_rows=0" in line
