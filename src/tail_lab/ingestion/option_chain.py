"""Daily forward-collected put-wing snapshot -- bronze layer
(``docs/DATA_CONTRACTS.md`` #6, ``docs/adr/0020``).

Source: Cboe's public delayed-quote CDN,
``https://cdn-api.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json``.
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
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import requests

from tail_lab.contracts.option_chain import (
    DATASET,
    MAX_TENOR_DAYS,
    MIN_SYMBOL_FRACTION,
    MONEYNESS_MAX,
    MONEYNESS_MIN,
    IncompleteSweepError,
    plan_session_write,
    split_valid_and_quarantined,
)
from tail_lab.ingestion.ohlcv import fetch_nasdaq_raw, parse_nasdaq_historical
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "CBOE_CHAIN_URL",
    "DATASET",
    "MIN_SYMBOL_FRACTION",
    "QUARANTINE_DATASET",
    "IncompleteSweepError",
    "IngestResult",
    "fetch_chain_raw",
    "ingest_option_chain",
    "latest_market_session",
    "parse_cboe_chain",
    "parse_osi_symbol",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

#: ``cdn-api``, not the old ``cdn.cboe.com``. Cboe moved ``/api/global/`` there:
#: the old host froze at 2026-09-23 03:55 UTC while still answering 200 with
#: the last snapshot, and only began 307-redirecting the chain paths on the
#: afternoon of 2026-09-24 (the index-history CSVs never redirected). A frozen
#: 200 is the worst failure a keyless source has -- see
#: ``contracts.option_chain.plan_session_write`` for the check that catches it.
CBOE_CHAIN_URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
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

#: How many times to fetch one symbol's chain before recording it as failed,
#: and how long to wait between attempts.
#:
#: Same reasoning as ``scripts/chain_snapshot.py``'s POST retry, one seam
#: earlier: a per-symbol Cboe blip previously cost that symbol its ENTIRE
#: session on the first exception, permanently -- nobody sells a retroactive
#: chain (module docstring). The driving script's ``MIN_PLAUSIBLE_ROWS`` floor
#: only catches a WHOLESALE failure across the universe; losing one name of
#: two dozen still passes it, and that loss is exactly as unrecoverable as
#: losing all of them.
_FETCH_ATTEMPTS = 3
_FETCH_BACKOFF_S = 3.0
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
    #: Whether this run actually CREATED the partition. ``write_bronze``
    #: returns the same path string whether it wrote or short-circuited on an
    #: existing ingest_date, so before this flag no caller could tell the two
    #: apart and every one of them printed ``valid_rows`` regardless. Measured
    #: 2026-09-19: two runs reported "committed 14091 rows" and "committed
    #: 19525 rows" against a partition that already held 19,572; the Delta log
    #: gained no version and neither wrote a byte.
    committed: bool = True
    #: Symbols whose quotes do not belong to this partition's session, in
    #: EITHER direction -- Cboe serving a symbol's previous session, or a
    #: symbol with no ``last_trade_time`` dated from the CDN's wall clock.
    #: Their rows are quarantined rather than filed under a session they did
    #: not trade in, and they have NO quotes for this session.
    symbols_off_session: tuple[str, ...] = ()


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


def _retryable_fetch_error(exc: Exception) -> bool:
    """Whether retrying THIS symbol's fetch could plausibly change the answer.

    Mirrors ``scripts/chain_snapshot.py``'s ``_retryable`` for the POST leg:
    a 5xx, a 429, or a connection/timeout fault says Cboe (or the network)
    failed to answer a request it might answer next time. A 4xx other than
    429 says Cboe understood the request and refused it -- most often a root
    this host does not list at all -- and repeating the identical GET cannot
    change that.
    """
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, requests.exceptions.RequestException)


def _fetch_with_retry(
    fetcher: Callable[[str], Mapping[str, Any]], symbol: str
) -> Mapping[str, Any]:
    """Fetch one symbol's chain, retrying a fault a retry could plausibly fix.

    Deliberately does not retry an exception ``_retryable_fetch_error`` calls
    a firm no (or one raised by an injected test fetcher, which is never a
    ``requests`` exception) -- retrying those only burns time before the
    symbol is recorded as failed anyway.
    """
    for attempt in range(1, _FETCH_ATTEMPTS + 1):
        try:
            return fetcher(symbol)
        except Exception as exc:
            if not _retryable_fetch_error(exc) or attempt == _FETCH_ATTEMPTS:
                raise
            _LOGGER.warning(
                "event=ingestion.option_chain.symbol_retry symbol=%s attempt=%d/%d",
                symbol,
                attempt,
                _FETCH_ATTEMPTS,
            )
            time.sleep(_FETCH_BACKOFF_S)
    raise AssertionError("_FETCH_ATTEMPTS must be >= 1")


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the snapshot contract.

    Bad rows are never silently dropped: they come back in the second frame
    so the caller can persist them for inspection.
    """
    return split_valid_and_quarantined(df)


#: The instrument whose daily bars stand in for "did the US market trade".
#: SPY because it is the most liquid listed name and already an OHLCV source
#: here; Nasdaq's historical endpoint lists only COMPLETED sessions (measured
#: 2026-09-24 17:36 UTC, mid-session: newest bar 2026-09-23), so a sweep run
#: during market hours cannot mistake a live session for a finished one.
_MARKET_REFERENCE_SYMBOL = "SPY"


