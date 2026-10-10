"""How far the served "delta at realised vol" strike sits from the market's.

OFFLINE and human-run (``make delta-strike-gap``): reads the hand-downloaded
optionsDX archives in ``data/vendor/optionsdx/`` directly -- no lake, no
network, never on CI (the corpus is gitignored and licence-limited). Prints a
report; writes nothing.

For the first quote date of every month it computes, for each target delta:

* **model** -- the strike the served backtest would pick: ``ByDelta(target)``
  on the trailing 20-day realised vol of the panel's own spot, T = 20/252
  (the backtest's 4 weeks of trading days);
* **market** -- the listed put, at the first listed expiry 28 or more calendar
  days out -- the served preview's rule (``strike_preview``) -- capped at 35 so
  it stays a 4-week option (T = days/365, the chain's own clock), whose delta
  under the SAME convention (``strike_rule.put_delta``) on the vendor's own
  ``P_IV`` is nearest the target. The vendor's ``P_DELTA`` is not used: its
  convention is not ours (docs/adr/0029).

A target is **unreachable** that day when even the deepest listed put with an
implied vol (the panel holds moneyness >= 0.60, ``contracts/optionsdx``) has a
larger |delta| than the target -- the market's strike lies below the panel.
Rows are grouped by the VIX close that day (the VIX panel's spot is the index)
on the platform's bands (``contracts/regime``), read as levels without the
classifier's hysteresis.

The spot is as-traded, so a split inside the window would read as a crash in
the realised vol: only names that did not split in 2010-2023 are accepted.

Disclosed simplifications: flat r = 4% (the platform's DEFAULT_RATE) and a
flat q per name (SPY 1.5%, QQQ 0.6%) -- the measured Tiingo yields are not in a
local lake; a 0.5pp error in q moves a 0.10-delta 4-week strike by ~0.04%.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from tail_lab.contracts.regime import CALM_MAX, ELEVATED_MAX

# The ingest's own readers: _read_month_file parses straight off disk (a month
# read into a str costs ~4x its size in RSS), _dedupe_archives drops browser
# re-downloads that would double a year.
from tail_lab.ingestion.optionsdx import _dedupe_archives, _read_month_file, read_archive_months
from tail_lab.research.backtest.put_roll import (
    REALIZED_VOL_CAP,
    REALIZED_VOL_FLOOR,
    trailing_realized_vol,
)
from tail_lab.research.backtest.strike_rule import ByDelta, put_delta

VENDOR = Path("data/vendor/optionsdx")  # overridable: --vendor
RATE = 0.04
FLAT_Q = {"spy": 0.015, "qqq": 0.006}
TARGETS = (0.05, 0.10, 0.20)
MODEL_T = 20 / 252
#: First expiry on or after the tenor (as the preview), no later than the cap.
MIN_DTE, MAX_DTE = 28, 35
SYMBOLS = ("spy", "qqq")  # no splits 2010-2023; see the docstring


@dataclass(frozen=True)
class GapRow:
    day: pd.Timestamp
    target: float
    regime: str
    model_pct: float  # model strike, % below spot
    market_pct: float | None  # None = unreachable or no IV
    reachable: bool


def regime_of(vix: float) -> str:
    return "calm" if vix < CALM_MAX else "elevated" if vix < ELEVATED_MAX else "crisis"


def market_strike_pct(day: pd.DataFrame, target: float, q: float) -> tuple[float | None, bool]:
    """(% below spot of the listed put nearest ``-target``, reachable?).

    ``day`` holds one quote date's puts: expiration, strike, spot, iv."""
    near = day[(day["dte"] >= MIN_DTE) & (day["dte"] <= MAX_DTE)]
    if near.empty:
        return None, True
    expiry = near["expiration"].min()
    at = day[(day["expiration"] == expiry) & (day["iv"] > 0)]
    if at.empty:
        return None, True
    spot = float(at["spot"].iloc[0])
    t = float(at["dte"].iloc[0]) / 365.0
    deltas = np.array(
        [
            -put_delta(spot=spot, strike=float(k), sigma=float(v), t_years=t, r=RATE, q=q)
            for k, v in zip(at["strike"], at["iv"], strict=True)
        ]
    )
    if deltas.min() > target:  # even the deepest listed put is closer to the money
        return None, False
    pos = int(np.abs(deltas - target).argmin())
    return (1.0 - float(at["strike"].iloc[pos]) / spot) * 100.0, True


