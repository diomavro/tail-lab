#!/usr/bin/env python3
"""Drive the daily forward-collection sweep (``docs/adr/0020``).

Deliberately a script and not a module inside ``tail_lab``: it is workflow
glue, and it is the piece that knows the sweep is split across two processes
(fetch here, write in the app). Keeping that knowledge out of
``tail_lab.ingestion`` leaves the adapter itself a plain source -> bronze
adapter that ``make ingest-option-chain`` can drive locally with no HTTP at
all.

Two modes, one code path:

    python scripts/chain_snapshot.py --post https://tail-lab.fly.dev
        Fetch + slice locally (no credentials needed), then hand the rows to
        the live app, which owns the object-storage keys and does the write.
        This is what the scheduled workflow runs.

    python scripts/chain_snapshot.py --local
        Fetch + slice + write straight to the configured lake. For a human
        at a terminal with credentials already in the environment.

Both modes fetch each chain ONCE and write two wings (``docs/adr/0031``): the
puts to ``option_chain_snapshot``, then the calls to
``option_chain_call_snapshot``. Every per-wing line, retries included, is
prefixed ``puts:`` or ``calls:``.

Exit codes are the contract with the scheduler: non-zero whenever a session
may be lost -- a sweep that returns nothing, too few chains, a feed behind the
market, or EITHER wing failing to land. 0 means both wings landed (or were
already captured), or the session is still trading and the previous session
is confirmed in the PUT lake. The calls, sliced from the same payloads, are
then not attempted, so that 0 vouches for the put dataset only: a call
session lost earlier went red on the run that lost it (``docs/adr/0031``
§6). A put failure this script recognises (an HTTP refusal, a transport
fault, a refused or failed lake write) still sends the calls (each session
is unrecoverable); an unforeseen exception is a bug and ends the run. A
call-only failure prints ``calls only; puts landed`` so an alert can tell it
from a lost put session.
"""

from __future__ import annotations

import argparse
import datetime as dt
import enum
import http.client
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from pydantic import ValidationError

from tail_lab.api.schemas import OptionChainSnapshotResponse
from tail_lab.contracts.option_chain import DEFAULT_SNAPSHOT_SYMBOLS, WINGS, Right
from tail_lab.ingestion.option_chain import latest_market_session, sweep_to_records
from tail_lab.observability import configure_logging

if TYPE_CHECKING:
    from tail_lab.ingestion.option_chain import IngestResult

_LOGGER = logging.getLogger("chain_snapshot")

#: Below this, assume something broke upstream rather than that the market
#: genuinely had nothing to quote across two dozen liquid chains. Applied to
#: each wing separately.
MIN_PLAUSIBLE_ROWS = 200

#: The two write routes (``api/ingest_routes``), one per wing.
PUT_PATH = "/api/ingest/option-chain"
CALL_PATH = "/api/ingest/option-chain-calls"
_PATH: dict[Right, str] = {"P": PUT_PATH, "C": CALL_PATH}
_LABEL: dict[Right, str] = {"P": "puts", "C": "calls"}

#: Printed by both modes when the puts are SKIPPED, so the calls' absence is
#: stated rather than left for the operator to infer.
_CALLS_NOT_ATTEMPTED = "calls: not attempted -- the session is still trading"


class Outcome(enum.Enum):
    """What one wing's write came to. ``SKIPPED`` is the session still
    trading, and only the puts produce it: an unsettled call wing is
    ``UNSETTLED``, a failure (see :func:`_unsettled`)."""

    SKIPPED = "skipped"
    OK = "ok"
    FAILED = "failed"
    UNSETTLED = "unsettled"


