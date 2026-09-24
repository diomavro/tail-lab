"""VIX ingestion adapter — bronze layer (`docs/DATA_CONTRACTS.md` #2).

**Primary source: Cboe** —
``https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv``.
Keyless, no account, `DATE,OPEN,HIGH,LOW,CLOSE` from **1990-01-02**, and it
is what `docs/DATA_CONTRACTS.md` #2 specified all along. Cboe computes the
VIX, so this is the index's own publisher rather than a redistributor.

**Fallback source: Yahoo's chart JSON** — the adapter's original primary,
now demoted. Yahoo began returning HTTP 429 to residential *and* datacenter
clients in 2026 (`docs/DATA_FINDINGS.md`), which is exactly why this module
now takes an ordered source chain instead of a single endpoint: when one
public endpoint dies, ingestion degrades to the next and says so in the run
log, rather than failing outright or — worse — appearing to succeed.

The functions keep the shape every adapter here uses, so tests never touch
the network:

- :func:`parse_cboe_vix_csv` / :func:`parse_yahoo_chart` — pure parsers, one
  per source, each unit-tested against a committed fixture.
- :func:`fetch_cboe_vix_raw` / :func:`fetch_vix_raw` — the HTTP calls,
  exercised only by ``make ingest-vix``, never by CI/pytest.

:func:`ingest_vix` orchestrates resolve-source -> validate/quarantine ->
commit-to-bronze.

**Deliberately unchanged: the committed shape is still ``(date, close)``.**
Cboe serves full OHLC and dataset #2 wants it, but widening the bronze
schema here would touch ``transforms/vix.py``, ``research/vix_stretch.py``
and the dashboard tile that reads them. Swapping the *source* is the fix
this module owes; completing the vol complex to OHLC across
VIX/VIX3M/VIX9D/VVIX/SKEW is its own queued increment (`AGENT_TODO.md`).
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.vix import VixSchema
from tail_lab.ingestion.sources import Source, find_header_line, first_available, require_current
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "QUARANTINE_DATASET",
    "IngestResult",
    "fetch_cboe_vix_raw",
    "fetch_vix_raw",
    "ingest_vix",
    "parse_cboe_vix_csv",
    "parse_yahoo_chart",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

CBOE_VIX_URL = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
DATASET = "vix"
QUARANTINE_DATASET = f"{DATASET}__quarantine"

_USER_AGENT = "Mozilla/5.0 (tail-lab ingestion bot)"
#: Cboe writes US-style dates. Parsed with an explicit format on purpose —
#: an inferred parse reads 06/07/1990 as 7 June in one file and 6 July in
#: another, and a VIX series silently shifted by a month around a crash is
#: worse than one that fails loudly.
_CBOE_DATE_FORMAT = "%m/%d/%Y"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined.

    ``source_id`` records which member of the source chain actually served
    this partition — bronze is immutable, so months later this is the only
    way to know whether a given snapshot came from Cboe or from a fallback.
    """

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    source_id: str


