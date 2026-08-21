"""Options-expiration-dates ingestion adapter -- bronze layer
(``docs/END_STATE.md`` §1.1/§1.2, ``AGENT_TODO.md``'s "live options-expiry
cadence adapter" item).

Source: Yahoo Finance's public options-chain JSON endpoint
(``https://query1.finance.yahoo.com/v7/finance/options/<SYMBOL>``). Still
keyless -- no account, no API key, no login -- but as of 2026-08-21 it no
longer answers a bare GET the way ``ingestion/vix.py``'s and
``ingestion/ohlcv.py``'s chart endpoint does: a request without a valid
``crumb`` now 401s with ``{"error": {"code": "Unauthorized", "description":
"Invalid Crumb"}}``. The documented (and ``yfinance``-library-proven)
workaround is a three-step, cookie-carrying session, all against Yahoo's own
keyless endpoints:

1. ``GET https://fc.yahoo.com`` -- seeds a session cookie (the response
   itself is a 404; only its ``Set-Cookie`` header matters).
2. ``GET https://query2.finance.yahoo.com/v1/test/getcrumb`` (with that
   cookie) -- returns a plain-text crumb token.
3. ``GET https://query1.finance.yahoo.com/v7/finance/options/<SYMBOL>
   ?crumb=<crumb>`` (with the same cookie) -- returns the options-chain JSON.

This is a fetch-mechanism change, not a new adapter shape: the module still
splits into a pure parser (tested against a fixture) and a network fetcher
(exercised only by a human-run ingest, never by CI/pytest), same as every
other ``ingestion/`` adapter.

- :func:`parse_yahoo_options_expiry` -- pure, parses the JSON shape into a
  DataFrame. Unit-tested against the committed fixture
  ``tests/fixtures/options_expiry_yahoo_sample.json``.
- :func:`fetch_options_expiry_raw` -- makes the three HTTP calls above.
  Exercised only by a human-run ingest, never by CI/pytest.

:func:`ingest_options_expiry` orchestrates fetch -> parse -> validate/quarantine
-> commit-to-bronze, mirroring ``ingest_vix`` / ``ingest_ohlcv``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.options_expiry import OptionsExpirySchema, dataset_id
from tail_lab.lake.store import LakeStore

__all__ = [
    "IngestResult",
    "dataset_id",
    "fetch_options_expiry_raw",
    "ingest_options_expiry",
    "parse_yahoo_options_expiry",
]

YAHOO_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
YAHOO_OPTIONS_URL_TEMPLATE = "https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
_USER_AGENT = "Mozilla/5.0 (tail-lab ingestion bot)"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None


def fetch_options_expiry_raw(symbol: str, *, timeout: float = 15.0) -> Any:
    """Fetch raw Yahoo options-chain JSON for ``symbol``. Network calls --
    not used by tests. See the module docstring for the cookie+crumb flow
    this now requires."""
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})
    # Best-effort cookie seed -- the response itself is a 404; only its
    # Set-Cookie header is needed, so its status is not checked.
    session.get(YAHOO_COOKIE_URL, timeout=timeout)
    crumb_resp = session.get(YAHOO_CRUMB_URL, timeout=timeout)
    crumb_resp.raise_for_status()
    crumb = crumb_resp.text
    resp = session.get(
        YAHOO_OPTIONS_URL_TEMPLATE.format(symbol=symbol.upper()),
        params={"crumb": crumb},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload: Any = resp.json()
    return payload


def parse_yahoo_options_expiry(symbol: str, raw: dict[str, Any]) -> pd.DataFrame:
    """Parse Yahoo's options-chain JSON payload into a typed
    (symbol, expiration_date) DataFrame -- one row per listed expiration.

    Pure function -- no network, no filesystem. Expiration timestamps are
    UTC midnight already (Yahoo lists them as whole trading dates, not
    intraday), so no local/UTC conversion is needed here, unlike the
    chart-quote adapters which convert an exchange-local timestamp.
    """
    result = raw["optionChain"]["result"][0]
    timestamps: list[int] = result["expirationDates"]
    dates = [dt.datetime.fromtimestamp(ts, tz=dt.UTC).date() for ts in timestamps]

    df = pd.DataFrame({"symbol": symbol.upper(), "expiration_date": pd.to_datetime(dates)})
    return (
        df.sort_values("expiration_date")
        .drop_duplicates(subset="expiration_date", keep="last")
        .reset_index(drop=True)
    )


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the options-expiry
    contract.

    Bad rows are never silently dropped: they come back in the second frame
    so the caller can persist them for inspection.
    """
    try:
        valid = OptionsExpirySchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = OptionsExpirySchema.validate(valid, lazy=True)
        return valid, quarantined


def ingest_options_expiry(
    store: LakeStore,
    symbol: str,
    *,
    ingest_date: dt.date | None = None,
    raw: dict[str, Any] | None = None,
) -> IngestResult:
    """Fetch (or use a supplied) raw payload, validate, and commit to bronze.

    ``raw`` lets callers (tests, or a future backfill script) inject a
    payload instead of hitting the network; ``fetch_options_expiry_raw`` is
    called only when ``raw`` is omitted.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py/ohlcv.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    payload = raw if raw is not None else fetch_options_expiry_raw(symbol)
    parsed = parse_yahoo_options_expiry(symbol, payload)
    valid, quarantined = validate_and_quarantine(parsed)

    bronze_path = store.write_bronze(dataset_id(symbol), ingest_date, valid)

    # Quarantined rows go through the store as an immutable bronze snapshot
    # under a sibling dataset name -- never a raw filesystem write (matches
    # vix.py/ohlcv.py; the lake is the only thing allowed to touch storage).
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(
            f"{dataset_id(symbol)}__quarantine", ingest_date, quarantined
        )

    return IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
    )