#: A connection that dropped AFTER the request went out. urllib wraps only
#: ``h.request()`` in ``URLError``; ``h.getresponse()`` and ``response.read()``
#: raise these raw (CPython 3.12 urllib/request.py:1347-1348). Retried, because
#: the POST is idempotent -- but such a drop may follow a commit, so the retry
#: can truthfully answer "no-op" for rows the dropped attempt wrote.
#: ``ConnectionResetError`` covers both a peer RST (errno 104) and
#: ``http.client.RemoteDisconnected``, which subclasses it.
_DROPS: tuple[type[Exception], ...] = (ConnectionResetError, http.client.IncompleteRead)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--post", metavar="BASE_URL", help="hand rows to the live app")
    mode.add_argument("--local", action="store_true", help="write to the configured lake")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SNAPSHOT_SYMBOLS),
        help="comma-separated override of the forward-collected set",
    )
    parser.add_argument("--token", default="", help="bearer token for --post")
    parser.add_argument("--timeout", type=int, default=300)
    return parser.parse_args(argv)


#: How many times to hand the sweep to the app before giving up, and how long
#: to wait between attempts.
#:
#: This retries because of what a lost attempt COSTS here, not because the app
#: is flaky. Every other source in this repo serves history on demand; nobody
#: sells a retroactive option chain, so an attempt abandoned on a transient
#: fault costs that session permanently.
#:
#: Measured 2026-09-04: the sweep fetched all 24 chains (20,113 quotes) and the
#: app answered HTTP 500. The identical sweep, re-posted by hand hours later,
#: was accepted with no change to either side — so the failure was transient
#: and a single immediate retry would have saved the session. It was instead
#: recovered by a human noticing a red run, which is not a control.
#:
#: Backoff is generous because the plausible causes are a cold start and memory
#: pressure on a 1 GB machine parsing a ~5 MB body; both want seconds, not
#: milliseconds.
POST_ATTEMPTS = 4
POST_BACKOFF_S = (5, 20, 60)


def _post_once(
    base_url: str, path: str, token: str, payload: Mapping[str, object], timeout: int
) -> object:
    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result: object = json.loads(response.read())
    return result


def _retryable(exc: Exception) -> bool:
    """Whether re-sending the SAME body could plausibly succeed.

    A 5xx or a transport error says the app failed to handle a request it
    might handle next time. A 4xx says the app understood and refused: 401 is
    a bad token, 422 is a body this contract rejects, and re-sending either
    just burns the window. The one exception is 429, which explicitly means
    "later". A dropped connection (:data:`_DROPS`) is a transport error too.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, (urllib.error.URLError, TimeoutError, *_DROPS))


def _failed_after_send(exc: Exception) -> bool:
    """Whether a retried failure may have followed a commit on the app.

    urllib wraps only a failure to connect or send in a bare ``URLError``;
    such an attempt never reached the route. Anything else came after the
    body went out: a drop or a raw ``TimeoutError`` while waiting for the
    answer, or a 5xx the app may have raised after ``write_bronze``. A 429
    is refused before any work, so it cannot have landed rows.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    return not isinstance(exc, urllib.error.URLError)


def _post(
    base_url: str, right: Right, token: str, payload: Mapping[str, object], timeout: int
) -> tuple[object, bool]:
    """POST one wing's sweep, retrying only faults that a retry could fix.

    Returns ``(answer, may_have_landed)``: ``may_have_landed`` says an earlier
    attempt failed after sending (:func:`_failed_after_send`), so a "no-op"
    answer may be the echo of that attempt's own commit.
    """
    may_have_landed = False
    label, path = _LABEL[right], _PATH[right]
    for attempt in range(1, POST_ATTEMPTS + 1):
        try:
            return _post_once(base_url, path, token, payload, timeout), may_have_landed
        except (urllib.error.URLError, TimeoutError, *_DROPS) as exc:
            if not _retryable(exc) or attempt == POST_ATTEMPTS:
                raise
            may_have_landed = may_have_landed or _failed_after_send(exc)
            detail = getattr(exc, "code", None) or getattr(exc, "reason", exc)
            wait = POST_BACKOFF_S[min(attempt - 1, len(POST_BACKOFF_S) - 1)]
            print(
                f"::warning::{label}: attempt {attempt}/{POST_ATTEMPTS} failed ({detail}); "
                f"retrying in {wait}s — an abandoned session cannot be re-collected",
                file=sys.stderr,
            )
            time.sleep(wait)
    # Unreachable while POST_ATTEMPTS >= 1: the last iteration either returns
    # or re-raises. Stated as an assertion rather than left to fall off the end,
    # because setting POST_ATTEMPTS = 0 to "turn retries off" would otherwise
    # return None into a caller that expects a dict -- and `scripts/` is linted
    # but never type-checked (`make typecheck` covers src/tail_lab only), so
    # nothing else would catch it.
    raise AssertionError("POST_ATTEMPTS must be >= 1")


