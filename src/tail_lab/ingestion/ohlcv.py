"""Underlying OHLCV ingestion adapter — bronze layer (`docs/DATA_CONTRACTS.md` #1).

Source: Yahoo Finance's public chart JSON endpoint
(``https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>``). No API
key, no account, no login — a plain HTTPS GET returns JSON. `docs/DATA_CONTRACTS.md`
names Stooq's CSV export as the primary source, but Stooq now serves a
JavaScript proof-of-work anti-bot challenge to plain HTTP clients for
symbol downloads too (the same failure mode ``ingestion/vix.py`` already
hit and documents) — so this adapter uses Yahoo's chart endpoint, exactly
as the VIX adapter does, per README's named fallback.

Two functions, deliberately split so tests never touch the network:

- :func:`parse_yahoo_chart_ohlcv` — pure, parses the JSON shape into a
  DataFrame. Unit-tested against the committed fixture
  ``tests/fixtures/ohlcv_yahoo_sample.json``.
- :func:`fetch_ohlcv_raw` — makes the HTTP call. Exercised only by
  ``make ingest-ohlcv`` (a human/manually run target), never by CI/pytest.

:func:`ingest_ohlcv` orchestrates fetch -> parse -> validate/quarantine ->
commit-to-bronze, and is the function ``make ingest-ohlcv`` calls. Each
symbol is its own bronze dataset (``ohlcv_<symbol>``), the same shape
``ingest_vix`` uses for its single ``vix`` dataset, so multi-symbol
ingestion is just calling this function once per symbol — no new pattern.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.ohlcv import OhlcvSchema
from tail_lab.lake.store import LakeStore

YAHOO_CHART_URL_TEMPLATE = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: Path
    valid_rows: int
    quarantined_rows: int
    quarantine_path: Path | None


def dataset_id(symbol: str) -> str:
    """The bronze dataset id for ``symbol`` — one dataset per symbol."""
    return f"ohlcv_{symbol.lower()}"


def fetch_ohlcv_raw(
    symbol: str, *, range_: str = "2y", interval: str = "1d", timeout: float = 15.0
) -> Any:
    """Fetch raw Yahoo chart JSON for ``symbol``. Network call — not used by tests."""
    resp = requests.get(
        YAHOO_CHART_URL_TEMPLATE.format(symbol=symbol.upper()),
        params={"range": range_, "interval": interval},
        headers={"User-Agent": "Mozilla/5.0 (tail-lab ingestion bot)"},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload: Any = resp.json()
    return payload


def parse_yahoo_chart_ohlcv(symbol: str, raw: dict[str, Any]) -> pd.DataFrame:
    """Parse Yahoo's chart JSON payload into a typed OHLCV DataFrame.

    Pure function — no network, no filesystem. A bar is dropped (not
    quarantined) when any of open/high/low/close is null — Yahoo emits
    these for non-trading timestamps in the requested range, so a null bar
    is "not a row," not an invalid one; contract validation downstream
    still catches anything else wrong with a bar that did trade.
    """
    result = raw["chart"]["result"][0]
    timestamps: list[int] = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    opens: list[float | None] = quote["open"]
    highs: list[float | None] = quote["high"]
    lows: list[float | None] = quote["low"]
    closes: list[float | None] = quote["close"]
    volumes: list[int | None] = quote["volume"]
    adjcloses: list[float | None] = result["indicators"]["adjclose"][0]["adjclose"]
    gmtoffset = int(result["meta"].get("gmtoffset", 0))

    dates: list[dt.date] = []
    kept_open: list[float] = []
    kept_high: list[float] = []
    kept_low: list[float] = []
    kept_close: list[float] = []
    kept_volume: list[int] = []
    kept_adjclose: list[float] = []
    for ts, o, h, low_, c, v, adj in zip(
        timestamps, opens, highs, lows, closes, volumes, adjcloses, strict=True
    ):
        if o is None or h is None or low_ is None or c is None or v is None or adj is None:
            continue
        local_dt = dt.datetime.fromtimestamp(ts + gmtoffset, tz=dt.UTC)
        dates.append(local_dt.date())
        kept_open.append(float(o))
        kept_high.append(float(h))
        kept_low.append(float(low_))
        kept_close.append(float(c))
        kept_volume.append(int(v))
        kept_adjclose.append(float(adj))

    df = pd.DataFrame(
        {
            "symbol": symbol.upper(),
            "trade_date": pd.to_datetime(dates),
            "open": kept_open,
            "high": kept_high,
            "low": kept_low,
            "close": kept_close,
            "volume": kept_volume,
            "adj_close": kept_adjclose,
        }
    )
    return (
        df.sort_values("trade_date")
        .drop_duplicates(subset="trade_date", keep="last")
        .reset_index(drop=True)
    )


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the OHLCV contract.

    Bad rows are never silently dropped: they come back in the second
    frame so the caller can persist them for inspection.
    """
    try:
        valid = OhlcvSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = OhlcvSchema.validate(valid, lazy=True)
        return valid, quarantined


def ingest_ohlcv(
    store: LakeStore,
    symbol: str,
    *,
    ingest_date: dt.date | None = None,
    raw: dict[str, Any] | None = None,
) -> IngestResult:
    """Fetch (or use a supplied) raw payload, validate, and commit to bronze.

    ``raw`` lets callers (tests, or a future backfill script) inject a
    payload instead of hitting the network; ``fetch_ohlcv_raw`` is called
    only when ``raw`` is omitted.
    """
    ingest_date = ingest_date or dt.date.today()
    payload = raw if raw is not None else fetch_ohlcv_raw(symbol)
    parsed = parse_yahoo_chart_ohlcv(symbol, payload)
    valid, quarantined = validate_and_quarantine(parsed)

    bronze_path = store.write_bronze(dataset_id(symbol), ingest_date, valid)

    quarantine_path: Path | None = None
    if not quarantined.empty:
        quarantine_path = bronze_path.parent / "quarantine.parquet"
        quarantined.to_parquet(quarantine_path, index=False)

    return IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
    )
