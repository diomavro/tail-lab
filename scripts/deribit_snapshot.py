#!/usr/bin/env python3
"""Drive the daily Deribit capture (``docs/adr/0032``).

A script, not a module inside ``tail_lab``, for the same reason as
``chain_snapshot.py``: it is workflow glue, the piece that knows the capture
is split across two processes (fetch here, write in the app).

Two modes, one rulebook (the contract's ``plan_deribit_write``):

    python scripts/deribit_snapshot.py --post https://tail-lab.fly.dev --token T
        Fetch each book keylessly, join it, and hand it to the live app, which
        owns the lake keys and does the write. This is what the scheduled
        workflow runs.

    python scripts/deribit_snapshot.py --local
        Fetch and write straight to the configured lake. For a human at a
        terminal. It runs outside the app's per-book lock, so a production
        ``--local`` run must never overlap the cron.

Books are handled one at a time -- BTC, ETH, USDC -- and one book failing
still sends the others: each book's session is lost on its own. Every line
is prefixed ``btc:``, ``eth:`` or ``usdc:``.

Exit codes are the contract with the scheduler. 0 only if every book
committed or was already captured, with no committed (or possibly landed)
answer missing the previous day, no quarantine share above 1%, and no
quarantine left unwritten. 2 for a missing ``--token``. Otherwise 1.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol

import requests
from pydantic import ValidationError

from tail_lab.api.schemas import DeribitSnapshotResponse
from tail_lab.contracts.deribit_options import (
    DERIBIT_BOOKS,
    MAX_QUARANTINE_SHARE,
    DeribitApiError,
    DeribitRefusal,
)
from tail_lab.ingestion import deribit_options as ingestion
from tail_lab.observability import configure_logging

if TYPE_CHECKING:
    from tail_lab.lake.blob_store import BlobStore
    from tail_lab.lake.store import LakeStore

_LOGGER = logging.getLogger("deribit_snapshot")

BOOK_PATH = "/api/ingest/deribit-options/{currency}"

#: Four POST attempts with a 120 s timeout and 15/60/180 s backoff: about
#: 12.3 min of POST at worst. Only the instruments GET comes after the
#: capture's ``observed_at``, so freshness sees about 15.4 min at worst --
#: inside the app's 30-minute stale bound. Not shared with
#: ``chain_snapshot.py``'s loop, whose tests patch its module globals
#: (unifying them is queued in ``AGENT_TODO.md``).
POST_ATTEMPTS = 4
POST_BACKOFF_S = (15, 60, 180)
POST_TIMEOUT_S = 120


def _utcnow() -> dt.datetime:
    """The clock ``--local`` stamps each book against (aware UTC). Looked up
    at call time by ``main``, so a test can pin it."""
    return dt.datetime.now(dt.UTC)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--post", metavar="BASE_URL", help="hand each book to the live app")
    mode.add_argument("--local", action="store_true", help="write to the configured lake")
    parser.add_argument("--token", default="", help="bearer token for --post")
    parser.add_argument("--timeout", type=int, default=POST_TIMEOUT_S)
    return parser.parse_args(argv)


def book_url(base_url: str, currency: str) -> str:
    """The route for one book. The path segment is lowercase; the route
    upper-cases it back to the contract's key."""
    return f"{base_url.rstrip('/')}{BOOK_PATH.format(currency=currency.lower())}"


def book_body(joined: ingestion.JoinedBook) -> dict[str, object]:
    """The request body ``--post`` sends: the joined rows and every listed
    name. The route forbids extra keys, so a misnamed field is a 422."""
    return {"rows": joined.records, "listed": joined.listed}


class _Answer(Protocol):
    """What both modes' answers share: the route's response and the
    ``--local`` result carry the same field names."""

    @property
    def committed(self) -> bool: ...
    @property
    def already_captured(self) -> bool: ...
    @property
    def rows(self) -> int: ...
    @property
    def quarantined(self) -> int: ...
    @property
    def snapshot_date(self) -> dt.date: ...
    @property
    def bronze_path(self) -> str | None: ...
    @property
    def previous_partition_exists(self) -> bool | None: ...
    @property
    def quarantine_share(self) -> float | None: ...
    @property
    def quarantine_written(self) -> bool | None: ...
    @property
    def quarantine_key(self) -> str | None: ...


