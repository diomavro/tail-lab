"""VIX futures term-structure ingestion adapter -- bronze layer
(`docs/DATA_CONTRACTS.md` #11).

Source: Cboe's public per-contract settlement CSVs,
``https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_{expiry}.csv``
-- the same keyless CDN host ``ingestion/vix.py`` and ``ingestion/cboe_strategy.py``
already use. No API key, no account, no login. Each file is one contract's
*entire* trade history from listing to expiry, so refetching it is always
safe (matches ``ingestion/cboe_strategy.py``'s full-history-every-time shape --
a partial run can never truncate a good partition).

Why this dataset exists: every research question this platform answers about
"which regime" or "how expensive is the tail right now" reads spot VIX; none
of them can see the *term structure* (contango/backwardation, how a shock
moves the front month versus the back) that a curve of contract settlements
provides for free.

**URL coverage, verified live 2026-09-02.** The ``historical_data/VX/``
path above only serves contracts expiring on or after **2013-01-16** --
every earlier date returns a bare S3 ``AccessDenied``, not the contract's
data. Pre-2013 contracts live at a different path and file-naming
convention (``.../resources/futures/archive/volume-and-price/CFE_{month
code}{YY}_VX.csv``, confirmed live for 2004-2012 contracts during the same
probe) with an apparent **10x price scaling** versus the modern series (a
2007 contract settled ~150-200 there, an implausible VIX level, versus the
same-era spot VIX trading ~15-20) that needs its own validation before
anything trusts it. Building that path is a deliberate follow-up
(`AGENT_TODO.md`), not an oversight -- silently gluing two differently-scaled
sources into one dataset would be worse than shipping half of it honestly.

**Expiry computation has no holiday adjustment.** A VX contract expires on
the Wednesday 30 calendar days before the third Friday of the following
calendar month (:func:`compute_vx_expiry`, verified against five real
contracts spanning 2004-2026 during the same probe) -- except when that
Wednesday is a market holiday, when Cboe moves it to the preceding business
day. This adapter does not implement that adjustment. The failure mode is
loud, not silent: a wrong guess 404s (a one-file-per-exact-date URL, so
there is no way to land on a different contract's data by mistake), it does
not corrupt or silently drop rows -- but it does mean the rare holiday-
affected month needs an explicit override until this is fixed.

Three functions, deliberately split so tests never touch the network:

- :func:`parse_vx_futures_csv` -- pure, parses one contract's CSV text into a
  ``(contract_expiry, trade_date, open, high, low, close, settle, volume,
  open_interest)`` frame. Unit-tested against a committed fixture built from
  a real fetch (``tests/fixtures/vix_futures_h2020_sample.csv``, the Mar-2020
  contract's inception window plus its expiry into the COVID crash).
- :func:`fetch_vx_futures_raw` -- makes the HTTP call. Exercised only by
  ``make ingest-vix-futures`` (human/manually run), never by CI/pytest.
- :func:`ingest_vix_futures` -- orchestrates fetch -> parse -> validate/
  quarantine -> commit-to-bronze across the requested set of contracts, one
  immutable snapshot per run (never one write per contract -- a partial run
  must not half-overwrite yesterday's complete curve).

:func:`default_expiries` picks a deliberately conservative 6 months forward
from ``as_of`` -- CBOE has listed at least that many consecutive VX months
throughout the product's history, so the default `make` target never guesses
past what is actually listed. A caller who wants deeper history (a full
2013-present backfill) passes an explicit ``expiries`` list instead.
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

from tail_lab.contracts.calendar import third_friday
from tail_lab.contracts.vix_futures import DATASET, VxFuturesSchema
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "MODERN_URL_START",
    "QUARANTINE_DATASET",
    "IngestResult",
    "compute_vx_expiry",
    "default_expiries",
    "fetch_vx_futures_raw",
    "ingest_vix_futures",
    "parse_vx_futures_csv",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

VX_HISTORY_URL_TEMPLATE = (
    "https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_{expiry}.csv"
)
QUARANTINE_DATASET = f"{DATASET}__quarantine"

#: Earliest expiry the modern URL pattern serves (docstring above). A request
#: for an earlier contract will 404/AccessDeny, not return wrong data.
MODERN_URL_START = dt.date(2013, 1, 16)

#: Cboe zero-fills OHLC on a day the contract didn't trade -- a real futures
#: price is never exactly $0.00 -- so it is mapped to a typed absence before
#: the schema ever sees it, same convention as ``ingestion/option_chain.py``.
_PRICE_SENTINEL = 0.0

_OHLC_COLUMNS = ("open", "high", "low", "close")


def compute_vx_expiry(year: int, month: int) -> dt.date:
    """The standard (non-holiday-adjusted) VX settlement date for the
    ``year``/``month`` contract: the Wednesday 30 calendar days before the
    third Friday of the following calendar month. See the module docstring
    for the one case this does not handle."""
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return third_friday(next_year, next_month) - dt.timedelta(days=30)


def default_expiries(as_of: dt.date, *, n_months: int = 6) -> list[dt.date]:
    """The next ``n_months`` not-yet-expired VX contract expiries as of
    ``as_of``, walking forward one contract-month at a time. Pure, no
    network -- this only decides *what* to fetch."""
    expiries: list[dt.date] = []
    year, month = as_of.year, as_of.month
    while len(expiries) < n_months:
        expiry = compute_vx_expiry(year, month)
        if expiry >= as_of:
            expiries.append(expiry)
        month, year = (1, year + 1) if month == 12 else (month + 1, year)
    return expiries


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    expiries: tuple[dt.date, ...]


def fetch_vx_futures_raw(expiry: dt.date, *, timeout: float = 30.0) -> str:
    """Fetch one contract's full settlement history CSV text. Network call --
    not used by tests."""
    resp = requests.get(
        VX_HISTORY_URL_TEMPLATE.format(expiry=expiry.isoformat()),
        headers={"User-Agent": "Mozilla/5.0 (tail-lab ingestion bot)"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def parse_vx_futures_csv(expiry: dt.date, raw: str) -> pd.DataFrame:
    """Parse one VX contract's CSV into a typed frame keyed by
    ``(contract_expiry, trade_date)``.

    Pure function -- no network, no filesystem. See the module docstring for
    the zero-fill-to-null OHLC convention; ``settle``/``volume``/
    ``open_interest`` are never zero-filled by the source and are passed
    through as-is (a genuine 0 open interest on a contract's first listed
    day is real, not a sentinel).
    """
    frame = pd.read_csv(io.StringIO(raw))
    if frame.empty:
        return _empty_frame()

    df = pd.DataFrame(
        {
            "contract_expiry": pd.Timestamp(expiry),
            "trade_date": pd.to_datetime(frame["Trade Date"], format="%Y-%m-%d", errors="coerce"),
            "open": pd.to_numeric(frame["Open"], errors="coerce"),
            "high": pd.to_numeric(frame["High"], errors="coerce"),
            "low": pd.to_numeric(frame["Low"], errors="coerce"),
            "close": pd.to_numeric(frame["Close"], errors="coerce"),
            "settle": pd.to_numeric(frame["Settle"], errors="coerce"),
            "volume": pd.to_numeric(frame["Total Volume"], errors="coerce"),
            "open_interest": pd.to_numeric(frame["Open Interest"], errors="coerce"),
        }
    )
    for column in _OHLC_COLUMNS:
        df.loc[df[column] == _PRICE_SENTINEL, column] = None

    return (
        df.sort_values("trade_date")
        .drop_duplicates(subset="trade_date", keep="last")
        .reset_index(drop=True)
    )


def _empty_frame() -> pd.DataFrame:
    """A correctly-typed zero-row frame, so an empty source still validates."""
    return pd.DataFrame(
        {
            "contract_expiry": pd.Series([], dtype="datetime64[ns]"),
            "trade_date": pd.Series([], dtype="datetime64[ns]"),
            "open": pd.Series([], dtype="float64"),
            "high": pd.Series([], dtype="float64"),
            "low": pd.Series([], dtype="float64"),
            "close": pd.Series([], dtype="float64"),
            "settle": pd.Series([], dtype="float64"),
            "volume": pd.Series([], dtype="int64"),
            "open_interest": pd.Series([], dtype="int64"),
        }
    )


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the VX futures contract.

    Bad rows are never silently dropped: they come back in the second frame
    so the caller can persist them for inspection.
    """
    try:
        valid = VxFuturesSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = VxFuturesSchema.validate(valid, lazy=True)
        return valid, quarantined


