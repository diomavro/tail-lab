"""Single-source price data-quality checks (``docs/END_STATE.md`` §2).

A free *independent* second price source isn't reachable keylessly today
(Stooq now gates downloads behind a JavaScript proof-of-work; Tiingo/Polygon
need keys — see ``HUMAN_TODO.md``). So instead of cross-source reconciliation,
this scans the one source we have for the failure mode that actually threatens
a verdict: **a bad tick** — a single bar that spikes and reverts, or a frozen
run of identical prices.

Crucially it does NOT flag real crashes: a genuine -30% day *persists* (or
keeps falling), whereas a bad tick reverts the next bar. The spike-and-revert
condition keys on exactly that difference, so a real COVID/2022 move is left
alone while a print error is caught.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import LakeStore

#: A single-bar move this large that then reverts is treated as a bad tick.
SPIKE_TOL = 0.35
#: The reversion must bring the net 2-bar move to <= this fraction of the
#: spike, i.e. the price came back near where it started.
REVERT_FRAC = 0.6
#: This many identical consecutive closes reads as a frozen/stale feed.
STALE_RUN = 4


class AnomalyFlag(BaseModel):
    date: dt.date
    kind: str  # "spike" | "stale"
    detail: str


class DataQualityReport(BaseModel):
    asset: str
    as_of: dt.date
    n_bars: int
    n_suspicious: int
    flags: list[AnomalyFlag]


def scan_price_anomalies(
    prices: pd.Series,
    *,
    spike_tol: float = SPIKE_TOL,
    revert_frac: float = REVERT_FRAC,
    stale_run: int = STALE_RUN,
) -> list[AnomalyFlag]:
    """Flag bad ticks (spike-and-revert) and stale runs in an adjusted-close
    series indexed by trade date. Pure; a real (persisting) crash is not
    flagged because it doesn't revert on the next bar."""
    px = prices.to_numpy(dtype=float)
    dates = [d.date() if isinstance(d, pd.Timestamp) else d for d in prices.index]
    flags: list[AnomalyFlag] = []

    for t in range(1, len(px) - 1):
        prev, cur, nxt = px[t - 1], px[t], px[t + 1]
        if prev <= 0 or cur <= 0:
            continue
        r_in = cur / prev - 1.0
        r_out = nxt / cur - 1.0
        net = abs(nxt / prev - 1.0)
        if (
            abs(r_in) >= spike_tol
            and r_in * r_out < 0  # reverses direction
            and net <= (1.0 - revert_frac) * abs(r_in)  # comes back near the start
        ):
            flags.append(
                AnomalyFlag(
                    date=dates[t],
                    kind="spike",
                    detail=f"{r_in * 100:+.0f}% spike reverting {r_out * 100:+.0f}% next bar",
                )
            )

    # Stale: a run of >= stale_run identical consecutive closes.
    run_start = 0
    for t in range(1, len(px) + 1):
        if t < len(px) and px[t] == px[t - 1]:
            continue
        run_len = t - run_start
        if run_len >= stale_run:
            flags.append(
                AnomalyFlag(
                    date=dates[run_start],
                    kind="stale",
                    detail=f"{run_len} identical consecutive closes at {px[run_start]:.2f}",
                )
            )
        run_start = t

    flags.sort(key=lambda f: f.date)
    return flags


def _close(bronze: pd.DataFrame) -> pd.Series:
    """Raw closing prices — the series this scanner must check.

    **Raw ``close``, not ``adj_close``, and the distinction has teeth here.**
    This module hunts *print errors*, so it has to look at what was actually
    printed; ``adj_close`` is a derived series, one indirection away. Three
    concrete consequences of getting this wrong:

    1. The stale-feed check below flags ``STALE_RUN`` consecutive **identical**
       closes. ``adj_close`` carries a cumulative adjustment factor that steps
       at every ex-dividend date, so a genuinely frozen feed spanning an
       ex-div date yields four *slightly different* adjusted values, the run
       is broken, and the freeze is silently missed. SPY goes ex-dividend
       quarterly, so this is a live false negative, not a hypothetical.
    2. It would guard a series nothing reads: the backtester prices off raw
       ``close`` (``research/backtest/put_roll.py``).
    3. ``adj_close`` is vendor-dependent — Nasdaq sets it equal to ``close``,
       Yahoo does not (`docs/DISCOVERIES.md` §2) — so the scanner's input
       would change meaning with whichever source served the partition. That
       is the worst possible property for a guard.

    The spike-and-revert check is genuinely indifferent: dividend adjustment
    is smooth and multiplicative, so a 35% spike appears in both series.
    """
    ordered = bronze.sort_values("trade_date").drop_duplicates(subset="trade_date", keep="last")
    return pd.Series(
        ordered["close"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(ordered["trade_date"]),
    )


def assess_asset_quality(store: LakeStore, *, asset: str, as_of: dt.date) -> DataQualityReport:
    """Data-quality report for ``asset`` as of ``as_of`` — point-in-time (reads
    only the bronze snapshot known on or before ``as_of``). Raises
    ``LookupError`` if there is no snapshot."""
    try:
        bronze = store.read_bronze_as_of(dataset_id(asset), as_of)
    except LookupError as exc:
        raise LookupError(f"no OHLCV for {asset} known as of {as_of.isoformat()}") from exc
    if bronze.empty:
        raise LookupError(f"no OHLCV for {asset} known as of {as_of.isoformat()}")
    prices = _close(bronze)
    flags = scan_price_anomalies(prices)
    return DataQualityReport(
        asset=asset, as_of=as_of, n_bars=len(prices), n_suspicious=len(flags), flags=flags
    )
