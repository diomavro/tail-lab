"""Daily forward-collected option-chain snapshot, both wings -- bronze layer
(``docs/DATA_CONTRACTS.md`` #6, ``docs/adr/0020``, ``docs/adr/0031``).

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
every possible question about one name. Each chain is fetched ONCE and sliced
twice: the put wing into ``option_chain_snapshot`` and the call wing into its
own ``option_chain_call_snapshot`` (``docs/adr/0031``), each with its own
moneyness band, contract, and failure boundary.

Same three-function split as every other adapter here, so tests never touch
the network:

- :func:`parse_cboe_chain` -- pure, turns one symbol's JSON into one wing's
  slice.
- :func:`fetch_chain_raw` -- the HTTP call, exercised only by
  ``make ingest-option-chain`` and the scheduled workflow, never by CI.
- :func:`fetch_chains` -- one fetch per symbol, shared by both wings.
- :func:`ingest_option_chain` -- :func:`fetch_chains`' payloads -> parse ->
  validate/quarantine -> commit one bronze partition for the whole
  universe, for one wing.

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

import pandas as pd
import requests

from tail_lab.contracts.option_chain import (
    DATASET,
    MAX_TENOR_DAYS,
    MIN_SYMBOL_FRACTION,
    QUARANTINE_DATASET,
    WINGS,
    IncompleteSweepError,
    OptionChainSnapshotSchema,
    Right,
    SessionInProgress,
    plan_session_write,
    split_valid_and_quarantined,
)
from tail_lab.ingestion.json_payload import json_list, json_object, to_float
from tail_lab.ingestion.ohlcv import latest_market_session
from tail_lab.ingestion.sources import retry_transient
from tail_lab.lake.store import LAKE_WRITE_ERRORS, LakeStore
from tail_lab.observability import log_event

__all__ = [
    "CBOE_CHAIN_URL",
    "DATASET",
    "MIN_SYMBOL_FRACTION",
    "QUARANTINE_DATASET",
    "IncompleteSweepError",
    "IngestResult",
    "SessionInProgress",
    "fetch_chain_raw",
    "fetch_chains",
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

    #: Which wing's dataset this run wrote (``option_chain_snapshot`` or
    #: ``option_chain_call_snapshot``) -- each wing logs its own record.
    dataset: str
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
    #: Symbols quarantined whole for carrying the other wing's delta signs
    #: (``Wing.split``): they had quotes, just not this wing's.
    symbols_misrouted: tuple[str, ...] = ()


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


def _greek(value: object, iv: float) -> float | None:
    """Cboe's greeks, with its zero-fill mapped to a typed absence."""
    if iv == _GREEK_SENTINEL or not isinstance(value, int | float | str):
        # A null or structured cell is an absent greek, as float() raising
        # TypeError on it always made it.
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_cboe_chain(payload: Mapping[str, object], right: Right = "P") -> tuple[pd.DataFrame, int]:
    """Turn one symbol's Cboe payload into one wing's slice -- puts by
    default, calls with ``right="C"`` (``docs/adr/0031``).

    Returns ``(frame, unparsed_contract_count)``. Pure: no clock, no network
    -- ``quote_date`` comes from the payload's own last-trade stamp, so a
    snapshot taken at an odd hour still lands on the session it belongs to.

    Only three filters are applied, and each drops rows that carry no
    information rather than rows that look inconvenient: the other right,
    tenors past :data:`~tail_lab.contracts.option_chain.MAX_TENOR_DAYS`, and
    strikes outside that right's moneyness band
    (:data:`~tail_lab.contracts.option_chain.WINGS`). ``unparsed`` counts
    OSI-unparseable symbols BEFORE the right filter (so both wings report
    them) plus this right's rows with an unparseable ``iv`` after it.
    Everything surviving those goes to the
    schema, which is what decides valid-vs-quarantined. An envelope of the
    wrong shape raises ``ValueError`` naming the field.
    """
    wing = WINGS[right]
    data = json_object(payload.get("data") or {}, "Cboe chain 'data'")
    underlying = str(data.get("symbol") or "").upper().lstrip("^_")
    spot = data.get("current_price")
    stamp = data.get("last_trade_time") or payload.get("timestamp")
    contracts = json_list(data.get("options") or [], "Cboe chain 'options'")

    if not underlying or not spot or not stamp:
        return pd.DataFrame(columns=_COLUMNS), 0

    quote_date = pd.Timestamp(str(stamp)[:10]).normalize()
    spot_price = to_float(spot, "current_price")

    rows: list[dict[str, object]] = []
    unparsed = 0
    for item in contracts:
        contract = json_object(item, "Cboe chain contract")
        parsed = parse_osi_symbol(str(contract.get("option", "")))
        if parsed is None:
            unparsed += 1
            continue
        _root, expiration, contract_right, strike = parsed
        if contract_right != right:
            continue
        tenor_days = (expiration - quote_date.date()).days
        if not 0 <= tenor_days <= MAX_TENOR_DAYS:
            continue
        if not wing.moneyness_min <= strike / spot_price <= wing.moneyness_max:
            continue
        try:
            iv = to_float(contract.get("iv") or 0.0, "iv")
        except ValueError:
            # Cboe is expected to zero-fill an uncomputable greek block, not
            # send text -- but an unparseable `iv` cell must cost only this
            # row, not the whole symbol's session (docs/adr/0020: nobody
            # sells a retroactive chain). Folding it into the same sentinel
            # `_greek` already treats as absent nulls delta/theo alongside
            # it too, exactly like a real zero-fill.
            unparsed += 1
            iv = _GREEK_SENTINEL
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