def _market_session() -> dt.date | None:
    """Newest completed US session per Nasdaq, for the stale-feed check.

    ``None`` is survivable -- the check is skipped, the sweep still runs --
    but it is announced, because a sweep that silently lost its only witness
    against a frozen Cboe feed is back to trusting Cboe about itself.
    """
    session = latest_market_session()
    if session is None:
        print(
            "::warning::could not read the latest market session from Nasdaq; "
            "a frozen Cboe feed would go undetected on this run"
        )
    return session


def _run_local(symbols: list[str]) -> int:
    """Fetch and write in one process, for a human at a terminal.

    Kept out of ``main`` so ``main`` stays under the complexity ratchet, and
    because this half must never run the ``--post`` path's ``sweep_to_records``
    first. It used to: ``main`` swept unconditionally and THEN called
    ``ingest_option_chain``, which then did its own fetch -- every chain fetched
    TWICE, 48 requests where 24 were needed, back to back.

    That was invisible on a GitHub runner and bit immediately on a workstation.
    Measured 2026-09-19: Cboe returned 429 on 9 of 24 symbols, the first sweep
    reporting 24/24 and the second 15/24. Because bronze is immutable the short
    partition is the one that would have stood for that session, and the nine
    missing chains are as unrecoverable as the whole day -- the reasoning
    already written against ``_FETCH_ATTEMPTS`` in the adapter. The same rule
    is why both wings are sliced from ONE ``fetch_chains`` pass
    (``docs/adr/0031``) rather than an ingest that fetches per wing.
    """
    # Imported here, not at module scope: the --post path must stay importable
    # (and runnable in CI) without any lake configuration.
    from tail_lab.config import get_lake_store
    from tail_lab.ingestion.option_chain import fetch_chains, ingest_option_chain

    store = get_lake_store()
    session = _market_session()
    now = dt.datetime.now(dt.UTC)
    payloads = fetch_chains(symbols)

    def ingest(right: Right) -> IngestResult:
        return ingest_option_chain(
            store,
            symbols,
            right=right,
            payloads=payloads,
            market_session=session,
            now=now,
        )

    put = _ingest_wing(ingest, "P", symbols)
    if put is Outcome.SKIPPED:
        print(_CALLS_NOT_ATTEMPTED)
        return 0
    return _finish(put, _ingest_wing(ingest, "C", symbols))


def _ingest_wing(
    ingest: Callable[[Right], IngestResult], right: Right, symbols: list[str]
) -> Outcome:
    """Run one wing's local ingest and turn every way it ends into an outcome."""
    from tail_lab.ingestion.option_chain import IncompleteSweepError, SessionInProgress
    from tail_lab.lake.store import LAKE_WRITE_ERRORS

    label = _LABEL[right]
    try:
        result = ingest(right)
    except SessionInProgress as exc:
        # A wake-time catch-up that landed mid-session. Benign for the puts by
        # construction: raised only once the lake confirms the previous session.
        return _unsettled(right, str(exc))
    except IncompleteSweepError as exc:
        # Refusing to write is the CORRECT outcome, but it is still a failed
        # sweep for the operator: the session is recoverable only until the
        # next US open, so this must exit non-zero and trip the alert.
        print(f"::error::{label}: {exc}", file=sys.stderr)
        return Outcome.FAILED
    except LAKE_WRITE_ERRORS as exc:
        # A failed bronze write. Annotated rather than raised, so the other
        # wing still gets its one chance at this session.
        _LOGGER.exception("event=chain_snapshot.lake_write_failed wing=%s", label)
        print(f"::error::{label}: the lake write failed: {exc!r}", file=sys.stderr)
        return Outcome.FAILED
    if result.valid_rows < MIN_PLAUSIBLE_ROWS:
        print(
            f"::error::{label}: only {result.valid_rows} rows across {len(symbols)} chains — "
            "treating as a failed sweep, not an empty market",
            file=sys.stderr,
        )
        return Outcome.FAILED
    _report_local(result, label, len(symbols))
    return Outcome.OK


