"""Deribit's listed option books -> bronze (``docs/DATA_CONTRACTS.md`` #16,
``docs/adr/0032``).

Keyless: two public GETs per book, ``get_book_summary_by_currency`` (the
quotes) and ``get_instruments`` (strike, expiry, right and contract terms),
fetched in that order and joined on ``instrument_name``. The book comes first
so every book row has an instrument unless that name expires or is delisted
between the calls; the plan's name fallback handles those, and a name listed
between the calls is absorbed by the coverage rule's 1% tolerance.

This module is the fetch and the join, plus the ``--local`` writer. The
scheduled path never runs the writer: ``scripts/deribit_snapshot.py`` POSTs
the joined records to ``api/deribit_routes``, which holds the lake
credentials (``docs/adr/0020``'s credential seam). Both writers decide with
the contract's ``plan_deribit_write``.

Never imports ``tail_lab.config``: that would break the layers contract
(``config`` imports ``memory`` and ``feedback``). The caller hands in the
store, the blob store and the code revision.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, NamedTuple, cast

import requests

from tail_lab.contracts.deribit_options import (
    DERIBIT_BOOKS,
    DeribitApiError,
    DeribitBook,
    DeribitBookRow,
    DeribitInstrumentRow,
    DeribitRecord,
    DeribitRefusal,
    DeribitWritePlan,
    QuarantineParts,
    check_book_rows,
    check_instrument_rows,
    deribit_frame,
    earliest_observed_ms,
    plan_deribit_write,
    prepare_quarantine,
    quarantine_log_records,
    right_from_option_type,
)
from tail_lab.ingestion.json_payload import json_object
from tail_lab.ingestion.sources import is_transient_fetch_error, retry_transient
from tail_lab.lake.blob_store import BlobStore, QuarantineWrite, commit_then_quarantine
from tail_lab.lake.store import LAKE_READ_ERRORS, LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DERIBIT_API",
    "DeribitApiError",
    "DeribitIngestResult",
    "FetchedBook",
    "JoinedBook",
    "fetch_book",
    "ingest_deribit_options",
    "to_records",
]

_LOGGER = logging.getLogger(__name__)

DERIBIT_API: Final = "https://www.deribit.com/api/v2/public"
_HEADERS: Final = {"User-Agent": "Mozilla/5.0 (tail-lab ingestion bot)"}
_TIMEOUT_S: Final = 60
_FETCH_ATTEMPTS: Final = 3
_FETCH_BACKOFF_S: Final = 5.0

RUN_EVENT: Final = "ingestion.deribit_options.run"
UNWRITTEN_EVENT: Final = "ingestion.deribit_options.quarantine_unwritten"

#: Wire field -> book field. ``creation_timestamp``, ``quote_currency`` and
#: ``base_currency`` come from the BOOK only: an instruments row carries its
#: own ``creation_timestamp`` -- its listing time, days or weeks old -- and a
#: merge that let it win would stamp every row stale and refuse every book.
_FROM_BOOK: Final[Mapping[str, str]] = {
    "underlying": "base_currency",
    "premium_currency": "quote_currency",
    "bid": "bid_price",
    "ask": "ask_price",
    "mark": "mark_price",
    "mark_iv_pct": "mark_iv",
    "underlying_price": "underlying_price",
    "underlying_index": "underlying_index",
    "index_price": "estimated_delivery_price",
    "interest_rate": "interest_rate",
    "open_interest": "open_interest",
    "volume": "volume",
    "volume_usd": "volume_usd",
}
#: Wire field -> instruments field. ``right`` is mapped on its own.
_FROM_INSTRUMENT: Final[Mapping[str, str]] = {
    "expiration_timestamp_ms": "expiration_timestamp",
    "strike": "strike",
    "strike_currency": "counter_currency",
    "contract_size": "contract_size",
    "tick_size": "tick_size",
    "min_trade_amount": "min_trade_amount",
}


class FetchedBook(NamedTuple):
    """One book's two raw payloads (each the JSON-RPC ``result``), with how
    many GETs it took and how many bytes came back."""

    book: object
    instruments: object
    attempts: int
    bytes: int


class JoinedBook(NamedTuple):
    """``to_records``' output: the wire records, the listed names (from
    ``get_instruments``, for the coverage rule) and how many instruments rows
    were dropped as malformed."""

    records: list[DeribitRecord]
    listed: list[str]
    instruments_dropped: int


def _raise_for_status(resp: requests.Response) -> None:
    """``raise_for_status``, carrying the body of a refusal that will not be
    retried: Deribit's JSON-RPC reason is in it, and a bare "400 Bad
    Request" in the run log says nothing."""
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        if is_transient_fetch_error(exc):
            raise
        raise requests.HTTPError(f"{exc}: {resp.text[:200]}", response=resp) from exc


