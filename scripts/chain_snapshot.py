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
import urllib.error
import urllib.request
from typing import Any

from tail_lab.contracts.option_chain import DEFAULT_SNAPSHOT_SYMBOLS
from tail_lab.ingestion.option_chain import sweep_to_records
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


def _post(base_url: str, token: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/api/ingest/option-chain",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result: dict[str, Any] = json.loads(response.read())
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging()

    symbols = [s.strip().lower() for s in args.symbols.split(",") if s.strip()]
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

    if args.local:
        # Imported here, not at module scope: the --post path must stay
        # importable (and runnable in CI) without any lake configuration.
        from tail_lab.config import get_lake_store
        from tail_lab.ingestion.option_chain import ingest_option_chain

        result = ingest_option_chain(get_lake_store(), symbols)
        print(f"committed {result.valid_rows} rows -> {result.bronze_path}")
        return 0

    if not args.token:
        print("::error::--post needs --token", file=sys.stderr)
        return 2

    payload = {"rows": records, "ingest_date": dt.date.today().isoformat()}
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

    print(
        f"committed {result_json['rows']} rows for {result_json['symbols']} symbols "
        f"(session {result_json['quote_date']}) -> {result_json['bronze_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
