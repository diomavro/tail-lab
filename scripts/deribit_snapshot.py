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
still sends the others: each book's session is lost on its own. Every
per-book line is prefixed ``btc:``, ``eth:`` or ``usdc:``.

Exit codes are the contract with the scheduler. 0 only if every book
committed or was already captured, with no committed answer missing the
previous day, no quarantine share above 1%, and no quarantine left
unwritten. An "already captured" answer to a retry whose earlier attempt
failed after sending (``may_have_landed``) is red too -- that attempt's
quarantine share and blob status are unknown -- but only when the stored
partition is THIS body's capture (its ``captured_observed_at_ms`` lies within
the body's own range of ``creation_timestamp_ms``, or the app does not say).
A day captured by an earlier run is a plain green no-op. 2 for a missing
``--token``. Otherwise 1.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Protocol

import requests
import urllib3
from pydantic import ValidationError

from tail_lab.api.schemas import DeribitSnapshotResponse
from tail_lab.contracts.deribit_options import (
    DERIBIT_BOOKS,
    MAX_QUARANTINE_SHARE,
    REFUSAL_MARKER,
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
    def dataset(self) -> str: ...
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
    @property
    def captured_observed_at_ms(self) -> int | None: ...


def _report(label: str, answer: _Answer, *, may_have_landed: bool) -> bool:
    """Say what happened to the dataset; ``False`` when nothing landed, or
    when a landed attempt's quarantine is unknown."""
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
        if may_have_landed and answer.captured_observed_at_ms is None:
            print(
                f"::error::{label}: the app could not say which run captured this day (see "
                "captured_read_failed in the app log); treating it as this run's, whose "
                f"quarantine is unknown -- check ops/quarantine/{answer.dataset}/"
                f"{answer.snapshot_date}/",
                file=sys.stderr,
            )
            return False
        if may_have_landed:
            # Red, not a warning: the landed attempt's response was lost, so
            # whether it quarantined over 1% or failed its blob write is
            # unknown -- and the catch-up will only ever answer "already
            # captured" for this day.
            print(
                f"::error::{label}: quarantine share and blob status of the landed attempt "
                f"are unknown; check ops/quarantine/{answer.dataset}/{answer.snapshot_date}/ "
                "and the app log for event=api.ingest.deribit_options.quarantine_unwritten",
                file=sys.stderr,
            )
            return False
        return True
    print(
        f"::error::{label}: nothing committed -- every live row was quarantined "
        f"({answer.quarantined}); a later run will retry",
        file=sys.stderr,
    )
    return False


#: The earliest and latest ``creation_timestamp_ms`` of the body this run sent.
ObservedRange = tuple[int, int]


def _own_attempt_may_have_landed(
    label: str, answer: _Answer, *, may_have_landed: bool, own_range: ObservedRange | None
) -> bool:
    """``may_have_landed``, narrowed: an "already captured" answer whose
    stored partition began OUTSIDE this body's observation range was captured
    by an earlier run -- the routine 10:45 catch-up after a green 08:15 -- so
    a transient first-attempt failure landed nothing, and is not an alarm.

    A range, not the body's minimum: a live book's ``creation_timestamp``
    values spread over 17-89 ms (measured 2026-10-30), and when every row at the
    body's earliest millisecond is quarantined or dropped as expired, the
    stored partition begins a few ms LATER than the body. An earlier run's
    fetch is minutes to hours earlier, so the two never overlap. Unknown (an
    older app, a failed read) stays "may have"."""
    captured = answer.captured_observed_at_ms
    if not (may_have_landed and answer.already_captured) or captured is None:
        return may_have_landed
    if own_range is None or own_range[0] <= captured <= own_range[1]:
        return True
    print(
        f"{label}: an attempt failed after sending, but the day was captured by an earlier "
        f"run (its earliest observation is {captured} ms; this body spans {own_range} ms)"
    )
    return False


def _judge(
    label: str, answer: _Answer, *, may_have_landed: bool, own_range: ObservedRange | None
) -> bool:
    """Whether this book leaves the run green (see the module docstring)."""
    may_have_landed = _own_attempt_may_have_landed(
        label, answer, may_have_landed=may_have_landed, own_range=own_range
    )
    ok = _report(label, answer, may_have_landed=may_have_landed)
    if answer.previous_partition_exists is False and (answer.committed or may_have_landed):
        print(
            f"::error::{label}: the day before {answer.snapshot_date} was never captured",
            file=sys.stderr,
        )
        ok = False
    elif answer.previous_partition_exists is False and answer.already_captured:
        # Another run captured this day and went red for the missing day
        # then; name it here too, so it is not only in that run's log.
        print(
            f"::warning::{label}: the day before {answer.snapshot_date} was never captured "
            "(the run that captured this day went red for it)"
        )
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


def _causes(exc: BaseException) -> Iterator[BaseException]:
    """``exc`` and everything it wraps: ``__cause__``/``__context__``,
    exception ``args`` and urllib3's ``.reason`` (how ``MaxRetryError``
    carries the connection error)."""
    seen: set[int] = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        nested = [current.__cause__, current.__context__, getattr(current, "reason", None)]
        stack.extend(n for n in [*nested, *current.args] if isinstance(n, BaseException))


_DESCRIBE_LIMIT = 300


def describe(exc: BaseException) -> str:
    """``exc``'s own message, then its INNERMOST cause (following
    ``__cause__`` and the implicit ``__context__``, with a cycle guard).

    Both halves matter: the outer message carries Deribit's 4xx body
    (``_raise_for_status`` appends it and re-raises ``from`` the original),
    the root carries why a connection failed. A long result is cut in the
    MIDDLE, so neither end is lost -- ``{exc!r:.200}`` cut the root off.
    """
    seen = {id(exc)}
    root = exc
    while (inner := root.__cause__ or root.__context__) is not None and id(inner) not in seen:
        seen.add(id(inner))
        root = inner
    text = f"{type(exc).__name__}: {exc}"
    if root is not exc:
        text = f"{text} <- {type(root).__name__}: {root}"
    if len(text) <= _DESCRIBE_LIMIT:
        return text
    head = _DESCRIBE_LIMIT // 2
    return f"{text[:head]} ... {text[-(_DESCRIBE_LIMIT - head - 5) :]}"


def _deterministic_refusal(exc: requests.RequestException) -> bool:
    """A 5xx the APP raised on purpose (``DeribitRefusal``, detail starting
    :data:`REFUSAL_MARKER`): it wrote nothing and would refuse again."""
    if not isinstance(exc, requests.HTTPError) or exc.response is None:
        return False
    try:
        detail = exc.response.json().get("detail")
    except (ValueError, AttributeError):
        return False
    return isinstance(detail, str) and detail.startswith(REFUSAL_MARKER)


def _retryable(exc: requests.RequestException) -> bool:
    """A 5xx, a 429 or a transport fault. Never a JSON error: a NaN the
    client refuses to encode, or a 200 whose body does not parse, cannot be
    fixed by re-sending -- and never the app's own deterministic refusal."""
    if isinstance(exc, requests.exceptions.InvalidJSONError) or _deterministic_refusal(exc):
        return False
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, requests.ConnectionError | requests.Timeout)