def _get_result(method: str, currency: str, attempts: list[int]) -> tuple[object, int]:
    """One public GET's ``result`` and its size, retried on 429/5xx and
    transport faults (and on a JSON decode error -- a Cloudflare HTML page or
    a truncated 200), never on a 4xx or a JSON-RPC error."""

    def call() -> tuple[object, int]:
        attempts[0] += 1
        resp = requests.get(
            f"{DERIBIT_API}/{method}",
            params={"currency": currency, "kind": "option"},
            headers=_HEADERS,
            timeout=_TIMEOUT_S,
        )
        _raise_for_status(resp)
        body = resp.json()
        try:
            payload = json_object(body, f"deribit {method}")
        except ValueError as exc:
            raise DeribitApiError(str(exc)) from exc
        if "error" in payload:
            raise DeribitApiError(f"deribit {method} error: {payload['error']!r:.200}")
        result = payload.get("result")
        if not result:
            raise DeribitApiError(f"deribit {method} returned an empty result for {currency}")
        return result, len(resp.content)

    return retry_transient(
        call,
        attempts=_FETCH_ATTEMPTS,
        backoff_s=_FETCH_BACKOFF_S,
        event="ingestion.deribit_options.retry",
        currency=currency,
    )


def fetch_book(currency: str) -> FetchedBook:
    """The book, then the instruments (the order the join relies on)."""
    attempts = [0]
    book, book_bytes = _get_result("get_book_summary_by_currency", currency, attempts)
    instruments, instrument_bytes = _get_result("get_instruments", currency, attempts)
    return FetchedBook(book, instruments, attempts[0], book_bytes + instrument_bytes)


def _record(book: DeribitBookRow, instrument: DeribitInstrumentRow | None) -> DeribitRecord:
    """One wire record, built field by field from the named source -- never
    by merging two dicts. A field fault becomes ``None`` plus an
    ``invalid_fields`` entry under the WIRE name."""
    invalid: list[str] = []

    def take(source: Mapping[str, object] | None, field: str, wire: str) -> object:
        if source is None:
            return None
        bad = cast("Mapping[str, object]", source["invalid"])
        if field in bad:
            invalid.append(f"{wire}={bad[field]!r:.40}")
            return None
        return source[field]

    out: dict[str, object] = {
        "instrument_name": book["instrument_name"],
        "creation_timestamp_ms": book["creation_timestamp"],
    }
    out |= {wire: take(book, field, wire) for wire, field in _FROM_BOOK.items()}
    out |= {wire: take(instrument, field, wire) for wire, field in _FROM_INSTRUMENT.items()}
    option_type = cast("str | None", take(instrument, "option_type", "right"))
    out["right"], known = right_from_option_type(option_type)
    if not known:
        invalid.append(f"right={option_type!r:.40}")
    out["invalid_fields"] = invalid or None
    return cast("DeribitRecord", out)


def to_records(book: object, instruments: object) -> JoinedBook:
    """Shape-check both payloads (the contract's one check), then join.

    A book row with no instrument keeps ``None`` instrument fields, and the
    schema quarantines it. ``listed`` comes from the instruments, so a name
    with no book row is still counted by the coverage rule.
    """
    book_rows = check_book_rows(book)
    instrument_rows, dropped = check_instrument_rows(instruments)
    by_name = {row["instrument_name"]: row for row in instrument_rows}
    records = [_record(row, by_name.get(row["instrument_name"])) for row in book_rows]
    return JoinedBook(records, [row["instrument_name"] for row in instrument_rows], dropped)


@dataclass(frozen=True)
class DeribitIngestResult:
    """One book's ``--local`` outcome, with the same field names as the
    route's response so the script judges both the same way."""

    dataset: str
    snapshot_date: dt.date
    rows: int
    quarantined: int
    expired_dropped: int
    committed: bool
    already_captured: bool
    previous_partition_exists: bool | None
    quarantine_share: float | None
    captured_observed_at_ms: int | None
    quarantine_key: str | None
    quarantine_written: bool | None
    bronze_path: str | None
    instruments_dropped: int


#: A lake read fault, or no such partition/column (``LookupError``/``KeyError``).
_CAPTURED_READ_ERRORS: tuple[type[Exception], ...] = (*LAKE_READ_ERRORS, LookupError)


def _captured_observed_at_ms(store: LakeStore, dataset: str, day: dt.date) -> int | None:
    """The stored partition D's earliest ``observed_at`` (ms), so the script
    can tell its own landed attempt from an earlier run's capture. Read only
    once D is known to exist, so the as-of resolution names D itself. A failed
    read is ``None`` -- the script then assumes the worst (red), it does not
    turn a green no-op into a 500."""
    try:
        return earliest_observed_ms(store.read_bronze_column_as_of(dataset, day, "observed_at"))
    except _CAPTURED_READ_ERRORS:
        _LOGGER.exception(
            "event=ingestion.deribit_options.captured_read_failed dataset=%s", dataset
        )
        return None


