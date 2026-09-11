"""Vix-complex ingestion adapter — bronze layer (`docs/DATA_CONTRACTS.md` #2).

Source: Cboe's public index CSVs,
``https://cdn.cboe.com/api/global/us_indices/daily_prices/{TICKER}_History.csv``
— the same keyless CDN host and URL family ``ingestion/vix.py`` and
``ingestion/cboe_strategy.py`` already use. No API key, no account, no login.

This fills in the rest of dataset #2's documented family — VIX3M, VIX9D,
VVIX and SKEW — which `ingestion/vix.py` never fetched even though all four
were confirmed reachable on the same host. SKEW in particular is the hard
blocker on the skew-aware pricer queued in `AGENT_TODO.md`: nothing in the
lake can price a smile without it.

Confirmed live 2026-09-07: VIX3M and VIX9D serve full
``DATE,OPEN,HIGH,LOW,CLOSE`` (like spot VIX), but VVIX and SKEW serve a bare
``DATE,<TICKER>`` (like the strategy indices in ``cboe_strategy.py``) — so
this adapter's parser has to tolerate both shapes, unlike either of those
two modules individually.

Two functions, split so tests never touch the network:

- :func:`parse_vix_complex_csv` — pure, parses one series' CSV text.
  Unit-tested against real fixtures for both shapes.
- :func:`fetch_vix_complex_raw` — the HTTP call. Exercised only by
  ``make ingest-vix-complex`` (human/manually run), never by CI/pytest.

:func:`ingest_vix_complex` orchestrates fetch -> parse -> validate/quarantine
-> commit-to-bronze across the whole family, mirroring
``ingest_cboe_strategy``: the family lands in one immutable bronze partition
per run, since it's fetched together and read together as one panel.

**Deliberately excludes spot VIX itself** — see `contracts/vix_complex.py`'s
module docstring for why merging the two datasets is a separate increment.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.vix_complex import (
    DATASET,
    SERIES_NAMES,
    VixComplexSchema,
    empty_vix_complex_frame,
)
from tail_lab.ingestion.sources import find_header_line
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "QUARANTINE_DATASET",
    "IngestResult",
    "fetch_vix_complex_raw",
    "ingest_vix_complex",
    "parse_vix_complex_csv",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

INDEX_HISTORY_URL_TEMPLATE = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/{ticker}_History.csv"
)
QUARANTINE_DATASET = f"{DATASET}__quarantine"

#: Cboe writes US-style dates across this whole CDN family. Parsed with an
#: explicit format on purpose (matches ``vix.py``/``cboe_strategy.py``) — an
#: inferred parse reads 06/07/2026 as 7 June in one file and 6 July in
#: another, and a vol series silently shifted by a month is worse than one
#: that fails loudly.
_DATE_FORMAT = "%m/%d/%Y"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    series: tuple[str, ...]


def fetch_vix_complex_raw(series: str, *, timeout: float = 30.0) -> str:
    """Fetch one series' full history CSV text. Network call — not used by tests."""
    resp = requests.get(
        INDEX_HISTORY_URL_TEMPLATE.format(ticker=series.upper()),
        headers={"User-Agent": "Mozilla/5.0 (tail-lab ingestion bot)"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def parse_vix_complex_csv(series: str, raw: str) -> pd.DataFrame:
    """Parse one series' Cboe history CSV into a typed frame.

    Pure function — no network, no filesystem. Tolerates both shapes this
    family serves: VIX3M/VIX9D's full ``DATE,OPEN,HIGH,LOW,CLOSE`` and
    VVIX/SKEW's bare ``DATE,<TICKER>`` — when only one value column is
    present, ``open``/``high``/``low`` come back null rather than a copy of
    ``close``, since the source never claimed those levels.

    Rows with a blank close are dropped as "not a row" (Cboe leaves
    holidays empty); anything else malformed survives as NaT/NaN for
    :func:`validate_and_quarantine` to catch, so bad data is never silently
    discarded.
    """
    header_offset = find_header_line(raw)
    frame = pd.read_csv(io.StringIO(raw), skiprows=header_offset)
    if frame.empty:
        return empty_vix_complex_frame()

    columns = {str(c).strip().upper(): c for c in frame.columns}
    date_col = columns.get("DATE", frame.columns[0])
    has_ohlc = "CLOSE" in columns
    if has_ohlc:
        close_col = columns["CLOSE"]
    else:
        remaining = [c for c in frame.columns if c != date_col]
        if not remaining:
            return empty_vix_complex_frame()
        close_col = remaining[0]

    missing = pd.Series([float("nan")] * len(frame))
    df = pd.DataFrame(
        {
            "series": series.upper(),
            "trade_date": pd.to_datetime(frame[date_col], format=_DATE_FORMAT, errors="coerce"),
            "open": pd.to_numeric(frame[columns["OPEN"]], errors="coerce") if has_ohlc else missing,
            "high": pd.to_numeric(frame[columns["HIGH"]], errors="coerce") if has_ohlc else missing,
            "low": pd.to_numeric(frame[columns["LOW"]], errors="coerce") if has_ohlc else missing,
            "close": pd.to_numeric(frame[close_col], errors="coerce"),
        }
    )
    df = df.loc[frame[close_col].notna()]
    return (
        df.sort_values("trade_date")
        .drop_duplicates(subset="trade_date", keep="last")
        .reset_index(drop=True)
    )


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the vix-complex contract.

    Bad rows are never silently dropped: they come back in the second frame
    so the caller can persist them for inspection.
    """
    try:
        valid = VixComplexSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = VixComplexSchema.validate(valid, lazy=True)
        return valid, quarantined


def ingest_vix_complex(
    store: LakeStore,
    series: Sequence[str] | None = None,
    *,
    ingest_date: dt.date | None = None,
    raw: Mapping[str, str] | None = None,
) -> IngestResult:
    """Fetch (or use supplied) series CSVs, validate, and commit one bronze snapshot.

    ``raw`` maps series -> CSV text, letting callers (tests, or a future
    backfill script) inject payloads instead of hitting the network;
    :func:`fetch_vix_complex_raw` is called only for series absent from it.

    The whole family is committed as a single immutable bronze partition: a
    partial run must not half-overwrite the previous complete panel.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    resolved = tuple(s.upper() for s in (series if series is not None else SERIES_NAMES))
    # `resolved` is always uppercase, so `raw`'s keys must match — a caller
    # passing lowercase keys would otherwise silently miss the injected
    # payload and fall through to a live network call (design review, PR #87).
    raw_upper = {k.upper(): v for k, v in raw.items()} if raw is not None else None

    parsed = [
        parse_vix_complex_csv(
            one_series,
            raw_upper[one_series]
            if raw_upper is not None and one_series in raw_upper
            else fetch_vix_complex_raw(one_series),
        )
        for one_series in resolved
    ]
    combined = pd.concat(parsed, ignore_index=True) if parsed else empty_vix_complex_frame()
    valid, quarantined = validate_and_quarantine(combined)

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    # Quarantined rows go through the store as an immutable bronze snapshot
    # under a sibling dataset name — never a raw filesystem write (matches
    # vix.py/cboe_strategy.py; the lake is the only thing allowed to touch
    # storage).
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        series=resolved,
    )
    log_event(
        _LOGGER,
        "ingest.vix_complex",
        dataset=DATASET,
        source="cboe_cdn",
        ingest_date=ingest_date.isoformat(),
        series=",".join(result.series),
        series_count=len(result.series),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
    )
    return result