def gap_rows(
    days: dict[pd.Timestamp, pd.DataFrame],
    spot: pd.Series,
    vix: pd.Series,
    *,
    q: float,
    targets: Sequence[float] = TARGETS,
) -> list[GapRow]:
    """One row per sampled day x target. ``spot`` is the daily spot series the
    realised vol is measured on; ``vix`` the VIX close by date."""
    rv = trailing_realized_vol(spot)
    rows: list[GapRow] = []
    for day, frame in sorted(days.items()):
        sigma = rv.get(day)
        level = vix.get(day)
        if sigma is None or not math.isfinite(sigma) or level is None:
            continue
        sigma = min(max(float(sigma), REALIZED_VOL_FLOOR), REALIZED_VOL_CAP)
        s = float(spot[day])
        for target in targets:
            k = ByDelta(target).strike(spot=s, sigma=sigma, t_years=MODEL_T, r=RATE, q=q)
            market, reachable = market_strike_pct(frame, target, q)
            rows.append(
                GapRow(day, target, regime_of(float(level)), (1 - k / s) * 100, market, reachable)
            )
    return rows


def _months(vendor: Path, symbol: str) -> Iterable[pd.DataFrame]:
    for archive in _dedupe_archives(sorted(vendor.glob(f"{symbol}_eod_*.7z"))):
        for _, path in read_archive_months(archive):
            frame, _ = _read_month_file(symbol, path)
            yield frame


def load(vendor: Path, symbol: str) -> tuple[dict[pd.Timestamp, pd.DataFrame], pd.Series]:
    """First quote date of each month (its puts), and the full daily spot."""
    days: dict[pd.Timestamp, pd.DataFrame] = {}
    spots: list[pd.Series] = []
    for frame in _months(vendor, symbol):
        if frame.empty:
            continue
        spots.append(frame.groupby("quote_date")["spot"].first())
        first = frame["quote_date"].min()
        day = frame[frame["quote_date"] == first].copy()
        day["dte"] = (day["expiration"] - day["quote_date"]).dt.days
        days[pd.Timestamp(first)] = day[["expiration", "strike", "spot", "iv", "dte"]]
    spot = pd.concat(spots).sort_index()
    return days, spot[~spot.index.duplicated()]


def load_vix(vendor: Path) -> pd.Series:
    closes = [
        f.groupby("quote_date")["spot"].first() for f in _months(vendor, "vix") if not f.empty
    ]
    vix = pd.concat(closes).sort_index()
    return vix[~vix.index.duplicated()]


def _median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else float("nan")


def report(symbol: str, rows: list[GapRow]) -> str:
    lines = [f"{symbol.upper()}: model = delta at 20-day realised vol, market = our delta on P_IV"]
    # model% is over every day; market% and the gap only over days with a
    # market answer (the gap is the median of each day's own difference).
    lines.append("target  regime    days  answered  unreach  model%  market%  gap(pp)")
    for target in TARGETS:
        for regime in ("calm", "elevated", "crisis"):
            sel = [r for r in rows if r.target == target and r.regime == regime]
            if not sel:
                continue
            got = [r for r in sel if r.market_pct is not None]
            unreach = sum(not r.reachable for r in sel)
            model = _median([r.model_pct for r in sel])
            market = _median([r.market_pct for r in got if r.market_pct is not None])
            gap = _median([r.market_pct - r.model_pct for r in got if r.market_pct is not None])
            lines.append(
                f"{target:5.2f}   {regime:8s} {len(sel):5d}  {len(got):8d}  {unreach:7d}  "
                f"{model:6.2f}  {market:7.2f}  {gap:7.2f}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Checked by hand: argparse tests a nargs="*" default against `choices`
    # as one value, so `make delta-strike-gap` (no names) would be refused.
    parser.add_argument("symbols", nargs="*", help=f"any of {', '.join(SYMBOLS)} (default: all)")
    parser.add_argument("--vendor", type=Path, default=VENDOR)
    args = parser.parse_args(argv)
    symbols = args.symbols or list(SYMBOLS)
    refused = sorted(set(symbols) - set(SYMBOLS))
    if refused:
        parser.error(f"not split-free over 2010-2023: {refused}; choose from {list(SYMBOLS)}")
    if not any(args.vendor.glob("*_eod_*.7z")):
        print(f"SKIPPED -- no optionsDX archives in {args.vendor} (licence-limited, never on CI)")
        return 0
    vix = load_vix(args.vendor)
    for symbol in symbols:
        days, spot = load(args.vendor, symbol)
        print(report(symbol, gap_rows(days, spot, vix, q=FLAT_Q.get(symbol, 0.0))))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