def _unsettled(right: Right, detail: str) -> Outcome:
    """A session still trading (a 425 / ``SessionInProgress``).

    For the puts that is a SKIP: nothing is written and nothing is at risk.
    For the calls it is a failure, and the log says so rather than printing a
    benign-looking ``calls: SKIPPED``. Whether it also means the wings
    disagree depends on how the puts ended, which :func:`_finish` knows.
    """
    if right == "P":
        print(f"puts: SKIPPED: {detail}")
        return Outcome.SKIPPED
    print(f"::error::calls: session still in progress: {detail}", file=sys.stderr)
    return Outcome.UNSETTLED


def _report_local(result: IngestResult, label: str, requested: int) -> None:
    _report_symbols(label, result.symbols_failed, result.symbols_misrouted)
    if result.symbols_off_session:
        print(
            f"::warning::{label}: off-session quotes for {', '.join(result.symbols_off_session)} "
            f"(Cboe served a different session, or no last_trade_time at all); their rows "
            f"were quarantined, so they have NO {result.quote_date} quotes"
        )
    # Say which of the two things actually happened. These used to print the
    # same sentence: `write_bronze` returns the same path whether it wrote or
    # short-circuited on an existing ingest_date, so a no-op reported the
    # in-memory row count as though it had been committed.
    if result.committed:
        print(
            f"{label}: swept {len(result.symbols_ok)}/{requested} chains -> "
            f"committed {result.valid_rows} rows -> {result.bronze_path}"
        )
    else:
        print(
            f"{label}: swept {len(result.symbols_ok)}/{requested} chains -> NO-OP: session "
            f"{result.quote_date} was already captured, wrote nothing -> {result.bronze_path}"
        )


def _finish(put: Outcome, call: Outcome) -> int:
    """The exit code once both wings were attempted: 0 only if both landed.

    The calls have no "skipped": :func:`_unsettled` maps theirs to UNSETTLED,
    a failure.
    """
    if call is Outcome.UNSETTLED and put is Outcome.OK:
        # Only after puts that LANDED: then the wings' voters disagreed about
        # the session. After a put failure nothing says they did.
        print(
            "::error::calls: still trading after the puts settled -- the wings disagree "
            "about the session",
            file=sys.stderr,
        )
    if call is not Outcome.OK and put is Outcome.OK:
        # Distinct on purpose: the chain-loss alert must not tell the operator
        # the PUT session was lost when it landed (AGENT_TODO, ADR 0031).
        print(
            "::error::calls only; puts landed -- this session's call wing was NOT captured",
            file=sys.stderr,
        )
    elif call is not Outcome.OK:
        print("::error::neither wing landed for this session", file=sys.stderr)
    elif put is not Outcome.OK:
        print("::error::puts failed; calls landed", file=sys.stderr)
    return 0 if put is Outcome.OK and call is Outcome.OK else 1


