#!/usr/bin/env python3
"""Turn the marked roll schedule into an order ticket a human can place.

This is rung (b) of the execution ladder: the platform states what it
concluded and what the market currently charges for it, a person reads that
and decides. **It holds no credential, contacts no broker and places no
order** -- it prints, and optionally writes a JSON file. That keeps it
squarely inside ``docs/adr/0007``, which ``docs/adr/0019`` has not yet
superseded.

Why this exists before an automated executor. On 2026-08-27 the schedule's top
recommendation priced a KRE put at $0.0019/share against a $1.13 market -- a
600x error, and the leg's advertised 1867% return on premium was that error
restated. An executor wired to that file would have placed the position
faithfully and at speed. Printing the same thing for a human to read costs
almost nothing and fails safe, and the useful work it forces -- snapping to a
listed strike, pricing off a real quote, refusing an illiquid contract -- is
the work an automated executor would need anyway.

  python scripts/order_ticket.py                        # print today's ticket
  python scripts/order_ticket.py --json out/today.json  # ...and drop the artifact

The JSON it writes is the same ``MarkedSchedule`` the API returns: data, never
code, so whatever consumes it later parses a document rather than executing
one.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE = "https://tail-lab.fly.dev"
SHARES_PER_CONTRACT = 100

#: Why a leg is not placeable, in words a reader can act on.
_REFUSAL = {
    "not_collected": "no chain collected for this name (24 of 70 covered — docs/adr/0020)",
    "no_listed_contract": "nothing listed at or after the target expiry",
    "illiquid": "listed but untradeable: no bid, thin interest, or spread wider than half the mid",
}


def _fetch(base: str, params: str, timeout: int) -> dict[str, Any]:
    url = f"{base.rstrip('/')}/api/putlab/roll-schedule/marked{params}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload: dict[str, Any] = json.loads(response.read())
    return payload


def _describe(leg: dict[str, Any]) -> str:
    """One placeable leg, in the words an order entry screen wants."""
    contracts = leg["market_contracts"]
    ask = leg["market_ask"]
    spend = contracts * ask * SHARES_PER_CONTRACT
    expiry = str(leg["listed_expiry"])
    return (
        f"  BUY {contracts:>3} x {leg['asset'].upper():<5} "
        f"{leg['listed_strike']:g}P {expiry}  "
        f"@ {ask:.2f} limit   ${spend:,.0f} of ${leg['premium_budget']:,.0f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--moneyness-pct", type=float, default=5.0)
    parser.add_argument("--tenor-weeks", type=float, default=4.0)
    parser.add_argument("--notional", type=float, default=1000.0)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--json", dest="json_path", default=None, help="also write the artifact")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)

    params = (
        f"?moneyness_pct={args.moneyness_pct}&tenor_weeks={args.tenor_weeks}"
        f"&notional={args.notional}&top_k={args.top_k}"
    )
    try:
        schedule = _fetch(args.base, params, args.timeout)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"could not reach {args.base}: {exc}", file=sys.stderr)
        return 1

    legs = schedule.get("legs", [])
    placeable = [leg for leg in legs if leg.get("quote_status") == "quoted"]

    print(f"schedule {schedule['schedule_id']}  screen as-of {schedule['as_of']}")
    print(f"quotes from session {schedule.get('quote_session')}")
    print(f"{len(placeable)} of {len(legs)} legs placeable\n")

    if placeable:
        print("ORDER TICKET — place by hand, then record the fill:")
        for leg in placeable:
            print(_describe(leg))
            ratio = leg.get("market_to_model_ratio")
            if ratio and ratio > 2:
                print(
                    f"        ! the market charges {ratio:,.0f}x the model's premium, so this"
                    f" leg's expected return ({leg.get('expected_roi_on_premium')}) is"
                    f" optimistic by roughly that factor"
                )
    else:
        print("ORDER TICKET — nothing to place today.")
        print("  For a bleed strategy that is a legitimate answer: the position only")
        print("  pays if it is on when the tail arrives, but paying up for a contract")
        print("  nobody trades is how the bleed stops being survivable.")

    refused = [leg for leg in legs if leg.get("quote_status") != "quoted"]
    if refused:
        print("\nNOT PLACEABLE:")
        for leg in refused:
            why = _REFUSAL.get(leg.get("quote_status", ""), leg.get("quote_status", "?"))
            print(f"  {leg['rank']:>2}. {leg['asset'].upper():<6} {why}")

    if args.json_path:
        path = Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(schedule, indent=2))
        print(f"\nartifact written: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
