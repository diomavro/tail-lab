"""Point-in-time S&P 500 constituents ingestion adapter — bronze layer
(`docs/DATA_CONTRACTS.md` #10).

**Source: `fja05680/sp500` on GitHub** (MIT-licensed, community-maintained),
a single raw CSV over HTTPS, keyless: no account, no login, no rate limit
beyond GitHub's ordinary anonymous ceiling. The file is one row per
observation date, carrying that date's full membership as a comma-joined
ticker list — refetched in full on every pull (like `ingestion/cboe_strategy.py`),
so a partial or failed fetch can never truncate a good partition.

Why this dataset exists: the screening universe today is "current
constituents," which is survivorship-biased by construction — it omits
exactly the names that blew up and delisted, the most tail-sensitive assets
of all (`docs/adr/0010`). This adapter is the first step toward closing that
gap; it ships the pure adapter only; wiring a research consumer to weight
screening results by point-in-time membership is a separate, later increment
(the precedent `ingestion/rates.py` and `ingestion/credit.py` both followed).

Two functions, split so tests never touch the network:

- :func:`parse_constituents_csv` — pure, parses the source CSV text into a
  typed (obs_date, tickers) DataFrame. Unit-tested against the committed
  fixture ``tests/fixtures/sp500_constituents_sample.csv``.
- :func:`fetch_constituents_raw` — makes the HTTP call. Exercised only by
  ``make ingest-sp500-constituents`` (human/manually run), never by CI/pytest.

:func:`ingest_sp500_constituents` orchestrates fetch -> parse ->
validate/quarantine -> commit-to-bronze.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from dataclasses import dataclass

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.sp500_constituents import DATASET, Sp500ConstituentsSchema
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "QUARANTINE_DATASET",
    "IngestResult",
    "fetch_constituents_raw",
    "ingest_sp500_constituents",
    "parse_constituents_csv",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)
QUARANTINE_DATASET = f"{DATASET}__quarantine"

_USER_AGENT = "Mozilla/5.0 (tail-lab ingestion bot)"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None


def fetch_constituents_raw(*, timeout: float = 30.0) -> str:
    """Fetch the full historical-components CSV text. Network call — not used by tests."""
    resp = requests.get(CONSTITUENTS_URL, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_constituents_csv(raw: str) -> pd.DataFrame:
    """Parse the source's ``date,tickers`` CSV into a typed (obs_date, tickers) frame.

    Pure function — no network, no filesystem. The ``tickers`` cell is a
    quoted, comma-joined list (standard CSV quoting, so pandas' default
    parser handles the embedded commas without special-casing); it is kept
    as one string here rather than exploded to one row per ticker — see the
    contract module's docstring for why. A row with a blank ticker list is
    dropped as "not a row"; an unparseable date survives as NaT for
    :func:`validate_and_quarantine` to catch, so bad data is never silently
    discarded.
    """
    frame = pd.read_csv(io.StringIO(raw))
    if frame.empty:
        return _empty_frame()

    columns = {str(c).strip().lower(): c for c in frame.columns}
    date_col = columns.get("date", frame.columns[0])
    tickers_col = columns.get("tickers", frame.columns[-1])

    df = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(frame[date_col], errors="coerce"),
            "tickers": frame[tickers_col].astype("string").str.strip(),
        }
    )
    df = df.loc[df["tickers"].notna() & (df["tickers"] != "")]
    return (
        df.astype({"tickers": "object"})
        .sort_values("obs_date")
        .drop_duplicates(subset="obs_date", keep="last")
        .reset_index(drop=True)
    )


def _empty_frame() -> pd.DataFrame:
    """A correctly-typed zero-row frame, so an empty source still validates."""
    return pd.DataFrame(
        {
            "obs_date": pd.Series([], dtype="datetime64[ns]"),
            "tickers": pd.Series([], dtype="object"),
        }
    )


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the constituents contract.

    Bad rows are never silently dropped: they come back in the second frame
    so the caller can persist them for inspection.
    """
    try:
        valid = Sp500ConstituentsSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = Sp500ConstituentsSchema.validate(valid, lazy=True)
        return valid, quarantined


def ingest_sp500_constituents(
    store: LakeStore,
    *,
    ingest_date: dt.date | None = None,
    raw: str | None = None,
) -> IngestResult:
    """Fetch (or use supplied) the constituents CSV, validate, and commit one bronze snapshot.

    ``raw`` lets callers (tests, or a future backfill script) inject the CSV
    text instead of hitting the network.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    parsed = parse_constituents_csv(raw if raw is not None else fetch_constituents_raw())
    valid, quarantined = validate_and_quarantine(parsed)

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
    )
    _log_run(result, ingest_date, valid)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date, valid: pd.DataFrame) -> None:
    """Emit the run's full surface (`docs/STANDARDS.md` §f) — an automated
    action without a runtime record is below the bar."""
    log_event(
        _LOGGER,
        "ingest.sp500_constituents",
        dataset=DATASET,
        source="github_fja05680",
        ingest_date=ingest_date.isoformat(),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
        first_obs_date=_edge(valid, "min"),
        last_obs_date=_edge(valid, "max"),
    )


def _edge(valid: pd.DataFrame, which: str) -> str | None:
    """Window boundary of the committed rows, for the run log. ``None`` on an
    empty frame so the log line drops the field rather than printing "NaT"."""
    if valid.empty:
        return None
    stamp = valid["obs_date"].min() if which == "min" else valid["obs_date"].max()
    return str(pd.Timestamp(stamp).date())