def _report(label: str, answer: _Answer, *, may_have_landed: bool) -> bool:
    """Say what happened to the dataset; ``False`` when nothing landed."""
    if answer.committed:
        print(
            f"{label}: committed {answer.rows} rows "
            f"(snapshot {answer.snapshot_date}) -> {answer.bronze_path}"
        )
        return True
    if answer.already_captured:
        how = (
            "landed (an earlier attempt failed after sending and may have committed it)"
            if may_have_landed
            else "NO-OP: already captured, wrote nothing"
        )
        print(f"{label}: {how}: snapshot {answer.snapshot_date}")
        return True
    print(
        f"::error::{label}: nothing committed -- every live row was quarantined "
        f"({answer.quarantined}); the catch-up will retry",
        file=sys.stderr,
    )
    return False


def _judge(label: str, answer: _Answer, *, may_have_landed: bool) -> bool:
    """Whether this book leaves the run green (see the module docstring)."""
    ok = _report(label, answer, may_have_landed=may_have_landed)
    if answer.previous_partition_exists is False and (answer.committed or may_have_landed):
        print(
            f"::error::{label}: the day before {answer.snapshot_date} was never captured",
            file=sys.stderr,
        )
        ok = False
    share = answer.quarantine_share
    if share is not None and share > MAX_QUARANTINE_SHARE:
        print(
            f"::error::{label}: {answer.quarantined} rows quarantined ({share:.1%}, "
            f"over {MAX_QUARANTINE_SHARE:.0%}) -> {answer.quarantine_key}",
            file=sys.stderr,
        )
        ok = False
    elif answer.quarantined:
        print(f"::warning::{label}: {answer.quarantined} rows quarantined")
    if answer.quarantine_written is False:
        print(
            f"::error::{label}: the quarantine blob was not written; its rows are only in "
            f"the log (capture {answer.quarantine_key})",
            file=sys.stderr,
        )
        ok = False
    return ok


# ---- --post ------------------------------------------------------------------


def _post_once(url: str, token: str, body: Mapping[str, object], timeout: int) -> object:
    resp = requests.post(
        url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=timeout
    )
    resp.raise_for_status()
    answer: object = resp.json()
    return answer


def _retryable(exc: requests.RequestException) -> bool:
    """A 5xx, a 429 or a transport fault. Never a JSON error: a NaN the
    client refuses to encode, or a 200 whose body does not parse, cannot be
    fixed by re-sending."""
    if isinstance(exc, requests.exceptions.InvalidJSONError):
        return False
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, requests.ConnectionError | requests.Timeout)


