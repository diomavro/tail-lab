"""Options-expiration-dates ingestion adapter -- bronze layer
(``docs/END_STATE.md`` §1.1/§1.2, ``AGENT_TODO.md``'s "live options-expiry
cadence adapter" item).

**Primary source: Cboe** --
``https://cdn-api.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json``.
Keyless, no cookie, no crumb, no account: a plain GET returns the whole
listed chain (14,402 contracts for SPY when measured on 2026-08-21), and
every contract carries its expiration inside its OCC symbol. Deriving the
expiration set from the chain is therefore strictly more direct than asking
a separate endpoint for it -- the expirations *are* the chain.

**Fallback source: Yahoo's options endpoint** -- the original primary, now
demoted. It is keyless but no longer answers a bare GET: a request without a
valid ``crumb`` 401s, so it needs a three-step cookie-carrying session
(``fc.yahoo.com`` for a cookie -> ``getcrumb`` -> the options endpoint), and
Yahoo has additionally been serving HTTP 429 to residential and datacenter
clients alike since 2026 (``docs/DATA_FINDINGS.md``). Kept in the chain as a
second opinion, not relied upon.

Index symbols carry an underscore prefix on Cboe's CDN (``_SPX``, ``_VIX``,
``_RUT``) while ETFs and single names do not (``SPY``, ``QQQ``); the fetcher
tries the bare form first and the underscored form second, since the
screening universe is ETF-heavy.

Functions split so tests never touch the network:

- :func:`parse_cboe_options_expiry` / :func:`parse_yahoo_options_expiry` --
  pure parsers, one per source, each pinned to a committed fixture.
- :func:`fetch_cboe_chain_raw` / :func:`fetch_options_expiry_raw` -- the HTTP
  calls, exercised only by a human-run ingest, never by CI/pytest.

:func:`ingest_options_expiry` orchestrates resolve-source -> validate/
quarantine -> commit-to-bronze, mirroring ``ingest_vix`` / ``ingest_ohlcv``.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.options_expiry import OptionsExpirySchema, dataset_id
from tail_lab.ingestion.sources import Source, first_available
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "IngestResult",
    "dataset_id",
    "fetch_cboe_chain_raw",
    "fetch_options_expiry_raw",
    "ingest_options_expiry",
    "parse_cboe_options_expiry",
    "parse_yahoo_options_expiry",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

CBOE_CHAIN_URL_TEMPLATE = "https://cdn-api.cboe.com/api/global/delayed_quotes/options/{symbol}.json"

#: OCC option symbol: root, then YYMMDD, then C/P, then the 8-digit strike.
#: Only the date group is needed here.
_OCC_SYMBOL = re.compile(r"^(?P<root>[A-Z0-9]+?)(?P<expiry>\d{6})(?P<kind>[CP])(?P<strike>\d{8})$")

YAHOO_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
YAHOO_OPTIONS_URL_TEMPLATE = "https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
_USER_AGENT = "Mozilla/5.0 (tail-lab ingestion bot)"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined.

    ``source_id`` records which member of the source chain served this
    immutable partition -- unanswerable later from the rows alone."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    source_id: str


def fetch_cboe_chain_raw(symbol: str, *, timeout: float = 60.0) -> Any:
    """Fetch Cboe's delayed-quote chain JSON for ``symbol``. Network -- not
    used by tests.

    Tries the bare symbol then the underscore-prefixed index form, because
    Cboe files index chains under ``_SPX``/``_VIX``/``_RUT`` and everything
    else under its plain ticker, with no lookup to tell you which."""
    last_error: Exception | None = None
    for candidate in (symbol.upper(), f"_{symbol.upper().lstrip('_')}"):
        try:
            resp = requests.get(
                CBOE_CHAIN_URL_TEMPLATE.format(symbol=candidate),
                headers={"User-Agent": _USER_AGENT},
                timeout=timeout,
            )
            resp.raise_for_status()
            payload: Any = resp.json()
            if (payload.get("data") or {}).get("options"):
                return payload
            last_error = ValueError(f"no options in chain for {candidate!r}")
        except Exception as exc:
            last_error = exc
    raise ValueError(f"Cboe returned no usable chain for {symbol!r}: {last_error}")


def parse_cboe_options_expiry(symbol: str, raw: dict[str, Any]) -> pd.DataFrame:
    """Derive the (symbol, expiration_date) set from a Cboe chain payload.

    Pure function -- no network, no filesystem. Each contract's expiration
    lives in its OCC symbol (``SPY260821C00200000`` -> 2026-08-21), so the
    distinct expiration set is a de-duplication over the chain rather than a
    separate field. A contract whose symbol does not match the OCC pattern
    is skipped rather than guessed at: an unparseable symbol tells us
    nothing about a date, and inventing one would put a fabricated
    expiration into a dataset whose whole purpose is knowing when options
    actually list.
    """
    options = ((raw.get("data") or {}).get("options")) or []
    dates: set[dt.date] = set()
    for contract in options:
        match = _OCC_SYMBOL.match(str(contract.get("option", "")))
        if match is None:
            continue
        try:
            dates.add(dt.datetime.strptime(match.group("expiry"), "%y%m%d").date())
        except ValueError:
            continue

    if not dates:
        return pd.DataFrame(
            {
                "symbol": pd.Series([], dtype="object"),
                "expiration_date": pd.Series([], dtype="datetime64[ns]"),
            }
        )

    df = pd.DataFrame({"symbol": symbol.upper(), "expiration_date": pd.to_datetime(sorted(dates))})
    return df.reset_index(drop=True)


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
    cboe_raw: dict[str, Any] | None = None,
) -> IngestResult:
    """Resolve a source, validate, and commit to bronze.

    ``cboe_raw`` / ``raw`` inject that source's payload instead of hitting
    the network. The chain is tried in order, so a supplied ``raw`` (Yahoo)
    is used only when the Cboe source fails or yields nothing.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py/ohlcv.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    parsed, source_id = first_available(
        _build_sources(symbol, cboe_raw=cboe_raw, raw=raw),
        dataset=dataset_id(symbol),
        logger=_LOGGER,
    )
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

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        source_id=source_id,
    )
    log_event(
        _LOGGER,
        "ingest.options_expiry",
        dataset=dataset_id(symbol),
        symbol=symbol.upper(),
        source=source_id,
        ingest_date=ingest_date.isoformat(),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
        first_expiry=(
            None if valid.empty else str(pd.Timestamp(valid["expiration_date"].min()).date())
        ),
        last_expiry=(
            None if valid.empty else str(pd.Timestamp(valid["expiration_date"].max()).date())
        ),
    )
    return result


def _build_sources(
    symbol: str, *, cboe_raw: dict[str, Any] | None, raw: dict[str, Any] | None
) -> list[Source]:
    """The ordered source chain: Cboe first, Yahoo second.

    **Injection is hermetic** — supplying any payload restricts the chain to
    the injected sources, so a test that injects one source can never reach
    the network for the others (`docs/STANDARDS.md`: tests never touch the
    network). Injecting nothing gives the full live chain.
    """
    cboe = Source(
        source_id="cboe",
        fetch=lambda: parse_cboe_options_expiry(
            symbol, cboe_raw if cboe_raw is not None else fetch_cboe_chain_raw(symbol)
        ),
    )
    yahoo = Source(
        source_id="yahoo",
        fetch=lambda: parse_yahoo_options_expiry(
            symbol, raw if raw is not None else fetch_options_expiry_raw(symbol)
        ),
    )
    if cboe_raw is None and raw is None:
        return [cboe, yahoo]
    return [src for src, injected in ((cboe, cboe_raw), (yahoo, raw)) if injected is not None]