def fetch_chain_raw(symbol: str, *, timeout: int = _REQUEST_TIMEOUT_S) -> dict[str, object]:
    """GET one symbol's full delayed chain from Cboe. Network; never in CI.
    Raises ``ValueError`` if the body is not a JSON object."""
    url = CBOE_CHAIN_URL.format(symbol=cboe_symbol(symbol))
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return json_object(response.json(), f"Cboe chain payload for {symbol}")


def _fetch_with_retry(
    fetcher: Callable[[str], Mapping[str, object]], symbol: str
) -> Mapping[str, object]:
    """Fetch one symbol's chain, retrying a fault a retry could plausibly fix
    (``sources.retry_transient``). An exception that is not a ``requests``
    fault -- e.g. one raised by an injected test fetcher -- is never retried."""
    return retry_transient(
        lambda: fetcher(symbol),
        attempts=_FETCH_ATTEMPTS,
        backoff_s=_FETCH_BACKOFF_S,
        event="ingestion.option_chain.symbol_retry",
        symbol=symbol,
    )


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the PUT contract.

    The adapters' common entry point, kept for that convention; the writers
    themselves go through ``Wing.split``, which also picks the wing's schema.
    Bad rows are never silently dropped: they come back in the second frame
    so the caller can persist them for inspection.
    """
    return split_valid_and_quarantined(df, OptionChainSnapshotSchema)


def fetch_chains(
    symbols: Iterable[str],
    fetch: Callable[[str], Mapping[str, object]] | None = None,
) -> dict[str, Mapping[str, object]]:
    """Fetch each symbol's chain ONCE, for every wing to parse
    (``docs/adr/0031``). A symbol whose fetch fails is absent from the result,
    which :func:`ingest_option_chain` counts as a failed symbol.

    One fetch, not one per wing: a second loop would repeat 2026-09-19 (48
    requests, Cboe 429 on 9 of 24) and could draw the two wings from
    different CDN snapshots.
    """
    fetcher = fetch or fetch_chain_raw
    payloads: dict[str, Mapping[str, object]] = {}
    for symbol in symbols:
        try:
            payloads[symbol] = _fetch_with_retry(fetcher, symbol)
        except Exception:
            # The ingest loop's process boundary (see ingest_option_chain),
            # moved one seam earlier: a dead chain costs that symbol both
            # wings, logged with its traceback, never the other symbols.
            _LOGGER.exception("event=ingestion.option_chain.symbol_failed symbol=%s", symbol)
    return payloads


@dataclass
class _Parsed:
    """One wing's per-symbol harvest, before validation."""

    frames: list[pd.DataFrame] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    unparsed: int = 0
    per_symbol: dict[str, int] = field(default_factory=dict)

    def combined(self) -> pd.DataFrame:
        if not self.frames:
            return pd.DataFrame(columns=_COLUMNS)
        return pd.concat(self.frames, ignore_index=True)