def _failed_after_send(exc: requests.RequestException) -> bool:
    """Whether a retried failure may have followed a commit. A 429 is refused
    before any work, and a connect timeout never reached the app; anything
    else -- including a ``ConnectionError``, which in ``requests`` also covers
    a disconnect after the body went out -- may have."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code != 429
    return not isinstance(exc, requests.exceptions.ConnectTimeout)


def _post(
    url: str, token: str, body: Mapping[str, object], timeout: int, label: str
) -> tuple[object, bool]:
    """POST one book, retrying what a retry could fix: ``(answer,
    may_have_landed)``."""
    may_have_landed = False
    for attempt in range(1, POST_ATTEMPTS + 1):
        try:
            return _post_once(url, token, body, timeout), may_have_landed
        except requests.RequestException as exc:
            if not _retryable(exc) or attempt == POST_ATTEMPTS:
                raise
            may_have_landed = may_have_landed or _failed_after_send(exc)
            wait = POST_BACKOFF_S[min(attempt - 1, len(POST_BACKOFF_S) - 1)]
            print(
                f"::warning::{label}: attempt {attempt}/{POST_ATTEMPTS} failed ({exc!r:.200}); "
                f"retrying in {wait}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise AssertionError("POST_ATTEMPTS must be >= 1")


def _refused(exc: requests.HTTPError, label: str) -> bool:
    status = exc.response.status_code if exc.response is not None else None
    text = exc.response.text[:400] if exc.response is not None else ""
    if status == 425:
        print(f"::error::{label}: too early, re-run after 08:05 UTC: {text}", file=sys.stderr)
    else:
        print(f"::error::{label}: app rejected the book: HTTP {status} {text}", file=sys.stderr)
    return False


def _fetch(currency: str, label: str) -> ingestion.JoinedBook | None:
    """Fetch and join one book; ``None`` (annotated) when the fetch failed."""
    try:
        fetched = ingestion.fetch_book(currency)
        joined = ingestion.to_records(fetched.book, fetched.instruments)
    except (requests.RequestException, DeribitApiError) as exc:
        print(f"::error::{label}: could not fetch the book: {exc!r:.300}", file=sys.stderr)
        return None
    print(
        f"{label}: fetched {len(joined.records)} rows, {len(joined.listed)} listed, "
        f"instruments_dropped={joined.instruments_dropped} "
        f"({fetched.attempts} requests, {fetched.bytes} bytes)"
    )
    return joined


def _post_book(args: argparse.Namespace, currency: str) -> bool:
    """Fetch, join and POST one book; every failure here costs only it."""
    label = currency.lower()
    joined = _fetch(currency, label)
    if joined is None:
        return False
    try:
        answer, may_have_landed = _post(
            book_url(args.post, currency), args.token, book_body(joined), args.timeout, label
        )
    except requests.HTTPError as exc:
        return _refused(exc, label)
    except requests.RequestException as exc:
        print(f"::error::{label}: the POST failed: {exc!r:.300}", file=sys.stderr)
        return False
    try:
        response = DeribitSnapshotResponse.model_validate(answer)
    except ValidationError as exc:
        print(f"::error::{label}: the app's answer is not a result: {exc}", file=sys.stderr)
        return False
    if response.dataset != DERIBIT_BOOKS[currency].dataset:
        print(
            f"::error::{label}: the app wrote {response.dataset}, "
            f"not {DERIBIT_BOOKS[currency].dataset}",
            file=sys.stderr,
        )
        return False
    return _judge(label, response, may_have_landed=may_have_landed)


# ---- --local -------------------------------------------------------------------


def _local_book(
    currency: str,
    store: LakeStore,
    blobs: BlobStore,
    clock: Callable[[], dt.datetime],
    code_sha: str,
) -> bool:
    """One book under ``--local``, with the same failure classes as
    ``--post``. Anything else is a bug and ends the run."""
    from tail_lab.lake.store import LAKE_WRITE_ERRORS

    label = currency.lower()
    try:
        result = ingestion.ingest_deribit_options(currency, store, blobs, clock, code_sha)
    except (requests.RequestException, DeribitApiError) as exc:
        print(f"::error::{label}: could not fetch the book: {exc!r:.300}", file=sys.stderr)
        return False
    except DeribitRefusal as exc:
        hint = ", re-run after 08:05 UTC" if exc.status == 425 else ""
        print(f"::error::{label}: refused ({exc.status}{hint}): {exc.detail}", file=sys.stderr)
        return False
    except LAKE_WRITE_ERRORS as exc:
        _LOGGER.exception("event=deribit_snapshot.lake_failed currency=%s", currency)
        print(f"::error::{label}: the lake read or write failed: {exc!r}", file=sys.stderr)
        return False
    print(f"{label}: instruments_dropped={result.instruments_dropped}")
    return _judge(label, result, may_have_landed=False)


def _run_local(clock: Callable[[], dt.datetime]) -> int:
    # Imported here, not at module scope: --post must stay importable (and
    # runnable in CI) without any lake configuration.
    from tail_lab.config import get_lake_store, get_quarantine_blobs, get_settings

    store, blobs = get_lake_store(), get_quarantine_blobs()
    code_sha = get_settings().code_sha
    oks = [_local_book(currency, store, blobs, clock, code_sha) for currency in DERIBIT_BOOKS]
    return 0 if all(oks) else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    # Before any fetch or log line: without it the --local run log and the
    # quarantine_unwritten lines -- the only copy of the rows when a blob
    # write fails -- would be dropped by Python's last-resort handler.
    configure_logging()
    if args.local:
        return _run_local(_utcnow)
    if not args.token:
        print("::error::--post needs --token", file=sys.stderr)
        return 2
    oks = [_post_book(args, currency) for currency in DERIBIT_BOOKS]
    return 0 if all(oks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