def fetch_cboe_vix_raw(*, timeout: float = 30.0) -> str:
    """Fetch Cboe's VIX history CSV text. Network call — not used by tests."""
    resp = requests.get(CBOE_VIX_URL, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_vix_raw(*, range_: str = "6mo", interval: str = "1d", timeout: float = 15.0) -> Any:
    """Fetch raw Yahoo chart JSON for ^VIX. Network call — not used by tests."""
    resp = requests.get(
        YAHOO_CHART_URL,
        params={"range": range_, "interval": interval},
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload: Any = resp.json()
    return payload


def parse_cboe_vix_csv(raw: str) -> pd.DataFrame:
    """Parse Cboe's VIX history CSV into a typed (date, close) DataFrame.

    Pure function — no network, no filesystem. The file carries full OHLC;
    only CLOSE is taken, because that is what dataset #2's committed shape
    holds today (see the module docstring on why widening it is a separate
    change). Rows with a blank close are dropped as "not a row"; anything
    else malformed survives as NaT/NaN for :func:`validate_and_quarantine`
    to catch, so bad data is never silently discarded.
    """
    header_offset = find_header_line(raw)
    frame = pd.read_csv(io.StringIO(raw), skiprows=header_offset)
    if frame.empty:
        return _empty_frame()

    columns = {str(c).strip().upper(): c for c in frame.columns}
    date_col = columns.get("DATE", frame.columns[0])
    close_col = columns.get("CLOSE")
    if close_col is None:
        remaining = [c for c in frame.columns if c != date_col]
        if not remaining:
            return _empty_frame()
        close_col = remaining[-1]

    df = pd.DataFrame(
        {
            "date": pd.to_datetime(frame[date_col], format=_CBOE_DATE_FORMAT, errors="coerce"),
            "close": pd.to_numeric(frame[close_col], errors="coerce"),
        }
    )
    df = df.loc[frame[close_col].notna()]
    return df.sort_values("date").drop_duplicates(subset="date", keep="last").reset_index(drop=True)


def _empty_frame() -> pd.DataFrame:
    """A correctly-typed zero-row frame, so an empty source still validates."""
    return pd.DataFrame(
        {
            "date": pd.Series([], dtype="datetime64[ns]"),
            "close": pd.Series([], dtype="float64"),
        }
    )


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


def _build_sources(*, cboe_csv: str | None, raw: dict[str, Any] | None) -> list[Source]:
    """The ordered source chain: Cboe first, Yahoo second.

    **Injection is hermetic.** If any payload is supplied, the chain is
    restricted to the injected sources only. Without that rule a caller who
    injects one source still reaches the network for the others — which
    would let a test that supplies a Yahoo payload silently perform a live
    Cboe fetch, breaking `docs/STANDARDS.md`'s "tests never touch the
    network". Injecting nothing gives the full live chain.
    """
    cboe = Source(
        source_id="cboe",
        fetch=lambda: parse_cboe_vix_csv(
            cboe_csv if cboe_csv is not None else fetch_cboe_vix_raw()
        ),
    )
    yahoo = Source(
        source_id="yahoo",
        fetch=lambda: parse_yahoo_chart(raw if raw is not None else fetch_vix_raw()),
    )
    if cboe_csv is None and raw is None:
        return [cboe, yahoo]
    return [src for src, injected in ((cboe, cboe_csv), (yahoo, raw)) if injected is not None]


def ingest_vix(
    store: LakeStore,
    *,
    ingest_date: dt.date | None = None,
    raw: dict[str, Any] | None = None,
    cboe_csv: str | None = None,
    market_session: dt.date | None = None,
) -> IngestResult:
    """Resolve a source, validate, and commit to bronze.

    ``cboe_csv`` (CSV text) and ``raw`` (Yahoo chart payload) let callers
    inject a payload for that source instead of hitting the network. Passing
    only ``raw`` still exercises the Yahoo path, so existing callers keep
    working — but note the chain is still tried in order, so a supplied
    ``raw`` is used only if Cboe is unavailable.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with, or point-in-time reads mismatch.
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    parsed, source_id = first_available(
        _build_sources(cboe_csv=cboe_csv, raw=raw), dataset=DATASET, logger=_LOGGER
    )
    valid, quarantined = validate_and_quarantine(parsed)

    require_current(
        valid, date_col="date", market_session=market_session, dataset=DATASET, expected=(DATASET,)
    )
    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    # Quarantined rows are committed through the store too (as an immutable
    # bronze snapshot under a sibling dataset name), never via a raw
    # filesystem write — the lake abstraction is the only thing allowed to
    # touch storage, local or object-storage alike (ARCHITECTURE.md).
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        source_id=source_id,
    )
    log_event(
        _LOGGER,
        "ingest.vix",
        dataset=DATASET,
        source=source_id,
        ingest_date=ingest_date.isoformat(),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
        first_date=None if valid.empty else str(pd.Timestamp(valid["date"].min()).date()),
        last_date=None if valid.empty else str(pd.Timestamp(valid["date"].max()).date()),
    )
    return result
