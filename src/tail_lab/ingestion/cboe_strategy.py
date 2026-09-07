"""Cboe option-strategy benchmark index ingestion adapter — bronze layer
(`docs/DATA_CONTRACTS.md` #7, `docs/DATA_SOURCING.md` §9.1).

Source: Cboe's public index CSVs,
``https://cdn.cboe.com/api/global/us_indices/daily_prices/{TICKER}_History.csv``
— the same keyless CDN host and URL family ``ingestion/vix.py`` already
uses for the vol complex. No API key, no account, no login.

Why this dataset exists: the backtester prices options with a
Black-Scholes proxy (`docs/adr/0004`), and the S1 thesis is precisely that
the market *misprices* tails — which a model-priced backtest cannot see.
Buying real historical chains to close that gap was scoped at $99/mo to $1,495
(`docs/DATA_SOURCING.md` §3). These indices close it for free: Cboe's
published methodology prices every roll at the volume-weighted average of
actual OPRA *transaction* prices, so PPUT/PPUT3M/VXTH are the realised P&L
of real, executed put programs — back to 1986, covering every tail event
the platform cares about (2008, 2011, 2015, 2018, 2020, 2022).

Three functions, deliberately split so tests never touch the network:

- :func:`parse_index_history_csv` — pure, parses one ticker's CSV text into
  a DataFrame. Unit-tested against the committed fixture
  ``tests/fixtures/cboe_pput_sample.csv``.
- :func:`fetch_index_history_raw` — makes the HTTP call. Exercised only by
  ``make ingest-cboe-strategy`` (human/manually run), never by CI/pytest.
- :func:`ingest_cboe_strategy` — orchestrates fetch -> parse -> validate/
  quarantine -> commit-to-bronze across the whole ticker family, and is the
  function ``make ingest-cboe-strategy`` calls.

Unlike ``ingestion/ohlcv.py`` (one bronze dataset per symbol), the whole
family lands in a single long-format ``cboe_strategy`` dataset keyed by
``(index_symbol, trade_date)`` — they are fetched together, read together
as a benchmark panel, and share an identical source format, so one
immutable snapshot per run is the honest unit of ingestion here.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.cboe_strategy import (
    DATASET,
    DEFAULT_TICKERS,
    CboeStrategySchema,
)
from tail_lab.ingestion.sources import find_header_line
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "QUARANTINE_DATASET",
    "IngestResult",
    "fetch_index_history_raw",
    "ingest_cboe_strategy",
    "parse_index_history_csv",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

INDEX_HISTORY_URL_TEMPLATE = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/{ticker}_History.csv"
)
QUARANTINE_DATASET = f"{DATASET}__quarantine"

#: Cboe serves these files with US-style ``MM/DD/YYYY`` dates. Parsing with
#: an explicit format (rather than letting pandas infer) is the point: an
#: inferred parse would silently read 06/07/1986 as 7 June in one file and
#: 6 July in another, and a benchmark series that is silently off by a month
#: around a crash is worse than one that fails loudly.
_DATE_FORMAT = "%m/%d/%Y"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    tickers: tuple[str, ...]


def fetch_index_history_raw(ticker: str, *, timeout: float = 30.0) -> str:
    """Fetch one index's full history CSV text. Network call — not used by tests."""
    resp = requests.get(
        INDEX_HISTORY_URL_TEMPLATE.format(ticker=ticker.upper()),
        headers={"User-Agent": "Mozilla/5.0 (tail-lab ingestion bot)"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def parse_index_history_csv(ticker: str, raw: str) -> pd.DataFrame:
    """Parse one Cboe index history CSV into a typed (index_symbol, trade_date, close) frame.

    Pure function — no network, no filesystem.

    Two shapes have to be tolerated. Most strategy indices serve exactly
    ``DATE,<TICKER>``; some of the vol-complex files on the same host serve
    ``DATE,OPEN,HIGH,LOW,CLOSE``. A ``CLOSE`` column is therefore preferred
    when present, falling back to the second column. Some files also carry
    a short preamble above the header, so the header row is located rather
    than assumed to be line 1.

    Rows whose value is blank are dropped here as "not a row" (Cboe leaves
    holidays empty in some files) rather than quarantined; anything else
    malformed — an unparseable date, a non-numeric level — survives as
    NaT/NaN and is caught by :func:`validate_and_quarantine`, so bad data is
    never silently discarded.
    """
    header_offset = find_header_line(raw)
    frame = pd.read_csv(io.StringIO(raw), skiprows=header_offset)
    if frame.empty:
        return _empty_frame()

    columns = {str(c).strip().upper(): c for c in frame.columns}
    date_col = columns.get("DATE", frame.columns[0])
    value_col = columns.get("CLOSE")
    if value_col is None:
        # ``DATE,<TICKER>`` — the two-column shape the strategy indices use.
        remaining = [c for c in frame.columns if c != date_col]
        if not remaining:
            return _empty_frame()
        value_col = remaining[0]

    df = pd.DataFrame(
        {
            "index_symbol": ticker.upper(),
            "trade_date": pd.to_datetime(frame[date_col], format=_DATE_FORMAT, errors="coerce"),
            "close": pd.to_numeric(frame[value_col], errors="coerce"),
        }
    )
    # Blank value cells only. A NaT date is *not* dropped here — that's a
    # malformed row and must reach quarantine to be seen.
    df = df.loc[frame[value_col].notna()]
    return (
        df.sort_values("trade_date")
        .drop_duplicates(subset="trade_date", keep="last")
        .reset_index(drop=True)
    )


def _empty_frame() -> pd.DataFrame:
    """A correctly-typed zero-row frame, so an empty source still validates."""
    return pd.DataFrame(
        {
            "index_symbol": pd.Series([], dtype="object"),
            "trade_date": pd.Series([], dtype="datetime64[ns]"),
            "close": pd.Series([], dtype="float64"),
        }
    )


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the strategy-index contract.

    Bad rows are never silently dropped: they come back in the second
    frame so the caller can persist them for inspection.
    """
    try:
        valid = CboeStrategySchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = CboeStrategySchema.validate(valid, lazy=True)
        return valid, quarantined


def ingest_cboe_strategy(
    store: LakeStore,
    tickers: Sequence[str] | None = None,
    *,
    ingest_date: dt.date | None = None,
    raw: Mapping[str, str] | None = None,
) -> IngestResult:
    """Fetch (or use supplied) index CSVs, validate, and commit one bronze snapshot.

    ``raw`` maps ticker -> CSV text, letting callers (tests, or a future
    backfill script) inject payloads instead of hitting the network;
    :func:`fetch_index_history_raw` is called only for tickers absent from it.

    The whole family is committed as a single immutable bronze partition:
    a partial run must not half-overwrite the previous complete panel.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    resolved = tuple(t.upper() for t in (tickers if tickers is not None else DEFAULT_TICKERS))

    parsed = [
        parse_index_history_csv(
            ticker,
            raw[ticker] if raw is not None and ticker in raw else fetch_index_history_raw(ticker),
        )
        for ticker in resolved
    ]
    combined = pd.concat(parsed, ignore_index=True) if parsed else _empty_frame()
    valid, quarantined = validate_and_quarantine(combined)

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    # Quarantined rows go through the store as an immutable bronze snapshot
    # under a sibling dataset name — never a raw filesystem write (matches
    # vix.py; the lake is the only thing allowed to touch storage).
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        tickers=resolved,
    )
    _log_run(result, ingest_date, valid)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date, valid: pd.DataFrame) -> None:
    """Emit the run's full surface (`docs/STANDARDS.md` §f) — an automated
    action without a runtime record is below the bar."""
    log_event(
        _LOGGER,
        "ingest.cboe_strategy",
        dataset=DATASET,
        source="cboe_cdn",
        ingest_date=ingest_date.isoformat(),
        tickers=",".join(result.tickers),
        ticker_count=len(result.tickers),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
        first_trade_date=_edge(valid, "min"),
        last_trade_date=_edge(valid, "max"),
    )


def _edge(valid: pd.DataFrame, which: str) -> str | None:
    """Window boundary of the committed rows, for the run log. ``None`` on an
    empty frame so the log line drops the field rather than printing "NaT"."""
    if valid.empty:
        return None
    stamp = valid["trade_date"].min() if which == "min" else valid["trade_date"].max()
    return str(pd.Timestamp(stamp).date())


def ticker_labels(tickers: Iterable[str]) -> list[str]:
    """Uppercased, de-duplicated ticker list preserving order — used by callers
    building an ingest set from user input."""
    seen: dict[str, None] = {}
    for ticker in tickers:
        seen.setdefault(ticker.upper(), None)
    return list(seen)
