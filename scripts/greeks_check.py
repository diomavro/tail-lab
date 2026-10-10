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

Each contract is priced with its underlying's measured dividend yield on the
quote date (`research/dividends.py`). Before that existed every name priced at
``q = 0`` and the residual tracked dividend yield exactly (TSLA 0.0006, SPY
0.0043, TLT 0.060, HYG 0.197). With measured ``q`` that ordering should
flatten; ``--no-dividends`` reruns the old ``q = 0`` scoring for comparison. A
name with no measured yield is reported with its source so an ``unknown`` is
never mistaken for a fit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from tail_lab.config import get_lake_store
from tail_lab.contracts.option_chain import DATASET
from tail_lab.research.dividends import dividend_lookup
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
    parser.add_argument(
        "--no-dividends", action="store_true", help="price at q = 0 (the pre-dividend scoring)"
    )
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

    session = quote_date.date()
    yields = {
        symbol: dividend_lookup(store, str(symbol), session).lookup(session)
        for symbol in df["underlying"].unique()
    }
    df["q"] = [0.0 if args.no_dividends else yields[u].q for u in df["underlying"]]
    pricer = BlackScholesPricer()
    df["our_delta"] = [
        pricer.greeks_put(
            spot=row.spot,
            strike=row.strike,
            t_years=row.t_years,
            r=args.rate,
            sigma=row.iv,
            q=row.q,
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
    basis = "q = 0 for every name" if args.no_dividends else "each name's measured q"
    print(f"priced with {basis}\n")
    print(f"{'symbol':<8}{'n':>6}{'median |err|':>14}{'p95 |err|':>12}{'q':>8}  source")
    for symbol, row in by_symbol.iterrows():
        dy = yields[str(symbol)]
        q = 0.0 if args.no_dividends else dy.q
        print(
            f"{symbol:<8}{int(row['n']):>6}{row['median']:>14.5f}{row['p95']:>12.5f}"
            f"{q:>8.4f}  {'-' if args.no_dividends else dy.source}"
        )
    overall = df["abs_err"].median()
    print(f"\n{'OVERALL':<8}{len(df):>6}{overall:>14.5f}{df['abs_err'].quantile(0.95):>12.5f}")
    print(
        "\nAt q = 0 the residual tracks dividend yield; with measured q it should flatten:\n"
        f"  tightest: {by_symbol.index[0]} at {by_symbol.iloc[0]['median']:.5f}\n"
        f"  loosest:  {by_symbol.index[-1]} at {by_symbol.iloc[-1]['median']:.5f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