def _parse_symbols(
    symbols: Sequence[str], payloads: Mapping[str, Mapping[str, object]], right: Right
) -> _Parsed:
    parsed = _Parsed()
    dataset = WINGS[right].dataset
    for symbol in symbols:
        try:
            payload = payloads.get(symbol)
            if payload is None:
                # Absent from fetch_chains' payloads: its fetch failed, and
                # fetch_chains already logged the traceback. A known case, so
                # one line here, not a second traceback blaming the parse.
                _LOGGER.warning(
                    "event=ingestion.option_chain.symbol_unfetched symbol=%s dataset=%s",
                    symbol,
                    dataset,
                )
                parsed.failed.append(symbol.upper())
                continue
            frame, unparsed = parse_cboe_chain(payload, right)
        except Exception:
            # Deliberately broad (Rule 14's process-boundary case, not an
            # oversight): this loop is the one capture of an unrecoverable
            # session (docs/adr/0020), so one symbol's unforeseen fault --
            # a bug included -- must cost that symbol, not the other 23. It is
            # not silent: the full traceback is logged, the symbol lands in
            # ``symbols_failed``, and a fault that hits most symbols trips
            # the MIN_SYMBOL_FRACTION floor in plan_session_write and fails
            # the run red. A narrow catch would instead turn a one-symbol bug
            # into a whole lost session.
            _LOGGER.exception(
                "event=ingestion.option_chain.symbol_failed symbol=%s dataset=%s", symbol, dataset
            )
            parsed.failed.append(symbol.upper())
            continue
        parsed.unparsed += unparsed
        if frame.empty:
            parsed.failed.append(symbol.upper())
            continue
        parsed.frames.append(frame)
        parsed.ok.append(symbol.upper())
        parsed.per_symbol[symbol.upper()] = len(frame)
    return parsed


