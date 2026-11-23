#!/usr/bin/env python3
"""Print the volatility risk premium a put buyer paid, per name and per year
(``docs/END_STATE.md`` §4 Q6; ``research/vrp.py``).

Read-only: touches no lake writes, so it is safe for an agent to run. Needs the
name's OHLCV and a quote source: by default the licence-limited optionsDX panel
(``make ingest-optionsdx``, 2010-2023), or with ``--source chain`` the
forward-collected Cboe chain (2026-08-26 onward). Neither exists on CI; a name
missing one is reported and skipped, not failed -- an absent source is not a
measurement.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict

import numpy as np

from tail_lab.config import get_lake_store
from tail_lab.research.vrp import VrpSummary, compute_chain_vrp, compute_vrp


def _report(summary: VrpSummary) -> None:
    print(
        f"{summary.underlying}  target {summary.moneyness:.2f} x spot, {summary.target_days}d "
        f"(measured {summary.mean_moneyness:.3f} x spot, {summary.mean_dte:.1f}d)  "
        f"{summary.start} .. {summary.end}  n={summary.n_observations} of {summary.n_quote_dates} dates (overlapping windows)"
    )
    print(
        f"  implied {summary.mean_implied_vol:6.1%}  realised {summary.mean_realised_vol:6.1%}  "
        f"VRP mean {summary.mean_vrp * 100:+5.1f} pts, median {summary.median_vrp * 100:+5.1f} pts, "
        f"implied > realised on {summary.share_positive:.0%}"
    )
    if summary.missing_months:
        print(
            f"  NO QUOTES AT ALL in {len(summary.missing_months)} months: {summary.missing_months}"
        )
    by_year: dict[int, list[float]] = defaultdict(list)
    for obs in summary.observations:
        by_year[obs.quote_date.year].append(obs.vrp)
    for year, vrps in sorted(by_year.items()):
        print(f"    {year}  n={len(vrps):3d}  mean {np.mean(vrps) * 100:+5.1f} pts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="*", default=["spy", "qqq", "nvda", "tsla"])
    parser.add_argument("--moneyness", type=float, default=1.0, help="strike / spot target")
    parser.add_argument("--days", type=int, default=30, help="target days to expiry")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--source",
        choices=["optionsdx", "chain"],
        default="optionsdx",
        help="optionsDX panel (vendor IV) or the forward Cboe chain (Cboe IV)",
    )
    args = parser.parse_args(argv)

    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
    store = get_lake_store()
    compute = compute_chain_vrp if args.source == "chain" else compute_vrp
    print(f"source: {args.source}, as of {as_of}")
    for symbol in args.symbols:
        try:
            summary = compute(
                store,
                underlying=symbol,
                as_of=as_of,
                moneyness=args.moneyness,
                target_days=args.days,
            )
        except (LookupError, ValueError) as exc:
            print(f"{symbol.upper()}: skipped -- {exc}")
            continue
        _report(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
