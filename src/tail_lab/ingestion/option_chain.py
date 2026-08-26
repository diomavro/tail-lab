"""Daily forward-collected put-wing snapshot -- bronze layer
(``docs/DATA_CONTRACTS.md`` #6, ``docs/adr/0020``).

Source: Cboe's public delayed-quote CDN,
``https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json``.
Keyless, no account, no crumb dance -- and it is the *exchange's own* feed
rather than a reseller's, so bid/ask/open interest and Cboe's own IV and
greeks all arrive in one document. (Yahoo's ``/v7/finance/options`` endpoint,
named as the fallback in ``contracts/options_calendar``, now answers 401
without a cookie+crumb pair; Cboe needs neither.)

**This adapter is the one that cannot wait.** Every other source here serves
history on demand, so a day we forget to ingest is recoverable. A live chain
is not: no free vendor sells a retroactive quote, so an un-snapshotted day is
lost for good. That is why this runs on a schedule that treats an empty day
as a failure, and why the slice below is tuned to be cheap enough to run
every single day across the whole universe rather than rich enough to answer
every possible question about one name.

Same three-function split as every other adapter here, so tests never touch
the network:

- :func:`parse_cboe_chain` -- pure, turns one symbol's JSON into the slice.
- :func:`fetch_chain_raw` -- the HTTP call, exercised only by
  ``make ingest-option-chain`` and the scheduled workflow, never by CI.
- :func:`ingest_option_chain` -- fetch -> parse -> validate/quarantine ->
  commit one bronze partition for the whole universe.

The universe is committed as a **single partition per day**, not one per
symbol: a half-finished sweep must not look like a complete one to an as-of
read, and ``docs/adr/0009`` makes that the adapter's problem rather than the
reader's.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.option_chain import (
    DATASET,
    MAX_TENOR_DAYS,
    MONEYNESS_MAX,
    MONEYNESS_MIN,
    OptionChainSnapshotSchema,
)
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "CBOE_CHAIN_URL",
    "DATASET",
    "QUARANTINE_DATASET",
    "IngestResult",
    "fetch_chain_raw",
    "ingest_option_chain",
    "parse_cboe_chain",
    "parse_osi_symbol",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

CBOE_CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
QUARANTINE_DATASET = f"{DATASET}__quarantine"

#: Cboe prefixes cash-settled index roots with an underscore (``_SPX``).
#: Equities and ETFs use the bare root.
_INDEX_ROOTS = frozenset({"SPX", "VIX", "NDX", "RUT", "DJX", "XSP", "OEX"})

#: Cboe's "I could not compute the greeks" fill. A listed option cannot have
#: a zero implied volatility, so a 0.0 here is a sentinel, not a measurement
#: -- and because Cboe computes the block together, a zero IV invalidates the
#: delta and theo on the same row too (``contracts/option_chain``).
_GREEK_SENTINEL = 0.0

_REQUEST_TIMEOUT_S = 60
_COLUMNS = [
    "underlying",
    "quote_date",
    "expiration",
    "strike",
    "bid",
    "ask",
    "volume",
    "open_interest",
    "spot",
    "iv",
    "delta",
    "theo",
]


@dataclass(frozen=True)
class IngestResult:
    """What one sweep committed, for the ``§f`` audit trail."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    quote_date: dt.date
    symbols_ok: tuple[str, ...] = ()
    symbols_failed: tuple[str, ...] = ()
    unparsed_contracts: int = 0
    per_symbol_rows: Mapping[str, int] = field(default_factory=dict)


def cboe_symbol(symbol: str) -> str:
    """Cboe's path segment for ``symbol`` -- ``_SPX`` for a cash index, the
    bare root for anything else."""
    root = symbol.upper().lstrip("^_")
    return f"_{root}" if root in _INDEX_ROOTS else root


def parse_osi_symbol(osi: str) -> tuple[str, dt.date, str, float] | None:
    """Split an OSI contract symbol into ``(root, expiration, right, strike)``.

    ``SPY260826P00500000`` -> ``("SPY", date(2026, 8, 26), "P", 500.0)``.
    Parsed from the right because the root is variable-length. Returns
    ``None`` for anything that does not fit the layout, so a garbled row is
    counted rather than crashing the sweep.
    """
    if len(osi) < 16:
        return None
    root, tail = osi[:-15], osi[-15:]
    ymd, right, strike_raw = tail[:6], tail[6], tail[7:]
    if not root or right not in {"C", "P"} or not ymd.isdigit() or not strike_raw.isdigit():
        return None
    try:
        expiration = dt.date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError:
        return None
    return root, expiration, right, int(strike_raw) / 1000.0


