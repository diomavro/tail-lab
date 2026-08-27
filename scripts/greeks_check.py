#!/usr/bin/env python3
"""Score our Black-Scholes greeks against the exchange's own.

`research/option_pricer.py` is validated in CI against finite differences and
against put-call parity -- both of which prove it is a correct derivative of
*itself*. Neither can catch a wrong model. Cboe publishes its own delta on
every contract in the forward-collected chain (`docs/adr/0020`), computed off
the real market smile by someone with no stake in our being right, which makes
it the one genuinely independent reference this platform has.

Read-only: touches no lake writes, so unlike the ingest-* targets it is safe
for an agent to run. Needs the chain snapshot, so it reports and exits 0 when
none exists rather than failing -- an empty lake is not a wrong pricer.

Expect a small residual, and expect it to track dividend yield: we price with
``q = 0`` by default (assumptions register item 4), so non-payers should match
tightest and high-yield ETFs loosest. A run where that ordering *inverts* is
the interesting failure, not a slightly larger median.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from tail_lab.config import get_lake_store
from tail_lab.contracts.option_chain import DATASET
from tail_lab.research.option_pricer import BlackScholesPricer

#: Liquidity floor. A contract with no real bid or no open interest carries a
#: stale exchange greek, and scoring against it measures staleness, not skill.
MIN_BID = 0.05
MIN_OPEN_INTEREST = 100
#: Tenor band, in years. Very short-dated greeks are numerically violent and
#: very long-dated ones are dominated by the rate and dividend assumptions.
MIN_T = 0.02
MAX_T = 0.60


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=float, default=0.04, help="flat risk-free rate")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)

    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
    store = get_lake_store()
    try:
        df = store.read_bronze_as_of(DATASET, as_of)
    except LookupError:
        print(f"no {DATASET} snapshot as of {as_of} — nothing to score (run the daily sweep first)")
        return 0

    df = df[df["iv"].notna() & df["delta"].notna()].copy()
    if df.empty:
        print("snapshot carries no exchange greeks — nothing to score")
        return 0

    quote_date = df["quote_date"].iloc[0]
    df["t_years"] = (df["expiration"] - quote_date).dt.days / 365.25
    df = df[
        (df["t_years"] > MIN_T)
        & (df["t_years"] < MAX_T)
        & (df["bid"] > MIN_BID)
        & (df["open_interest"] > MIN_OPEN_INTEREST)
    ]
    if df.empty:
        print("no contracts cleared the liquidity/tenor filters")
        return 0

    pricer = BlackScholesPricer()
    df["our_delta"] = [
        pricer.greeks_put(
            spot=row.spot, strike=row.strike, t_years=row.t_years, r=args.rate, sigma=row.iv
        ).delta
        for row in df.itertuples()
    ]
    df["abs_err"] = (df["our_delta"] - df["delta"]).abs()

    by_symbol = (
        df.groupby("underlying")["abs_err"]
        .agg(n="size", median="median", p95=lambda s: s.quantile(0.95))
        .sort_values("median")
    )
    print(f"session {quote_date.date()!s} — our delta vs Cboe's, {len(df)} liquid contracts\n")
    print(f"{'symbol':<8}{'n':>6}{'median |err|':>14}{'p95 |err|':>12}")
    for symbol, row in by_symbol.iterrows():
        print(f"{symbol:<8}{int(row['n']):>6}{row['median']:>14.5f}{row['p95']:>12.5f}")
    overall = df["abs_err"].median()
    print(f"\n{'OVERALL':<8}{len(df):>6}{overall:>14.5f}{df['abs_err'].quantile(0.95):>12.5f}")
    print(
        "\nResidual is expected and should track dividend yield (assumptions register item 4):\n"
        f"  tightest: {by_symbol.index[0]} at {by_symbol.iloc[0]['median']:.5f}\n"
        f"  loosest:  {by_symbol.index[-1]} at {by_symbol.iloc[-1]['median']:.5f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