def _post_sweep(args: argparse.Namespace, right: Right, payload: dict[str, object]) -> Outcome:
    """POST one wing and turn every way it can fail into an annotated outcome."""
    label, path = _LABEL[right], _PATH[right]
    try:
        result_json, may_have_landed = _post(args.post, right, args.token, payload, args.timeout)
    except urllib.error.HTTPError as exc:
        return _refused(exc, right, label)
    except urllib.error.URLError as exc:
        print(f"::error::{label}: could not reach {args.post}: {exc.reason}", file=sys.stderr)
        return Outcome.FAILED
    except TimeoutError:
        # NOT caught by the clause above: TimeoutError is a sibling of URLError
        # under OSError, not a subclass. `_post` now models timeouts as a
        # first-class retryable outcome, so this is the shape a fully wedged
        # app arrives in -- and it has to read as an annotated failure rather
        # than a raw traceback, because the premise of this workflow is that a
        # red run is legible at a glance.
        print(
            f"::error::{label}: {args.post} did not answer within {args.timeout}s "
            f"across {POST_ATTEMPTS} attempts",
            file=sys.stderr,
        )
        return Outcome.FAILED
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # AFTER the clauses above, which are all OSError subclasses and would
        # lose their annotations to this one. What reaches here: a connection
        # dropped on every attempt, or a 200 whose body is not JSON. Named
        # classes, not a catch-all (Rule 14); logged with the traceback, and
        # mapped to FAILED so the other wing is still attempted.
        _LOGGER.exception("event=chain_snapshot.post_failed wing=%s", label)
        print(f"::error::{label}: the POST to {path} failed: {exc!r}", file=sys.stderr)
        return Outcome.FAILED

    try:
        response = OptionChainSnapshotResponse.model_validate(result_json)
    except ValidationError as exc:
        # The app answered 2xx with a body this script cannot read: the sweep
        # may or may not have landed, so this is a failure, never a guess.
        print(
            f"::error::{label}: the app's response is not a snapshot result: {exc}",
            file=sys.stderr,
        )
        return Outcome.FAILED
    if response.dataset != WINGS[right].dataset:
        # The app filed this wing under another dataset: the rows landed
        # somewhere a reader of this wing will never look.
        print(
            f"::error::{label}: the app wrote {response.dataset}, "
            f"not {WINGS[right].dataset}: {response.bronze_path}",
            file=sys.stderr,
        )
        return Outcome.FAILED
    return _report_post(response, label, may_have_landed=may_have_landed)


def _error_body(exc: urllib.error.HTTPError) -> bytes:
    """The refusal's body, or ``b""`` when the connection drops mid-read: a
    lost detail must not become a traceback that skips the other wing."""
    try:
        return exc.read()[:400]
    except (OSError, http.client.HTTPException):
        return b""


def _refused(exc: urllib.error.HTTPError, right: Right, label: str) -> Outcome:
    detail = _error_body(exc)
    if exc.code == 425:
        # SessionInProgress on the app side: nothing written.
        return _unsettled(right, repr(detail))
    # Between a merge and its deploy, the SPA's StaticFiles mount (mounted at
    # "/" in api/main.py) answers a POST to the unknown call path with 405.
    # Named, so the red run explains itself. Only 405: a 404 is what the auth
    # gate answers when the app's token is unset (api/auth.py), on either route.
    hint = (
        " -- call route not deployed yet (check deploy.yml)"
        if right == "C" and exc.code == 405
        else ""
    )
    print(
        f"::error::{label}: app rejected the sweep: HTTP {exc.code} {detail!r}{hint}",
        file=sys.stderr,
    )
    return Outcome.FAILED


def _post_wing(
    args: argparse.Namespace,
    right: Right,
    records: list[dict[str, object]],
    symbols: list[str],
    session: dt.date | None,
) -> Outcome:
    """Floor-check one wing, then POST it. A wing under the floor is not sent."""
    label = _LABEL[right]
    if len(records) < MIN_PLAUSIBLE_ROWS:
        print(
            f"::error::{label}: only {len(records)} rows across {len(symbols)} chains — "
            "treating as a failed sweep, not an empty market",
            file=sys.stderr,
        )
        return Outcome.FAILED
    # No ingest_date: the server derives the partition from the session the
    # quotes belong to. Sending today's date here is what put 2026-08-26's
    # session into an ingest_date=2026-08-27 partition when GitHub ran the
    # 21:30 cron at 00:57 (docs/adr/0020, "the partition is the session").
    payload: dict[str, object] = {
        "rows": records,
        "symbols": symbols,
        "market_session": session.isoformat() if session else None,
    }
    return _post_sweep(args, right, payload)


