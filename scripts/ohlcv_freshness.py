"""Which names the daily refresh pulls OHLCV for, and whether any fell behind.

Measured 2026-11-04 against the deployed lake: 46 of the 70 screening names
held 178 of 210 sessions for 2026 while the 24 chain-sweep names held 210.
``local_daily_refresh.sh`` had defaulted its OHLCV list to the chain sweep
(``DEFAULT_SNAPSHOT_SYMBOLS``) since it was written, so the rest of the
screening universe (``options_calendar``) was ingested once by hand and never
again. Nothing noticed for ~32 sessions, because 178/210 still clears
``ranking.MIN_WINDOW_COVERAGE``: the stale names were RANKED on stale prices,
not skipped.

Two modes, both used by the refresh script:

``--list``
    Print the refresh universe: the screening universe united with the chain
    sweep, so a collect-only chain name (ARM) still gets prices.

``--check``
    Exit 1, naming every laggard, when a universe name's newest bar is more
    than ``MAX_LAG_SESSIONS`` SPY sessions behind SPY's. SPY is the yardstick,
    so it is itself checked against Nasdaq's newest completed session
    (``latest_market_session``): if SPY froze, every name would look fresh.
    The check always covers the WHOLE universe, never the list a run actually
    looped over -- an override of ``REFRESH_OHLCV_SYMBOLS`` must not recreate
    the blind spot this exists to close.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Iterable

import numpy as np
import pandas as pd

from tail_lab.config import get_lake_store
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.contracts.option_chain import DEFAULT_SNAPSHOT_SYMBOLS
from tail_lab.contracts.options_calendar import universe_symbols
from tail_lab.ingestion.ohlcv import latest_market_session
from tail_lab.lake.store import LakeStore

#: A week of slack: Nasdaq occasionally lags a day on a thin name, and the
#: refresh runs each weekday, so five missed sessions means the name is not
#: being refreshed at all rather than late.
MAX_LAG_SESSIONS = 5
#: The yardstick. Swept daily, so its bars define the trading calendar here.
REFERENCE = "spy"
#: How far SPY may trail Nasdaq's newest session (see the same-day re-run note).
SPY_SLACK_BUSINESS_DAYS = 1


def refresh_symbols() -> tuple[str, ...]:
    """The screening universe plus every chain-swept name, lower-cased, sorted."""
    return tuple(sorted(set(universe_symbols()) | {s.lower() for s in DEFAULT_SNAPSHOT_SYMBOLS}))


def _newest_bar(store: LakeStore, symbol: str, as_of: dt.date) -> pd.Timestamp | None:
    try:
        bars = store.read_bronze_as_of(dataset_id(symbol), as_of)
    except LookupError:
        return None
    if bars.empty:
        return None
    return pd.Timestamp(bars["trade_date"].max())


def stale_symbols(
    store: LakeStore,
    symbols: Iterable[str],
    *,
    as_of: dt.date,
    market_session: dt.date | None,
) -> list[str]:
    """One line per failure; empty means every name is current."""
    try:
        reference = store.read_bronze_as_of(dataset_id(REFERENCE), as_of)
    except LookupError:
        return [f"{REFERENCE}: no OHLCV in bronze -- the yardstick itself is missing"]
    calendar = pd.DatetimeIndex(sorted(reference["trade_date"].unique()))
    failures: list[str] = []
    # One business day of slack: bronze is immutable, so a SAME-DAY re-run
    # after the close keeps the morning's SPY partition (yesterday's bar)
    # while Nasdaq already reports today -- a false red on exactly the re-run
    # the failure message asks for. A real freeze grows past one day. (Over
    # a holiday the slack is calendar business days, so the edge errs red.)
    if (
        market_session is not None
        and int(np.busday_count(calendar[-1].date(), market_session)) > SPY_SLACK_BUSINESS_DAYS
    ):
        failures.append(
            f"{REFERENCE}: newest bar {calendar[-1].date()} is behind Nasdaq's "
            f"newest session {market_session} -- every comparison below is suspect"
        )
    for symbol in symbols:
        if symbol == REFERENCE:
            continue
        newest = _newest_bar(store, symbol, as_of)
        if newest is None:
            failures.append(f"{symbol}: no OHLCV in bronze")
            continue
        lag = int((calendar > newest).sum())
        if lag > MAX_LAG_SESSIONS:
            failures.append(
                f"{symbol}: newest bar {newest.date()} is {lag} sessions behind {REFERENCE}"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        print(" ".join(refresh_symbols()))
        return 0
    session = latest_market_session()
    if session is None:
        print(f"warning: Nasdaq unreachable; {REFERENCE} itself is not checked this run")
    failures = stale_symbols(
        get_lake_store(),
        refresh_symbols(),
        as_of=dt.datetime.now(dt.UTC).date(),
        market_session=session,
    )
    for line in failures:
        print(f"STALE {line}")
    if failures:
        return 1
    print(
        f"ohlcv fresh: {len(refresh_symbols())} names within {MAX_LAG_SESSIONS} sessions of {REFERENCE}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