def ingest_option_chain(
    store: LakeStore,
    symbols: Sequence[str],
    *,
    payloads: Mapping[str, Mapping[str, object]],
    ingest_date: dt.date | None = None,
    min_symbol_fraction: float = MIN_SYMBOL_FRACTION,
    market_session: dt.date | None = None,
    now: dt.datetime | None = None,
    right: Right = "P",
) -> IngestResult:
    """Sweep ``symbols``, slice each chain, and commit ONE bronze partition.

    ``right`` picks the wing -- its dataset, quarantine, contract and band all
    come from that one choice (``contracts.option_chain.WINGS``). It never
    fetches: ``payloads`` come from :func:`fetch_chains`, and a symbol absent
    from them counts as failed, exactly as a fetch failure does. That is how
    ``--local`` slices both wings out of one fetch per chain, and tests pass
    ``fetch_chains(symbols, fake_fetch)`` to run without a socket. A symbol
    whose fetch or parse fails is recorded in ``symbols_failed`` and
    skipped -- one dead chain must not cost the other sixty-nine their only
    chance at today's quotes.

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
    wing = WINGS[right]
    parsed = _parse_symbols(symbols, payloads, right)
    # Per symbol, as the route does (``Wing.split``): a chain whose deltas are
    # the other wing's is quarantined whole, so both writers agree on what a
    # Cboe sign glitch costs -- that symbol, never the session.
    split = wing.split(parsed.combined())
    if split.misrouted:
        _LOGGER.warning(
            "event=ingestion.option_chain.misrouted dataset=%s symbols=%s rows=%d -- their "
            "deltas are the other wing's; quarantined whole",
            wing.dataset,
            ",".join(split.misrouted),
            split.misrouted_rows,
        )

    # Partition by the SESSION the quotes belong to, never by the clock --
    # see the note in ``api/ingest_routes``. Shared with that route so the two
    # writers cannot drift apart again (``contracts.option_chain.SessionPlan``).
    plan = plan_session_write(
        split.valid,
        split.quarantined,
        requested=symbols,
        fetched_ok=parsed.ok,
        fetch_failed=parsed.failed,
        partition_exists=lambda day: store.bronze_partition_exists(wing.dataset, day),
        min_symbol_fraction=min_symbol_fraction,
        ingest_date=ingest_date,
        market_session=market_session,
        now=now,
    )
    valid, quarantined, ingest_date = plan.valid, plan.quarantined, plan.ingest_date

    bronze_path = store.write_bronze(wing.dataset, ingest_date, valid)
    quarantine_path: str | None = None
    if not quarantined.empty:
        # A failed QUARANTINE write must not fail the sweep. Quarantine is
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
        # longer matched it (repaired 2026-09-24). What is survived is a LAKE
        # write failure (`LAKE_WRITE_ERRORS`: Delta, Arrow, I/O). Anything else
        # is a bug and fails loud -- the Rule 14 trade-off, accepted
        # explicitly: an unforeseen class raises here rather than hiding.
        try:
            quarantine_path = store.write_bronze(wing.quarantine_dataset, ingest_date, quarantined)
        except LAKE_WRITE_ERRORS:
            _LOGGER.exception(
                "event=ingestion.option_chain.quarantine_write_failed dataset=%s ingest_date=%s "
                "rows=%d -- the session itself committed fine; these rows are not persisted",
                wing.quarantine_dataset,
                ingest_date.isoformat(),
                len(quarantined),
            )

    quote_date = valid["quote_date"].max().date() if not valid.empty else ingest_date
    result = IngestResult(
        dataset=wing.dataset,
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        quote_date=quote_date,
        symbols_ok=plan.symbols_ok,
        symbols_failed=plan.symbols_failed,
        unparsed_contracts=parsed.unparsed,
        per_symbol_rows=parsed.per_symbol,
        committed=not plan.already_captured,
        symbols_off_session=plan.symbols_off_session,
        symbols_misrouted=split.misrouted,
    )
    _log_run(result, ingest_date)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date) -> None:
    log_event(
        _LOGGER,
        "ingestion.option_chain.run",
        dataset=result.dataset,
        ingest_date=ingest_date,
        quote_date=result.quote_date,
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        symbols_ok=len(result.symbols_ok),
        symbols_failed=",".join(result.symbols_failed) or None,
        unparsed_contracts=result.unparsed_contracts or None,
        committed=result.committed,
        symbols_off_session=",".join(result.symbols_off_session) or None,
        symbols_misrouted=",".join(result.symbols_misrouted) or None,
        bronze_path=result.bronze_path,
    )


def sweep_to_records(
    symbols: Iterable[str],
    *,
    fetch: Callable[[str], Mapping[str, object]] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Fetch + slice ``symbols`` and return plain JSON-ready records, as
    ``(put_records, call_records)``.

    The credential-free half of the scheduled sweep: the workflow runs this
    (it needs no lake access at all), then hands the rows to the live app,
    which owns the object-storage credentials and does the bronze write. See
    ``docs/adr/0020`` for why the write is delegated rather than granted.

    Each chain is fetched ONCE and sliced into both wings (``docs/adr/0031``),
    each in its own failure boundary: a dead fetch costs that symbol both
    wings, but a bug in one wing's parse or record conversion costs only that
    wing -- a call-only fault can never push the put session under the
    symbol floor.
    """
    puts: list[dict[str, object]] = []
    calls: list[dict[str, object]] = []
    # A dropped symbol is logged by fetch_chains and then counted against the
    # symbol floor by the route that commits the rows.
    for symbol, payload in fetch_chains(symbols, fetch).items():
        puts.extend(_wing_records(payload, symbol, "P"))
        calls.extend(_wing_records(payload, symbol, "C"))
    return puts, calls


def _wing_records(
    payload: Mapping[str, object], symbol: str, right: Right
) -> list[dict[str, object]]:
    """One wing of one payload as JSON-ready records, or ``[]`` (logged with
    its traceback) if that wing's parse or conversion fails. Broad for the
    same process-boundary reason as the ingest loop: the other wing, and the
    other symbols, must survive a bug that only this one trips."""
    try:
        frame, _unparsed = parse_cboe_chain(payload, right)
        records: list[dict[str, object]] = []
        for raw in frame.to_dict(orient="records"):
            row = {str(k): v for k, v in raw.items()}
            row["quote_date"] = str(row["quote_date"].date())
            row["expiration"] = str(row["expiration"].date())
            records.append(row)
    except Exception:
        _LOGGER.exception(
            "event=ingestion.option_chain.symbol_failed symbol=%s dataset=%s",
            symbol,
            WINGS[right].dataset,
        )
        return []
    return records
