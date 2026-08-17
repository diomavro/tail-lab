"""VIX ingestion adapter — bronze layer.

Source: Yahoo Finance's public chart JSON endpoint for ``^VIX``
(``https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX``). No API key,
no account, no login — a plain HTTPS GET returns JSON. (stooq's CSV export,
the other keyless option named in the brief, now serves a JavaScript
proof-of-work anti-bot challenge instead of CSV to plain HTTP clients — see
README deviation note — so this adapter uses Yahoo's chart endpoint instead.)

Two functions, deliberately split so tests never touch the network:

- :func:`parse_yahoo_chart` — pure, parses the JSON shape into a DataFrame.
  Unit-tested against the committed fixture ``tests/fixtures/vix_yahoo_sample.json``.
- :func:`fetch_vix_raw` — makes the HTTP call. Exercised only by
  ``make ingest-vix`` (a human/manually run target), never by CI/pytest.

:func:`ingest_vix` orchestrates fetch -> parse -> validate/quarantine ->
commit-to-bronze, and is the function ``make ingest-vix`` calls.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.vix import VixSchema
from tail_lab.lake.store import LakeStore

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
DATASET = "vix"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: Path
    valid_rows: int
    quarantined_rows: int
    quarantine_path: Path | None


def fetch_vix_raw(*, range_: str = "6mo", interval: str = "1d", timeout: float = 15.0) -> Any:
    """Fetch raw Yahoo chart JSON for ^VIX. Network call — not used by tests."""
    resp = requests.get(
        YAHOO_CHART_URL,
        params={"range": range_, "interval": interval},
        headers={"User-Agent": "Mozilla/5.0 (tail-lab ingestion bot)"},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload: Any = resp.json()
    return payload


def parse_yahoo_chart(raw: dict[str, Any]) -> pd.DataFrame:
    """Parse Yahoo's chart JSON payload into a typed (date, close) DataFrame.

    Pure function — no network, no filesystem. Rows with a null close (Yahoo
    emits these for non-trading timestamps in the requested range) are
    dropped here as "not a row" rather than "an invalid row"; contract
    validation in :func:`validate_and_quarantine` still runs downstream to
    catch anything else in range.
    """
    result = raw["chart"]["result"][0]
    timestamps: list[int] = result["timestamp"]
    closes: list[float | None] = result["indicators"]["quote"][0]["close"]
    gmtoffset = int(result["meta"].get("gmtoffset", 0))

    dates: list[dt.date] = []
    kept_closes: list[float] = []
    for ts, close in zip(timestamps, closes, strict=True):
        if close is None:
            continue
        local_dt = dt.datetime.fromtimestamp(ts + gmtoffset, tz=dt.UTC)
        dates.append(local_dt.date())
        kept_closes.append(float(close))

    df = pd.DataFrame({"date": pd.to_datetime(dates), "close": kept_closes})
    return df.sort_values("date").drop_duplicates(subset="date", keep="last").reset_index(drop=True)


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the VIX contract.

    Bad rows are never silently dropped: they come back in the second
    frame so the caller can persist them for inspection.
    """
    try:
        valid = VixSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = VixSchema.validate(valid, lazy=True)
        return valid, quarantined


def ingest_vix(
    store: LakeStore,
    *,
    ingest_date: dt.date | None = None,
    raw: dict[str, Any] | None = None,
) -> IngestResult:
    """Fetch (or use a supplied) raw payload, validate, and commit to bronze.

    ``raw`` lets callers (tests, or a future backfill script) inject a
    payload instead of hitting the network; ``fetch_vix_raw`` is called only
    when ``raw`` is omitted.
    """
    ingest_date = ingest_date or dt.date.today()
    payload = raw if raw is not None else fetch_vix_raw()
    parsed = parse_yahoo_chart(payload)
    valid, quarantined = validate_and_quarantine(parsed)

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

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
