"""The write endpoint for the daily Deribit capture, and its freshness check
(``docs/adr/0032``, ``docs/DATA_CONTRACTS.md`` #16).

Same seam as the Cboe chain (``api/ingest_routes``, ``docs/adr/0020``): the
scheduled workflow fetches Deribit's books with no credentials at all and
hands each book here, and the app -- which already holds the lake keys --
does the write. One route per book, never a batch, so one book's failure
cannot cost another's session.

A module of its own rather than more of ``ingest_routes``: the rules here are
the contract's ``plan_deribit_write``, shared with the ``--local`` writer, and
nothing about them is shared with the Cboe routes.

**Concurrency.** Each dataset has its own lock around the whole plan and both
writes. Without it two retried POSTs both pass the exists-check and both
append (reproduced 20 times out of 20 in review). The lock holds only within
one process: the single-machine deploy is a precondition (``docs/adr/0032``).
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections.abc import Mapping, Sequence

from fastapi import APIRouter, Depends, Header, HTTPException

from tail_lab.api.auth import require_bearer_token
from tail_lab.api.schemas import (
    DeribitBookStatus,
    DeribitSnapshotRequest,
    DeribitSnapshotResponse,
    DeribitSnapshotStatus,
)
from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.config import get_quarantine_blobs as _get_configured_quarantine_blobs
from tail_lab.config import get_settings
from tail_lab.contracts.deribit_options import (
    DERIBIT_BOOKS,
    REFUSAL_MARKER,
    DeribitBook,
    DeribitRefusal,
    QuarantineParts,
    deribit_frame,
    plan_deribit_write,
    prepare_quarantine,
    quarantine_log_records,
)
from tail_lab.lake.blob_store import BlobStore, QuarantineWrite, commit_then_quarantine
from tail_lab.lake.store import LakeStore, earliest_in_partition_ms
from tail_lab.observability import log_event

router = APIRouter()

_LOGGER = logging.getLogger(__name__)

EVENT = "api.ingest.deribit_options"
UNWRITTEN_EVENT = "api.ingest.deribit_options.quarantine_unwritten"

_LOCKS: dict[str, threading.Lock] = {
    book.dataset: threading.Lock() for book in DERIBIT_BOOKS.values()
}


def _utcnow() -> dt.datetime:
    """The server clock (aware UTC), as a seam tests replace."""
    return dt.datetime.now(dt.UTC)


def get_lake_store() -> LakeStore:
    # Overridden in tests via dependency_overrides (which bypasses this call).
    return _get_configured_lake_store()


def get_quarantine_blobs() -> BlobStore:
    # Overridden in tests, on the same tmp root as get_lake_store.
    return _get_configured_quarantine_blobs()


@router.post("/api/ingest/deribit-options/{currency}")
def ingest_deribit_options(
    currency: str,
    body: DeribitSnapshotRequest,
    authorization: str | None = Header(default=None),
    store: LakeStore = Depends(get_lake_store),
    blobs: BlobStore = Depends(get_quarantine_blobs),
) -> DeribitSnapshotResponse:
    """Commit one Deribit book's capture. Auth first (unset 404, wrong 401),
    then the book lookup (unknown 404); a malformed body is a 422 before
    either, as on the chain route."""
    require_bearer_token(authorization)
    book = DERIBIT_BOOKS.get(currency.upper())
    if book is None:
        raise HTTPException(status_code=404, detail=f"no Deribit book {currency!r}")
    records = [row.model_dump(mode="json") for row in body.rows]
    with _LOCKS[book.dataset]:
        try:
            return _ingest(book, records, body.listed, store, blobs)
        except DeribitRefusal as exc:
            log_event(_LOGGER, EVENT, dataset=book.dataset, refusal=exc.status, detail=exc.detail)
            raise HTTPException(
                status_code=exc.status, detail=f"{REFUSAL_MARKER}{exc.detail}"
            ) from exc


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
            "event=api.ingest.deribit_options.quarantine_write_failed capture=%s error=%s: %s",
            parts.key,
            type(exc).__name__,
            exc,
        ),
    )


def _ingest(
    book: DeribitBook,
    records: Sequence[Mapping[str, object]],
    listed: Sequence[str],
    store: LakeStore,
    blobs: BlobStore,
) -> DeribitSnapshotResponse:
    started = time.monotonic()
    code_sha = get_settings().code_sha
    plan = plan_deribit_write(
        deribit_frame(records),
        currency=book.currency,
        listed=listed,
        now=_utcnow(),
        partition_exists=lambda day: store.bronze_partition_exists(book.dataset, day),
        has_earlier_partition=lambda day: any(
            d < day for d in store.bronze_ingest_dates(book.dataset)
        ),
    )
    parts = prepare_quarantine(dataset=book.dataset, plan=plan, records=records, code_sha=code_sha)
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
    response = DeribitSnapshotResponse(
        dataset=book.dataset,
        snapshot_date=plan.snapshot_date,
        rows=len(plan.valid) if bronze_path is not None else 0,
        quarantined=len(plan.quarantined),
        quarantine_key=parts.key if parts is not None else None,
        quarantine_written=written,
        bronze_path=bronze_path,
        expired_dropped=plan.expired_dropped,
        committed=bronze_path is not None,
        already_captured=plan.already_captured,
        previous_partition_exists=plan.previous_partition_exists,
        quarantine_share=plan.quarantine_share,
        captured_observed_at_ms=(
            earliest_in_partition_ms(
                store,
                book.dataset,
                plan.snapshot_date,
                "observed_at",
                event="api.ingest.deribit_options.captured_read_failed",
            )
            if plan.already_captured
            else None
        ),
    )
    log_event(
        _LOGGER,
        EVENT,
        currency=book.currency,
        **response.model_dump(exclude={"snapshot_date"}),
        snapshot_date=plan.snapshot_date,
        rows_posted=len(records),
        listed=len(listed),
        code_sha=code_sha,
        duration_s=round(time.monotonic() - started, 2),
    )
    return response


@router.get("/api/ingest/deribit-options/status")
def deribit_options_status(store: LakeStore = Depends(get_lake_store)) -> DeribitSnapshotStatus:
    """How stale each book's collection is -- public, read by the daily agent
    before it picks any work (``docs/AGENT_MISSION.md``). For a 24/7 market a
    ``stale_days`` above 1 is a lost session."""
    today = _utcnow().date()
    return DeribitSnapshotStatus(
        books={
            currency.lower(): _book_status(store.bronze_ingest_dates(book.dataset), today)
            for currency, book in DERIBIT_BOOKS.items()
        }
    )


def _book_status(dates: list[dt.date], today: dt.date) -> DeribitBookStatus:
    if not dates:
        return DeribitBookStatus(
            first_snapshot_date=None,
            last_snapshot_date=None,
            stale_days=None,
            partitions=0,
            missing_days=None,
        )
    first, last = dates[0], dates[-1]
    return DeribitBookStatus(
        first_snapshot_date=first,
        last_snapshot_date=last,
        stale_days=(today - last).days,
        partitions=len(dates),
        missing_days=(last - first).days + 1 - len(dates),
    )
