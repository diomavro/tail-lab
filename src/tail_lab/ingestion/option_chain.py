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
import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import requests

from tail_lab.contracts.option_chain import (
    DATASET,
    MAX_TENOR_DAYS,
    MONEYNESS_MAX,
    MONEYNESS_MIN,
    split_valid_and_quarantined,
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

#: Fraction of the REQUESTED symbols that must return quotes before a sweep is
#: allowed to create a partition. Bronze is immutable, so the first write of a
#: session is the only one: a partial sweep does not merely under-report, it
#: permanently defines that session. Measured 2026-09-19 on this workstation,
#: Cboe returned 429 for 9 of 24 symbols and the run still exited 0 with
#: 14,091 rows -- far above the driving script's 200-row floor, which checks
#: rows and therefore cannot see a missing symbol at all. Every one of the 24
#: chains clears 200 rows on its own (thinnest: FXI at 213), so that floor
#: tolerated losing 23 of 24 names.
#:
#: 0.9 rather than 1.0 deliberately: demanding every symbol would let one
#: delisted or permanently-dead chain block the other 23 from ever being
#: captured, which is the same unrecoverable loss in the other direction.
#:
#: Note what the ceiling does to that argument on SMALL universes. Tolerated
#: losses are ``n - ceil(n * 0.9)``: 2 at the production 24, 7 at the 70 the
#: docstring above imagines -- but **0 for any n below 10**, where this floor
#: is effectively 1.0 and the paragraph above does not hold. That only
#: reaches a human running ``make ingest-option-chain CHAIN_SYMBOLS=spy,qqq``
#: by hand, who gets a loud refusal and can re-run; the scheduled sweep
#: always passes all 24. It is called out because the rationale reads as
#: universal and is not.
MIN_SYMBOL_FRACTION = 0.9


class IncompleteSweepError(RuntimeError):
    """Too few chains returned to define a session, and none exists yet.

    Raised INSTEAD of writing. A partial partition cannot be completed later
    (bronze is immutable), so refusing is strictly better than committing: the
    caller exits non-zero, the operator is alerted, and the session is still
    recoverable until the next US open. Never raised when the partition
    already exists -- that write would be a no-op anyway.
    """


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


def _session_from_quotes(valid: pd.DataFrame) -> dt.date:
    """The session an ABSOLUTE MAJORITY of symbols agree on -- one vote each.

    NOT ``max(quote_date)``, which this used to be and which inverts on a
    single bad record. ``parse_cboe_chain`` falls back to the payload's
    ``timestamp`` when a symbol carries no ``last_trade_time``, and that field
    is Cboe's CDN **wall clock**, not a session: measured live on 2026-09-21
    it read ``09:11:43`` on a session of ``2026-09-18``, three days apart. One
    symbol missing that field was enough to elect a session nobody traded in,
    whereupon every correct symbol looked off-session and was quarantined --
    a one-row partition stamped with a FUTURE date, permanent by
    immutability, which also pre-claimed the next session's key so the next
    real sweep no-opped.

    **More than half** is required, not merely the most votes, and there is
    deliberately no tie-break. A plurality rule left the degenerate
    distributions writing tiny partitions under a future key -- measured at
    24 symbols: a 12/12 tie wrote 12 rows dated to the wall clock, an 8/8/8
    split wrote 8, and 24 distinct dates wrote **1**. An earlier version broke
    ties toward the later date, which is precisely the side this function
    exists to distrust.

    An absolute majority is unique, so requiring one removes the tie-break
    rather than repairing it. When no date commands one, the session is not
    identifiable from this sweep and ``IncompleteSweepError`` is raised:
    bronze is immutable, so writing a partition whose own session is in doubt
    is the one thing that cannot be undone.
    """
    # One vote per symbol: its own freshest quote date.
    per_symbol = valid.groupby("underlying")["quote_date"].max().dt.date
    tally: dict[dt.date, int] = {}
    for raw in per_symbol.tolist():
        day = raw if isinstance(raw, dt.date) else dt.date.fromisoformat(str(raw))
        tally[day] = tally.get(day, 0) + 1

    voters = sum(tally.values())
    for day, votes in tally.items():
        if votes * 2 > voters:
            return day

    spread = ", ".join(f"{d}:{n}" for d, n in sorted(tally.items()))
    raise IncompleteSweepError(
        f"no session commands a majority of the {voters} chains that returned "
        f"quotes ({spread}); refusing to write, because the partition would be "
        "named for a session this sweep cannot identify and bronze is immutable."
    )


def _quarantine_off_session_rows(
    valid: pd.DataFrame, quarantined: pd.DataFrame, session: dt.date
) -> tuple[pd.DataFrame, pd.DataFrame, tuple[str, ...], dt.date | None]:
    """Move rows that do not belong to ``session`` out of ``valid``.

    Catches both directions, which is the point: a LAGGARD (Cboe still
    serving a symbol's previous session) and a WALL-CLOCK outlier (a symbol
    with no ``last_trade_time``, dated from the CDN's clock) are the same
    defect seen from either side, and only one of them is "stale".

    The laggard case is not hypothetical: ``ingest_date=2026-09-08`` in
    production holds 412 ARKK rows stamped ``2026-09-04``, ARKK's last trade
    before Labor Day. ARKK's 2026-09-08 session was never captured, and
    nothing noticed, because the partition looks healthy in aggregate.

    Off-session rows go to quarantine rather than the bin: they are real
    quotes, just not this session's. One honest limit -- quarantine is written
    with the same ``write_bronze``, so on a re-run of an already-captured
    session that write no-ops too and these rows are NOT persisted. That is
    immutability working as intended (the first run's quarantine stands), but
    it makes "preserved" a claim about the run that creates the partition.
    """
    if valid.empty:
        return valid, quarantined, (), None
    on_session = valid["quote_date"].dt.date == session
    if bool(on_session.all()):
        return valid, quarantined, (), None
    off = valid[~on_session]
    off_symbols = tuple(sorted({str(u).upper() for u in off["underlying"].unique()}))
    newest_off = off["quote_date"].max().date()
    return (
        valid[on_session].reset_index(drop=True),
        pd.concat([quarantined, off], ignore_index=True),
        off_symbols,
        newest_off,
    )


def _require_enough_symbols(
    responded: int,
    requested: int,
    min_fraction: float,
    session: dt.date,
    missing: Sequence[str] = (),
) -> None:
    """Refuse to define a session from too few chains. See ``MIN_SYMBOL_FRACTION``.

    **What this actually bounds, precisely, because the loose reading is
    wrong:** it bounds the number of *recoverable* losses, NOT the number of
    symbols missing from the partition. ``responded`` counts every symbol Cboe
    answered with usable rows for SOME session, so an off-session symbol
    (quarantined by ``_quarantine_off_session_rows``) does not count against
    the floor and the partition can therefore hold fewer names than
    ``requested - tolerated``. Measured: two fetch failures plus nine
    laggards writes a 13-of-24 partition, while three fetch failures and no
    laggards refuses at 21-of-24.

    That asymmetry is deliberate, and it is the whole design:

    * a fetch failure (429, timeout) or a symbol whose rows all fail the
      schema is **recoverable** -- a re-run before the next US open gets it.
      Writing now would lock that symbol out permanently, so refusing is the
      cheaper mistake;
    * a laggard, where Cboe serves a symbol's previous session, is **not** --
      the re-run returns the same stale chain. Counting it against the floor
      would discard every healthy chain alongside it, permanently, to avoid a
      partial that is already the best answer obtainable.

    Measured before the distinction existed: three laggards refused a sweep in
    which 21 of 24 chains were fresh, and no re-run could have recovered them.
    Off-session symbols are surfaced through ``symbols_off_session`` and the
    caller's ``::warning::`` instead.

    The separate guarantee that the partition is named for a session this
    sweep can actually identify lives in ``_session_from_quotes``, which
    requires an absolute majority -- that is what stops a handful of symbols
    defining a session between them.
    """
    required = max(1, math.ceil(requested * min_fraction))
    if responded >= required:
        return
    raise IncompleteSweepError(
        f"only {responded} of {requested} chains returned usable quotes for session "
        f"{session.isoformat()} (need {required}); refusing to create the partition, "
        "because bronze is immutable and a partial session can never be completed. "
        f"Missing: {', '.join(missing) if missing else '(unknown)'}. Re-run before the "
        "next US open -- and if the same names fail every day they are delisted or "
        "dead, and belong out of DEFAULT_SNAPSHOT_SYMBOLS rather than blocking every "
        "future session."
    )


def ingest_option_chain(
    store: LakeStore,
    symbols: Sequence[str],
    *,
    ingest_date: dt.date | None = None,
    fetch: Callable[[str], Mapping[str, Any]] | None = None,
    min_symbol_fraction: float = MIN_SYMBOL_FRACTION,
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
    # see the note in ``api/ingest_routes``. Falls back to today only when
    # the sweep produced nothing to read a session from.
    session = _session_from_quotes(valid) if not valid.empty else dt.date.today()
    valid, quarantined, off_session, newest_off = _quarantine_off_session_rows(
        valid, quarantined, session
    )
    off_set = set(off_session)
    # `ok` is appended on PARSE success, BEFORE validation, so a symbol whose
    # every row failed the schema counted as captured and counted toward the
    # floor while contributing nothing to the partition. Measured: nine
    # symbols served with a null ask produced `symbols_ok=24` on a partition
    # holding 15 -- the same 15-of-24 outcome the floor refuses by the fetch
    # route, reached silently by the validation route.
    #
    # They belong with the fetch failures, not with the laggards: a symbol
    # that returned unusable rows may well return usable ones on a re-run, so
    # the loss is recoverable and SHOULD count against the floor.
    landed = {str(u).upper() for u in valid["underlying"].unique()} if not valid.empty else set()
    failed = failed + [s for s in ok if s not in landed and s not in off_set]
    ok = [symbol for symbol in ok if symbol in landed]
    ingest_date = ingest_date or session

    # Order matters: the floor is checked ONLY when this run would create the
    # partition. On a re-run, weekend or holiday the write is a no-op anyway,
    # and raising there would turn a correct, benign outcome into a red alert.
    already = store.bronze_partition_exists(DATASET, ingest_date)
    if already and newest_off is not None and newest_off > session:
        # The majority elected a session we have ALREADY captured, while a
        # minority carried FRESHER quotes. Cboe serves per-symbol CDN
        # snapshots of wildly different ages -- measured 2026-09-21, the
        # `timestamp` field spanned ten hours across the universe -- so when
        # most chains are still serving yesterday, the majority vote elects
        # yesterday, `write_bronze` no-ops on its existing partition, and the
        # run reports "already captured" and exits 0. Today is then gone,
        # silently, with the fresh chains discarded as "off-session".
        #
        # Measured at 24 symbols with yesterday already in the lake: 13 stale
        # chains lost the day, 20 stale chains lost the day, both exit 0.
        # This is the one combination where the majority rule is worse than
        # the max() it replaced, so it is refused explicitly rather than
        # rebalanced -- the operator can re-run once the CDN catches up, and
        # the session is recoverable until the next US open.
        raise IncompleteSweepError(
            f"the majority of chains still report {session}, which is already captured, "
            f"but {len(off_session)} chain(s) carry quotes as new as {newest_off} "
            f"({', '.join(off_session)}); refusing rather than no-opping, because that "
            f"would silently discard {newest_off} and exit 0. Re-run once Cboe's "
            "per-symbol caches catch up, before the next US open."
        )
    if not already:
        _require_enough_symbols(
            len(ok) + len(off_session),
            len(symbols),
            min_symbol_fraction,
            session,
            missing=sorted(failed),
        )

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
        # The quarantine table carries a stale `__index_level_0__` column
        # from before write_bronze's reset_index fix, so a clean frame no
        # longer matches it. Repairing that table is queued in AGENT_TODO.md;
        # it must not hold the sweep hostage meanwhile.
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
        symbols_ok=tuple(ok),
        symbols_failed=tuple(failed),
        unparsed_contracts=unparsed_total,
        per_symbol_rows=per_symbol,
        committed=not already,
        symbols_off_session=off_session,
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