def _quarantine_write(parts: QuarantineParts | None) -> QuarantineWrite | None:
    """The capture's quarantine with this writer's log event bound."""
    if parts is None:
        return None
    return QuarantineWrite(
        parts.key,
        parts.doc,
        quarantine_log_records(parts.doc, parts.key),
        lambda line: log_event(_LOGGER, UNWRITTEN_EVENT, **line),
        lambda exc: _LOGGER.error(
            "event=ingestion.deribit_options.quarantine_write_failed capture=%s error=%s: %s",
            parts.key,
            type(exc).__name__,
            exc,
        ),
    )


def _plan(
    book: DeribitBook, joined: JoinedBook, store: LakeStore, now: dt.datetime
) -> DeribitWritePlan:
    return plan_deribit_write(
        deribit_frame(joined.records),
        currency=book.currency,
        listed=joined.listed,
        now=now,
        partition_exists=lambda day: store.bronze_partition_exists(book.dataset, day),
        has_earlier_partition=lambda day: any(
            d < day for d in store.bronze_ingest_dates(book.dataset)
        ),
    )


def ingest_deribit_options(
    currency: str,
    store: LakeStore,
    blobs: BlobStore,
    clock: Callable[[], dt.datetime],
    code_sha: str,
) -> DeribitIngestResult:
    """Fetch, join, plan and write one book -- the ``--local`` entry point.

    ``clock`` (timezone-aware) is read AFTER this book's fetch: a clock read
    once before all three fetches would let a slow BTC fetch push ETH's and
    USDC's ``observed_at`` past the 5-minute future bound and refuse them.
    Raises ``DeribitRefusal``, ``DeribitApiError``, a ``requests`` fault or
    a lake error -- each costs this book only (``scripts/deribit_snapshot``).
    """
    book = DERIBIT_BOOKS[currency.upper()]
    started = time.monotonic()
    fetched = fetch_book(book.currency)
    joined = to_records(fetched.book, fetched.instruments)
    fields: dict[str, object] = {
        "currency": book.currency,
        "dataset": book.dataset,
        "attempts": fetched.attempts,
        "bytes": fetched.bytes,
        "rows_fetched": len(joined.records),
        "instruments_dropped": joined.instruments_dropped,
    }
    fields["observed_min_ms"], fields["observed_max_ms"] = _observed_span(joined.records)
    try:
        plan = _plan(book, joined, store, clock())
        parts = prepare_quarantine(
            dataset=book.dataset, plan=plan, records=joined.records, code_sha=code_sha
        )
    except DeribitRefusal as exc:
        log_event(
            _LOGGER,
            RUN_EVENT,
            **fields,
            refusal=f"{exc.status}: {exc.detail}",
            duration_s=round(time.monotonic() - started, 2),
        )
        raise
    bronze_path, written = (
        (None, None)
        if plan.already_captured
        else commit_then_quarantine(
            store,
            blobs,
            dataset=book.dataset,
            ingest_date=plan.snapshot_date,
            valid=plan.valid,
            quarantine=_quarantine_write(parts),
        )
    )
    result = DeribitIngestResult(
        dataset=book.dataset,
        snapshot_date=plan.snapshot_date,
        rows=len(plan.valid) if bronze_path is not None else 0,
        quarantined=len(plan.quarantined),
        expired_dropped=plan.expired_dropped,
        committed=bronze_path is not None,
        already_captured=plan.already_captured,
        previous_partition_exists=plan.previous_partition_exists,
        quarantine_share=plan.quarantine_share,
        captured_observed_at_ms=(
            _captured_observed_at_ms(store, book.dataset, plan.snapshot_date)
            if plan.already_captured
            else None
        ),
        quarantine_key=parts.key if parts is not None else None,
        quarantine_written=written,
        bronze_path=bronze_path,
        instruments_dropped=joined.instruments_dropped,
    )
    log_event(
        _LOGGER,
        RUN_EVENT,
        **fields,
        valid=len(plan.valid),
        quarantined=result.quarantined,
        expired_dropped=result.expired_dropped,
        partition=_partition_outcome(result),
        quarantine_key=result.quarantine_key,
        quarantine_written=written,
        quarantine_share=result.quarantine_share,
        previous_partition_exists=result.previous_partition_exists,
        code_sha=code_sha,
        duration_s=round(time.monotonic() - started, 2),
    )
    return result


def _partition_outcome(result: DeribitIngestResult) -> str:
    """The run log's ``partition``: written, a no-op, or none."""
    if result.committed:
        return result.snapshot_date.isoformat()
    if result.already_captured:
        return "no-op, exists"
    return "none (every row quarantined)"


def _observed_span(records: list[DeribitRecord]) -> tuple[str, str]:
    """The capture's earliest and latest ``creation_timestamp`` (ms, as
    Deribit sent it) for the run log."""
    stamps = [r["creation_timestamp_ms"] for r in records]
    return str(min(stamps, default=None)), str(max(stamps, default=None))