def _greek(value: Any, iv: float) -> float | None:
    """Cboe's greeks, with its zero-fill mapped to a typed absence."""
    if iv == _GREEK_SENTINEL:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_cboe_chain(payload: Mapping[str, Any]) -> tuple[pd.DataFrame, int]:
    """Turn one symbol's Cboe payload into the put-wing slice.

    Returns ``(frame, unparsed_contract_count)``. Pure: no clock, no network
    -- ``quote_date`` comes from the payload's own last-trade stamp, so a
    snapshot taken at an odd hour still lands on the session it belongs to.

    Only three filters are applied, and each drops rows that carry no
    information rather than rows that look inconvenient: calls (the platform
    buys puts), tenors past
    :data:`~tail_lab.contracts.option_chain.MAX_TENOR_DAYS`, and strikes
    outside the moneyness band. Everything surviving those goes to the
    schema, which is what decides valid-vs-quarantined.
    """
    data = payload.get("data") or {}
    underlying = str(data.get("symbol") or "").upper().lstrip("^_")
    spot = data.get("current_price")
    stamp = data.get("last_trade_time") or payload.get("timestamp")
    contracts = data.get("options") or []

    if not underlying or not spot or not stamp:
        return pd.DataFrame(columns=_COLUMNS), 0

    quote_date = pd.Timestamp(str(stamp)[:10]).normalize()
    spot_price = float(spot)

    rows: list[dict[str, Any]] = []
    unparsed = 0
    for contract in contracts:
        parsed = parse_osi_symbol(str(contract.get("option", "")))
        if parsed is None:
            unparsed += 1
            continue
        _root, expiration, right, strike = parsed
        if right != "P":
            continue
        tenor_days = (expiration - quote_date.date()).days
        if not 0 <= tenor_days <= MAX_TENOR_DAYS:
            continue
        if not MONEYNESS_MIN <= strike / spot_price <= MONEYNESS_MAX:
            continue
        iv = float(contract.get("iv") or 0.0)
        rows.append(
            {
                "underlying": underlying,
                "quote_date": quote_date,
                "expiration": pd.Timestamp(expiration),
                "strike": strike,
                "bid": contract.get("bid"),
                "ask": contract.get("ask"),
                "volume": contract.get("volume"),
                "open_interest": contract.get("open_interest"),
                "spot": spot_price,
                "iv": None if iv == _GREEK_SENTINEL else iv,
                "delta": _greek(contract.get("delta"), iv),
                "theo": _greek(contract.get("theo"), iv),
            }
        )

    frame = pd.DataFrame(rows, columns=_COLUMNS)
    if not frame.empty:
        frame = frame.sort_values(["expiration", "strike"]).reset_index(drop=True)
    return frame, unparsed


def fetch_chain_raw(symbol: str, *, timeout: int = _REQUEST_TIMEOUT_S) -> dict[str, Any]:
    """GET one symbol's full delayed chain from Cboe. Network; never in CI."""
    url = CBOE_CHAIN_URL.format(symbol=cboe_symbol(symbol))
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the snapshot contract.

    Bad rows are never silently dropped: they come back in the second frame
    so the caller can persist them for inspection.
    """
    try:
        return OptionChainSnapshotSchema.validate(df, lazy=True), df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        kept = df.loc[~df.index.isin(bad_index)]
        return OptionChainSnapshotSchema.validate(kept, lazy=True), quarantined


def ingest_option_chain(
    store: LakeStore,
    symbols: Sequence[str],
    *,
    ingest_date: dt.date | None = None,
    fetch: Callable[[str], Mapping[str, Any]] | None = None,
) -> IngestResult:
    """Sweep ``symbols``, slice each chain, and commit ONE bronze partition.

    ``fetch`` is injectable so tests drive the whole orchestration off canned
    payloads without a socket. A symbol whose fetch or parse fails is
    recorded in ``symbols_failed`` and skipped -- one dead chain must not
    cost the other sixty-nine their only chance at today's quotes.
    """
    fetcher = fetch or fetch_chain_raw
    ingest_date = ingest_date or dt.date.today()

    frames: list[pd.DataFrame] = []
    ok: list[str] = []
    failed: list[str] = []
    unparsed_total = 0
    per_symbol: dict[str, int] = {}

    for symbol in symbols:
        try:
            payload = fetcher(symbol)
            frame, unparsed = parse_cboe_chain(payload)
        except Exception:
            _LOGGER.exception("event=ingestion.option_chain.symbol_failed symbol=%s", symbol)
            failed.append(symbol.upper())
            continue
        unparsed_total += unparsed
        if frame.empty:
            failed.append(symbol.upper())
            continue
        frames.append(frame)
        ok.append(symbol.upper())
        per_symbol[symbol.upper()] = len(frame)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=_COLUMNS)
    valid, quarantined = validate_and_quarantine(combined)

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    quote_date = valid["quote_date"].max().date() if not valid.empty else ingest_date
    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        quote_date=quote_date,
        symbols_ok=tuple(ok),
        symbols_failed=tuple(failed),
        unparsed_contracts=unparsed_total,
        per_symbol_rows=per_symbol,
    )
    _log_run(result, ingest_date)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date) -> None:
    log_event(
        _LOGGER,
        "ingestion.option_chain.run",
        dataset=DATASET,
        ingest_date=ingest_date,
        quote_date=result.quote_date,
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        symbols_ok=len(result.symbols_ok),
        symbols_failed=",".join(result.symbols_failed) or None,
        unparsed_contracts=result.unparsed_contracts or None,
        bronze_path=result.bronze_path,
    )


def sweep_to_records(
    symbols: Iterable[str],
    *,
    fetch: Callable[[str], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fetch + slice ``symbols`` and return plain JSON-ready records.

    The credential-free half of the scheduled sweep: the workflow runs this
    (it needs no lake access at all), then hands the rows to the live app,
    which owns the object-storage credentials and does the bronze write. See
    ``docs/adr/0020`` for why the write is delegated rather than granted.
    """
    fetcher = fetch or fetch_chain_raw
    records: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            frame, _unparsed = parse_cboe_chain(fetcher(symbol))
        except Exception:
            _LOGGER.exception("event=ingestion.option_chain.symbol_failed symbol=%s", symbol)
            continue
        for raw in frame.to_dict(orient="records"):
            row = {str(k): v for k, v in raw.items()}
            row["quote_date"] = str(row["quote_date"].date())
            row["expiration"] = str(row["expiration"].date())
            records.append(row)
    return records