def latest_market_session(
    fetch: Callable[[], Mapping[str, Any]] | None = None,
) -> dt.date | None:
    """The newest completed US equity session, from a source that is not Cboe.

    Exists because Cboe cannot testify against itself: when its feed freezes,
    every chain agrees on the stale session and nothing inside the sweep can
    tell. Returns ``None`` -- logged, never raised -- when the reference
    cannot be read: a failed cross-check must not cost the sweep it guards.
    """
    try:
        raw = fetch() if fetch is not None else fetch_nasdaq_raw(_MARKET_REFERENCE_SYMBOL, years=1)
        bars = parse_nasdaq_historical(_MARKET_REFERENCE_SYMBOL, dict(raw))
        newest = pd.to_datetime(bars["trade_date"]).max()
    except Exception:
        _LOGGER.exception("event=ingestion.option_chain.market_session_unavailable")
        return None
    if pd.isna(newest):
        _LOGGER.warning("event=ingestion.option_chain.market_session_unavailable reason=no_bars")
        return None
    session: dt.date = newest.date()
    return session


def ingest_option_chain(
    store: LakeStore,
    symbols: Sequence[str],
    *,
    ingest_date: dt.date | None = None,
    fetch: Callable[[str], Mapping[str, Any]] | None = None,
    min_symbol_fraction: float = MIN_SYMBOL_FRACTION,
    market_session: dt.date | None = None,
) -> IngestResult:
    """Sweep ``symbols``, slice each chain, and commit ONE bronze partition.

    ``fetch`` is injectable so tests drive the whole orchestration off canned
    payloads without a socket. A symbol whose fetch or parse fails is
    recorded in ``symbols_failed`` and skipped -- one dead chain must not
    cost the other sixty-nine their only chance at today's quotes.

    Raises ``IncompleteSweepError`` when fewer than ``min_symbol_fraction`` of
    the requested chains returned AND no partition exists yet for the session:
    bronze is immutable, so writing a partial session is permanent, and
    refusing leaves it recoverable until the next US open.

    Also raises it when the session Cboe serves is already captured but
    ``market_session`` -- the newest completed session per an independent
    source, see :func:`latest_market_session` -- is newer: Cboe's feed is
    behind, and a no-op would lose that session silently.

    ``IngestResult.committed`` says whether this call actually created the
    partition; a re-run, a weekend or a holiday resolves to a session already
    captured and writes nothing.
    """
    fetcher = fetch or fetch_chain_raw

    frames: list[pd.DataFrame] = []
    ok: list[str] = []
    failed: list[str] = []
    unparsed_total = 0
    per_symbol: dict[str, int] = {}

    for symbol in symbols:
        try:
            payload = _fetch_with_retry(fetcher, symbol)
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

    # Partition by the SESSION the quotes belong to, never by the clock --
    # see the note in ``api/ingest_routes``. Shared with that route so the two
    # writers cannot drift apart again (``contracts.option_chain.SessionPlan``).
    plan = plan_session_write(
        valid,
        quarantined,
        requested=symbols,
        fetched_ok=ok,
        fetch_failed=failed,
        partition_exists=lambda day: store.bronze_partition_exists(DATASET, day),
        min_symbol_fraction=min_symbol_fraction,
        ingest_date=ingest_date,
        market_session=market_session,
    )
    valid, quarantined, ingest_date = plan.valid, plan.quarantined, plan.ingest_date

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)
    quarantine_path: str | None = None
    if not quarantined.empty:
        # A failed QUARANTINE write must never fail the sweep. Quarantine is
        # diagnostic; the session itself is already committed on the line
        # above, and bronze is immutable so it cannot be un-written. Raising
        # here turns a correct capture into a non-zero exit, which fires the
        # chain-loss alert and tells the operator to re-run -- a re-run that
        # can only no-op. Cry-wolf on the one alert that must stay credible.
        #
        # Measured 2026-09-23 00:31, the first time an off-session symbol met
        # a non-empty quarantine table in production: xbi lagged, its rows
        # were correctly quarantined, the 23 fresh chains were correctly
        # committed -- and the run still exited 2 with
        # "SchemaMismatchError: number of fields does not match: 13 vs 14".
        # The quarantine table carried a stale `__index_level_0__` column
        # from before write_bronze's reset_index fix, so a clean frame no
        # longer matched it (repaired 2026-09-24). Diagnostic writes must never
        # hold the sweep hostage, whatever breaks them next.
        try:
            quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)
        except Exception:
            _LOGGER.exception(
                "event=ingestion.option_chain.quarantine_write_failed dataset=%s ingest_date=%s "
                "rows=%d -- the session itself committed fine; these rows are not persisted",
                QUARANTINE_DATASET,
                ingest_date.isoformat(),
                len(quarantined),
            )

    quote_date = valid["quote_date"].max().date() if not valid.empty else ingest_date
    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        quote_date=quote_date,
        symbols_ok=plan.symbols_ok,
        symbols_failed=plan.symbols_failed,
        unparsed_contracts=unparsed_total,
        per_symbol_rows=per_symbol,
        committed=not plan.already_captured,
        symbols_off_session=plan.symbols_off_session,
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
        committed=result.committed,
        symbols_off_session=",".join(result.symbols_off_session) or None,
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
            frame, _unparsed = parse_cboe_chain(_fetch_with_retry(fetcher, symbol))
        except Exception:
            _LOGGER.exception("event=ingestion.option_chain.symbol_failed symbol=%s", symbol)
            continue
        for raw in frame.to_dict(orient="records"):
            row = {str(k): v for k, v in raw.items()}
            row["quote_date"] = str(row["quote_date"].date())
            row["expiration"] = str(row["expiration"].date())
            records.append(row)
    return records
