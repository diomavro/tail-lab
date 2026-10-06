#!/usr/bin/env python3
"""Vendor-disagreement check: Kaggle (Alpha Vantage) SPY puts vs optionsDX SPY.

OFFLINE and human-run (``make kaggle-spy-vs-optionsdx``). Prints a report and
writes nothing: the Kaggle corpus is a cross-check only and must never feed a
verdict or anything served (``docs/DATA_CONTRACTS.md`` #13). Two vendors that
agree on strike sets and quotes over 2023 are evidence each is recording the
market rather than an artefact of its own pipeline; where they disagree, this
names the dates.

The comparison is made on optionsDX's slice (``contracts/optionsdx``:
moneyness band, ask > 0), re-applied to BOTH corpora with one definition --
calendar days to expiry, one day inside each of optionsDX's 0- and 120-day
edges (``COMPARED_MIN_DTE``/``COMPARED_MAX_DTE``). Kaggle carries no spot, so both sides are sliced with
optionsDX's spot for the same date -- which is also why only overlapping
dates can be compared at all.

Degrades rather than fails: either corpus absent is a "nothing to compare"
message and exit 0, because an absent licence-limited download is the normal
state on every machine but the owner's.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass

import pandas as pd

from tail_lab.config import get_lake_store
from tail_lab.contracts.optionsdx import DATASET as ODX_DATASET
from tail_lab.contracts.optionsdx import MAX_DTE_DAYS, MONEYNESS_MAX, MONEYNESS_MIN
from tail_lab.ingestion.kaggle_spy import read_year

_KEY = ["quote_date", "expiration", "strike"]
_ODX_COLUMNS = ["quote_date", "expiration", "strike", "bid", "ask", "spot"]
#: A matched quote whose bid OR ask differs by more than this many CENTS is a
#: disagreement. Two: SPY's tick is one cent and each vendor may round its
#: snapshot by a tick, so a two-tick gap is still noise; three is not.
#: Compared in integer cents -- ``abs(0.32 - 0.30) > 0.02`` is True in float,
#: which once counted every exact two-cent gap as a disagreement.
TOLERANCE_CENTS = 2

#: Calendar days-to-expiry compared, on BOTH sides, one day inside each of
#: optionsDX's ingest edges. optionsDX was sliced on its own vendor DTE column,
#: which disagrees with the calendar count AT the edges: on real 2023, contracts
#: exactly 120 days out were in Kaggle only (390 keys), and so were expiry-day
#: (0-day) contracts (365 keys). Inside the edges both corpora hold every
#: contract, so a one-sided key there is a real vendor difference.
COMPARED_MIN_DTE = 1
COMPARED_MAX_DTE = MAX_DTE_DAYS - 1


@dataclass(frozen=True)
class Comparison:
    """What two vendors said about the same put wing on the same dates."""

    dates_compared: int
    dates_only_kaggle: int
    dates_only_optionsdx: int
    keys_both: int
    keys_only_kaggle: int
    keys_only_optionsdx: int
    median_jaccard: float
    worst_dates: list[tuple[str, float]]
    median_bid_diff: float
    median_ask_diff: float
    median_abs_bid_diff: float
    median_abs_ask_diff: float
    share_outside_tolerance: float


def _in_band(frame: pd.DataFrame, spot: pd.Series) -> pd.DataFrame:
    """Rows on the compared band, using optionsDX's spot for the date.

    Applied to BOTH corpora with one definition (calendar DTE, the same spot),
    so a contract is in the comparison for both vendors or for neither --
    otherwise a band edge manufactures one-sided keys.
    """
    out = frame.drop(columns="spot", errors="ignore").merge(
        spot.rename("spot"), left_on="quote_date", right_index=True
    )
    dte = (out["expiration"] - out["quote_date"]).dt.days
    keep = (
        dte.between(COMPARED_MIN_DTE, COMPARED_MAX_DTE)
        & (out["strike"] / out["spot"]).between(MONEYNESS_MIN, MONEYNESS_MAX)
        & (out["ask"] > 0)
    )
    return out.loc[keep]


def _cents(diff: pd.Series) -> pd.Series:
    return (diff * 100).round().astype("int64")


def compare(kaggle: pd.DataFrame, optionsdx: pd.DataFrame, *, worst: int = 5) -> Comparison | None:
    """Compare on overlapping dates. ``None`` when there are none."""
    k_dates = set(kaggle["quote_date"].unique())
    o_dates = set(optionsdx["quote_date"].unique())
    shared = k_dates & o_dates
    if not shared:
        return None
    odx = optionsdx[optionsdx["quote_date"].isin(shared)]
    spot = odx.groupby("quote_date")["spot"].first()
    kag = _in_band(kaggle[kaggle["quote_date"].isin(shared)], spot)
    odx = _in_band(odx, spot)

    joined = kag[[*_KEY, "bid", "ask"]].merge(
        odx[[*_KEY, "bid", "ask"]], on=_KEY, how="outer", suffixes=("_k", "_o"), indicator=True
    )
    side = joined["_merge"]
    per_day = joined.assign(both=side == "both").groupby("quote_date")["both"].mean()
    both = joined[side == "both"]
    bid_diff = both["bid_k"] - both["bid_o"]
    ask_diff = both["ask_k"] - both["ask_o"]
    outside = (_cents(bid_diff).abs() > TOLERANCE_CENTS) | (
        _cents(ask_diff).abs() > TOLERANCE_CENTS
    )
    worst_days = per_day.nsmallest(worst)
    return Comparison(
        dates_compared=len(shared),
        dates_only_kaggle=len(k_dates - o_dates),
        dates_only_optionsdx=len(o_dates - k_dates),
        keys_both=len(both),
        keys_only_kaggle=int((side == "left_only").sum()),
        keys_only_optionsdx=int((side == "right_only").sum()),
        median_jaccard=float(per_day.median()),
        worst_dates=[(pd.Timestamp(d).date().isoformat(), float(j)) for d, j in worst_days.items()],
        median_bid_diff=float(bid_diff.median()),
        median_ask_diff=float(ask_diff.median()),
        median_abs_bid_diff=float(bid_diff.abs().median()),
        median_abs_ask_diff=float(ask_diff.abs().median()),
        share_outside_tolerance=float(outside.mean()) if len(both) else 0.0,
    )


def _render(year: int, c: Comparison) -> str:
    lines = [
        f"Kaggle (Alpha Vantage) vs optionsDX -- SPY puts, {year}: moneyness "
        f"{MONEYNESS_MIN}-{MONEYNESS_MAX}, calendar DTE {COMPARED_MIN_DTE}-{COMPARED_MAX_DTE}, "
        "ask > 0",
        f"  dates compared {c.dates_compared}  (only Kaggle {c.dates_only_kaggle}, "
        f"only optionsDX {c.dates_only_optionsdx})",
        f"  (expiry, strike) keys: both {c.keys_both}, only Kaggle {c.keys_only_kaggle}, "
        f"only optionsDX {c.keys_only_optionsdx}",
        f"  per-day key overlap (Jaccard), median {c.median_jaccard:.3f}",
        f"  bid diff (K - O): median {c.median_bid_diff:+.4f}, median |.| {c.median_abs_bid_diff:.4f}",
        f"  ask diff (K - O): median {c.median_ask_diff:+.4f}, median |.| {c.median_abs_ask_diff:.4f}",
        f"  matched quotes off by more than {TOLERANCE_CENTS} cents on bid or ask "
        f"(integer cents): {c.share_outside_tolerance:.1%}",
        "  worst days by overlap: " + ", ".join(f"{d} {j:.3f}" for d, j in c.worst_dates),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2023, help="optionsDX ends 2023-12")
    parser.add_argument("--as-of", default=None, help="optionsDX snapshot, YYYY-MM-DD")
    args = parser.parse_args(argv)

    try:
        kaggle = read_year(args.year)
    except FileNotFoundError as err:
        print(f"SKIPPED -- Kaggle corpus absent: {err}")
        return 0
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
    try:
        odx = get_lake_store().read_bronze_columns_as_of(f"{ODX_DATASET}_spy", as_of, _ODX_COLUMNS)
    except LookupError:
        print(f"SKIPPED -- no {ODX_DATASET}_spy snapshot as of {as_of} (make ingest-optionsdx)")
        return 0
    odx = odx[odx["quote_date"].dt.year == args.year]
    result = compare(kaggle, odx)
    if result is None:
        print(f"SKIPPED -- the two corpora share no quote dates in {args.year}")
        return 0
    print(_render(args.year, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
