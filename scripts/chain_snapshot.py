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

Exit codes are the contract with the scheduler: 0 only if quotes actually
landed. A sweep that returns nothing is a FAILURE, not a quiet success —
today's chain is only available today, so a silent no-op is the one outcome
that must never look like a good day.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from tail_lab.contracts.option_chain import DEFAULT_SNAPSHOT_SYMBOLS
from tail_lab.ingestion.option_chain import latest_market_session, sweep_to_records
from tail_lab.observability import configure_logging

#: Below this, assume something broke upstream rather than that the market
#: genuinely had nothing to quote across two dozen liquid chains.
MIN_PLAUSIBLE_ROWS = 200


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


def _post_once(base_url: str, token: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/api/ingest/option-chain",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result: dict[str, Any] = json.loads(response.read())
    return result


def _retryable(exc: Exception) -> bool:
    """Whether re-sending the SAME body could plausibly succeed.

    A 5xx or a transport error says the app failed to handle a request it
    might handle next time. A 4xx says the app understood and refused: 401 is
    a bad token, 422 is a body this contract rejects, and re-sending either
    just burns the window. The one exception is 429, which explicitly means
    "later".
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, urllib.error.URLError | TimeoutError)


def _post(base_url: str, token: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    """POST the sweep, retrying only faults that a retry could fix."""
    for attempt in range(1, POST_ATTEMPTS + 1):
        try:
            return _post_once(base_url, token, payload, timeout)
        except (urllib.error.URLError, TimeoutError) as exc:
            if not _retryable(exc) or attempt == POST_ATTEMPTS:
                raise
            detail = getattr(exc, "code", None) or getattr(exc, "reason", exc)
            wait = POST_BACKOFF_S[min(attempt - 1, len(POST_BACKOFF_S) - 1)]
            print(
                f"::warning::attempt {attempt}/{POST_ATTEMPTS} failed ({detail}); "
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
    ``ingest_option_chain``, which does its own fetch -- every chain fetched
    TWICE, 48 requests where 24 were needed, back to back.

    That was invisible on a GitHub runner and bit immediately on a workstation.
    Measured 2026-09-19: Cboe returned 429 on 9 of 24 symbols, the first sweep
    reporting 24/24 and the second 15/24. Because bronze is immutable the short
    partition is the one that would have stood for that session, and the nine
    missing chains are as unrecoverable as the whole day -- the reasoning
    already written against ``_FETCH_ATTEMPTS`` in the adapter.
    """
    # Imported here, not at module scope: the --post path must stay importable
    # (and runnable in CI) without any lake configuration.
    from tail_lab.config import get_lake_store
    from tail_lab.ingestion.option_chain import IncompleteSweepError, ingest_option_chain

    try:
        result = ingest_option_chain(get_lake_store(), symbols, market_session=_market_session())
    except IncompleteSweepError as exc:
        # Refusing to write is the CORRECT outcome, but it is still a failed
        # sweep for the operator: the session is recoverable only until the
        # next US open, so this must exit non-zero and trip the alert.
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    if result.valid_rows < MIN_PLAUSIBLE_ROWS:
        print(
            f"::error::only {result.valid_rows} rows across {len(symbols)} chains — "
            "treating as a failed sweep, not an empty market",
            file=sys.stderr,
        )
        return 1
    if result.symbols_failed:
        print(f"::warning::no quotes for {', '.join(result.symbols_failed)}")
    if result.symbols_off_session:
        print(
            f"::warning::off-session quotes for {', '.join(result.symbols_off_session)} "
            f"(Cboe served a different session, or no last_trade_time at all); their rows "
            f"were quarantined, so they have NO {result.quote_date} quotes"
        )
    # Say which of the two things actually happened. These used to print the
    # same sentence: `write_bronze` returns the same path whether it wrote or
    # short-circuited on an existing ingest_date, so a no-op reported the
    # in-memory row count as though it had been committed.
    if result.committed:
        print(
            f"swept {len(result.symbols_ok)}/{len(symbols)} chains -> "
            f"committed {result.valid_rows} rows -> {result.bronze_path}"
        )
    else:
        print(
            f"swept {len(result.symbols_ok)}/{len(symbols)} chains -> NO-OP: session "
            f"{result.quote_date} was already captured, wrote nothing -> {result.bronze_path}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging()

    symbols = [s.strip().lower() for s in args.symbols.split(",") if s.strip()]

    if args.local:
        return _run_local(symbols)

    records = sweep_to_records(symbols)

    swept = {r["underlying"] for r in records}
    missing = sorted({s.upper() for s in symbols} - swept)
    print(f"swept {len(swept)}/{len(symbols)} chains -> {len(records)} put quotes")
    if missing:
        print(f"::warning::no quotes for {', '.join(missing)}")

    if len(records) < MIN_PLAUSIBLE_ROWS:
        print(
            f"::error::only {len(records)} rows across {len(symbols)} chains — "
            "treating as a failed sweep, not an empty market",
            file=sys.stderr,
        )
        return 1

    if not args.token:
        print("::error::--post needs --token", file=sys.stderr)
        return 2

    # No ingest_date: the server derives the partition from the session the
    # quotes belong to. Sending today's date here is what put 2026-08-26's
    # session into an ingest_date=2026-08-27 partition when GitHub ran the
    # 21:30 cron at 00:57 (docs/adr/0020, "the partition is the session").
    session = _market_session()
    payload = {
        "rows": records,
        "symbols": symbols,
        "market_session": session.isoformat() if session else None,
    }
    try:
        result_json = _post(args.post, args.token, payload, args.timeout)
    except urllib.error.HTTPError as exc:
        print(
            f"::error::app rejected the sweep: HTTP {exc.code} {exc.read()[:400]!r}",
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as exc:
        print(f"::error::could not reach {args.post}: {exc.reason}", file=sys.stderr)
        return 1
    except TimeoutError:
        # NOT caught by the clause above: TimeoutError is a sibling of URLError
        # under OSError, not a subclass. `_post` now models timeouts as a
        # first-class retryable outcome, so this is the shape a fully wedged
        # app arrives in -- and it has to read as an annotated failure rather
        # than a raw traceback, because the premise of this workflow is that a
        # red run is legible at a glance.
        print(
            f"::error::{args.post} did not answer within {args.timeout}s "
            f"across {POST_ATTEMPTS} attempts",
            file=sys.stderr,
        )
        return 1

    return _report_post(result_json)


def _report_post(result_json: dict[str, Any]) -> int:
    """Say which of the two things the app did -- commit or no-op.

    The endpoint used to echo ``rows`` either way, so this printed
    "committed 20078 rows" on 2026-09-23 and again on 2026-09-24 for a session
    the lake already held, while 2026-09-23 was being lost unmentioned.
    """
    if result_json.get("committed", True):
        print(
            f"committed {result_json['rows']} rows for {result_json['symbols']} symbols "
            f"(session {result_json['quote_date']}) -> {result_json['bronze_path']}"
        )
    else:
        print(
            f"NO-OP: session {result_json['quote_date']} was already captured, wrote "
            f"nothing -> {result_json['bronze_path']}"
        )
    if result_json.get("symbols_failed"):
        print(f"::warning::no quotes landed for {', '.join(result_json['symbols_failed'])}")
    if result_json.get("symbols_off_session"):
        print(
            f"::warning::off-session quotes for {', '.join(result_json['symbols_off_session'])}"
            f"; their rows were quarantined, so they have NO {result_json['quote_date']} quotes"
        )
    quarantined = result_json.get("quarantined", 0)
    if quarantined:
        # Expected in small numbers -- far-OTM strikes with no resting offer.
        # Worth surfacing, never worth failing the sweep over.
        print(f"::warning::{quarantined} rows quarantined (no two-sided quote)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