def _report_sweep(symbols: list[str], records: list[dict[str, object]], label: str) -> None:
    swept = {r["underlying"] for r in records}
    missing = sorted({s.upper() for s in symbols} - swept)
    print(f"{label}: swept {len(swept)}/{len(symbols)} chains -> {len(records)} quotes")
    if missing:
        print(f"::warning::{label}: no quotes for {', '.join(missing)}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging()

    symbols = [s.strip().lower() for s in args.symbols.split(",") if s.strip()]

    if args.local:
        return _run_local(symbols)

    # Before the sweep and any floor: a tokenless run can land nothing, and
    # the operator's mistake is the token, whatever the sweep would return.
    if not args.token:
        print("::error::--post needs --token", file=sys.stderr)
        return 2

    puts, calls = sweep_to_records(symbols)
    _report_sweep(symbols, puts, "puts")
    _report_sweep(symbols, calls, "calls")

    # ONE witness read for both wings, so they cannot straddle a session.
    session = _market_session()
    put = _post_wing(args, "P", puts, symbols, session)
    if put is Outcome.SKIPPED:
        print(_CALLS_NOT_ATTEMPTED)
        return 0
    return _finish(put, _post_wing(args, "C", calls, symbols, session))


def _report_post(
    response: OptionChainSnapshotResponse, label: str, *, may_have_landed: bool = False
) -> Outcome:
    """Say which of the two things the app did -- commit or no-op.

    The endpoint used to echo ``rows`` either way, so this printed
    "committed 20078 rows" on 2026-09-23 and again on 2026-09-24 for a session
    the lake already held, while 2026-09-23 was being lost unmentioned.

    ``may_have_landed``: an earlier attempt failed after sending, so a no-op
    answer cannot say "wrote nothing" -- that attempt may have committed the
    rows, or an earlier run did; from here the two look the same.
    """
    if response.committed:
        print(
            f"{label}: committed {response.rows} rows for {response.symbols} symbols "
            f"(session {response.quote_date}) -> {response.bronze_path}"
        )
    elif may_have_landed:
        print(
            f"{label}: landed (an earlier attempt failed after sending and may have "
            f"committed it): session {response.quote_date} -> {response.bronze_path}"
        )
    else:
        print(
            f"{label}: NO-OP: session {response.quote_date} was already captured, wrote "
            f"nothing -> {response.bronze_path}"
        )
    _report_symbols(label, response.symbols_failed, response.symbols_misrouted)
    if response.symbols_off_session:
        print(
            f"::warning::{label}: off-session quotes for "
            f"{', '.join(response.symbols_off_session)}"
            f"; their rows were quarantined, so they have NO {response.quote_date} quotes"
        )
    if response.quarantined:
        # Expected in small numbers -- far-OTM strikes with no resting offer.
        # Worth surfacing, never worth failing the sweep over. No cause is
        # printed: the count also holds off-session and misrouted rows.
        print(f"::warning::{label}: {response.quarantined} rows quarantined")
    return Outcome.OK


def _report_symbols(label: str, failed: Sequence[str], misrouted: Sequence[str]) -> None:
    """Name the symbols that landed nothing, by cause. A misrouted symbol HAD
    quotes -- mostly of the other wing's delta sign (``Wing.split``) -- so it
    is not reported as "no quotes": that would point the operator at a
    re-run, which sends the same chain."""
    unquoted = [s for s in failed if s not in misrouted]
    if unquoted:
        print(f"::warning::{label}: no quotes landed for {', '.join(unquoted)}")
    if misrouted:
        print(
            f"::warning::{label}: {', '.join(misrouted)} carried the other wing's delta "
            "signs; quarantined whole"
        )


if __name__ == "__main__":
    raise SystemExit(main())