def ingest_vix_futures(
    store: LakeStore,
    expiries: Sequence[dt.date] | None = None,
    *,
    ingest_date: dt.date | None = None,
    raw: Mapping[dt.date, str] | None = None,
) -> IngestResult:
    """Fetch (or use supplied) contract CSVs, validate, and commit one bronze
    snapshot spanning the whole requested curve.

    ``raw`` maps expiry -> CSV text, letting callers (tests, or a future
    backfill script) inject payloads instead of hitting the network;
    :func:`fetch_vx_futures_raw` is called only for expiries absent from it.

    The whole curve is committed as a single immutable bronze partition: a
    partial run must not half-overwrite yesterday's complete panel (matches
    ``ingestion/cboe_strategy.py``).
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    resolved = tuple(expiries) if expiries is not None else tuple(default_expiries(ingest_date))

    parsed = [
        parse_vx_futures_csv(
            expiry,
            raw[expiry] if raw is not None and expiry in raw else fetch_vx_futures_raw(expiry),
        )
        for expiry in resolved
    ]
    combined = pd.concat(parsed, ignore_index=True) if parsed else _empty_frame()
    valid, quarantined = validate_and_quarantine(combined)

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    # Quarantined rows go through the store as an immutable bronze snapshot
    # under a sibling dataset name -- never a raw filesystem write (matches
    # vix.py; the lake is the only thing allowed to touch storage).
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        expiries=resolved,
    )
    _log_run(result, ingest_date, valid)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date, valid: pd.DataFrame) -> None:
    """Emit the run's full surface (`docs/STANDARDS.md` §f) -- an automated
    action without a runtime record is below the bar."""
    log_event(
        _LOGGER,
        "ingest.vix_futures",
        dataset=DATASET,
        source="cboe_cdn",
        ingest_date=ingest_date.isoformat(),
        contract_count=len(result.expiries),
        nearest_expiry=min(result.expiries).isoformat() if result.expiries else None,
        farthest_expiry=max(result.expiries).isoformat() if result.expiries else None,
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