def _failed_after_send(exc: requests.RequestException) -> bool:
    """Whether a retried failure may have followed a commit. A 429 is refused
    before any work, and a connect timeout or a refused connection (urllib3's
    ``NewConnectionError`` in the chain) never reached the app; anything else
    -- including a ``ConnectionError``, which in ``requests`` also covers a
    disconnect after the body went out -- may have."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code != 429
    if any(isinstance(e, urllib3.exceptions.NewConnectionError) for e in _causes(exc)):
        return False
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
                f"::warning::{label}: attempt {attempt}/{POST_ATTEMPTS} failed ({describe(exc)}); "
                f"retrying in {wait}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise AssertionError("POST_ATTEMPTS must be >= 1")


#: What a refusal with this status means for the operator, checked first.
_STATUS_HINTS: dict[int, str] = {
    425: (
        "too early, re-run after 08:05 UTC; if this is a catch-up after 00:00 UTC, check "
        "/api/ingest/deribit-options/status for yesterday instead"
    ),
    # A 429 is refused before any work. Only an earlier 5xx in the same run
    # could have landed, which the status route answers.
    429: (
        "rate-limited: retries exhausted (a 429 lands nothing; if an earlier attempt "
        "was a 5xx, check /api/ingest/deribit-options/status)"
    ),
    404: "app rejected the book: route not deployed yet? (check deploy.yml) or token unset on the app",
}


def _refused(exc: requests.HTTPError, label: str) -> bool:
    status = exc.response.status_code if exc.response is not None else None
    text = exc.response.text[:400] if exc.response is not None else ""
    if status in _STATUS_HINTS:
        why = _STATUS_HINTS[status]
    elif _deterministic_refusal(exc):
        why = "the app refused the book (deterministic, nothing written)"
    elif status is not None and status >= 500:
        why = "retries exhausted (may have landed; check /api/ingest/deribit-options/status)"
    else:
        why = "app rejected the book"
    print(f"::error::{label}: {why}: HTTP {status} {text}", file=sys.stderr)
    return False


def _fetch(currency: str, label: str) -> ingestion.JoinedBook | None:
    """Fetch and join one book; ``None`` (annotated) when the fetch failed."""
    try:
        fetched = ingestion.fetch_book(currency)
        joined = ingestion.to_records(fetched.book, fetched.instruments)
    except (requests.RequestException, DeribitApiError) as exc:
        print(f"::error::{label}: could not fetch the book: {describe(exc)}", file=sys.stderr)
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
        print(f"::error::{label}: the POST failed: {describe(exc)}", file=sys.stderr)
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
    stamps = [int(r["creation_timestamp_ms"]) for r in joined.records]
    own_range = (min(stamps), max(stamps))
    return _judge(label, response, may_have_landed=may_have_landed, own_range=own_range)


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
        print(f"::error::{label}: could not fetch the book: {describe(exc)}", file=sys.stderr)
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
    # --local sends nothing that "may have landed", so it has no range to
    # compare: its own capture is the only one this call can be about.
    return _judge(label, result, may_have_landed=False, own_range=None)


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
