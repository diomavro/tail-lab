"""The per-name page's data-coverage grid: how many of the sessions a name
should have, it has, per calendar year and dataset.

Cheap by construction -- three small bronze reads and one memoised snapshot,
no scan of a quote table. In particular it never reads ``option_chain_snapshot``
or the optionsDX panel (so, unlike the backtests, it reads OHLCV alone, not
``load_asof_series``, whose SPY/QQQ pre-history splice consults the panel):
whether a name has collected *quotes* is a membership test, and the depth of
those quotes is the Marks route's job (``api/marks_routes.py``).

"Expected" is SPY's sessions in that year, from the same OHLCV snapshot, so a
name listed mid-year or missing a stretch shows as present < expected. A name
session SPY lacks is not counted (present never exceeds expected).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from tail_lab.api.putlab_routes import get_lake_store as _putlab_lake_store
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event
from tail_lab.research.backtest.put_roll import load_ohlcv_series
from tail_lab.research.dividends import session_dates
from tail_lab.research.quote_coverage import COVERAGE_SYMBOLS
from tail_lab.research.regimes.timeline import load_vix_close

router = APIRouter()
logger = logging.getLogger("tail_lab.api.coverage")

#: The calendar every name is measured against.
REFERENCE_SYMBOL = "spy"


class CoverageYear(BaseModel):
    year: int
    sessions_present: int
    #: SPY's session count for the year, or ``None`` when SPY's own OHLCV
    #: does not cover the WHOLE year (before its history starts, or the
    #: partial first year) -- there is then no honest denominator, and the
    #: year is shown as "present, no reference" rather than as complete.
    sessions_expected: int | None


class SymbolCoverage(BaseModel):
    symbol: str
    as_of: dt.date
    #: Per calendar year, oldest first; empty when the lake holds no rows.
    ohlcv: list[CoverageYear]
    tiingo_eod: list[CoverageYear]
    #: Shared by every name: the volatility index the regimes come from.
    vix: list[CoverageYear]
    #: Whether the lake collects put quotes for this name (see the Marks
    #: route for their depth); membership only, no quote table is read.
    quotes_collected: bool


def get_lake_store() -> LakeStore:
    # Overridden in tests via dependency_overrides (which bypasses this call).
    # The SAME cached store the Put Lab routes use: a fresh store per request
    # had no frame cache, so every name view paid three cold lake reads
    # (measured 3.9-5.1 s per request against Tigris, 2026-11-01).
    return _putlab_lake_store()


def _full_reference_years(reference: pd.DatetimeIndex) -> set[int]:
    """Years SPY's OHLCV covers from their first week: its first year is
    partial (history starts mid-year) unless it starts in early January."""
    years = set(int(y) for y in reference.year.unique())
    first = reference.min()
    if first.month > 1 or first.day > 7:
        years.discard(int(first.year))
    return years


def _by_year(dates: pd.DatetimeIndex | None, reference: pd.DatetimeIndex) -> list[CoverageYear]:
    """Per calendar year the dataset OR SPY covers, oldest first.

    Where SPY covers the whole year, ``present`` counts the dataset's dates on
    SPY sessions (so it can never exceed ``expected``). Where it does not (the
    dataset reaches back before SPY's OHLCV, or SPY's partial first year),
    ``present`` is the dataset's own date count and ``expected`` is ``None``:
    the grid must show history the lake holds, not cut it at SPY's start.
    ``None`` (no rows at all) is an empty grid."""
    if dates is None:
        return []
    full = _full_reference_years(reference)
    expected = reference.year.value_counts()
    on_reference = dates.intersection(reference).year.value_counts()
    own = dates.year.value_counts()
    years = sorted(full | set(int(y) for y in own.index))
    return [
        CoverageYear(
            year=year,
            sessions_present=int(on_reference.get(year, 0) if year in full else own.get(year, 0)),
            sessions_expected=int(expected[year]) if year in full else None,
        )
        for year in years
    ]


def _ohlcv_sessions(store: LakeStore, symbol: str, as_of: dt.date) -> pd.DatetimeIndex | None:
    try:
        prices, _ = load_ohlcv_series(store, symbol, as_of)
    except LookupError:
        return None
    return pd.DatetimeIndex(prices.index)


@router.get("/api/putlab/coverage/{symbol}")
def putlab_coverage(
    symbol: Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")],
    as_of: dt.date | None = None,
    store: LakeStore = Depends(get_lake_store),
) -> SymbolCoverage:
    """Sessions present vs expected per calendar year for ``symbol``'s OHLCV
    and Tiingo end-of-day rows, plus the shared VIX row. 404 when the lake
    holds neither dataset for the name (or has no SPY calendar to compare to)."""
    started = time.monotonic()
    name = symbol.lower()
    resolved = as_of or dt.datetime.now(dt.UTC).date()
    reference = _ohlcv_sessions(store, REFERENCE_SYMBOL, resolved)
    if reference is None:
        raise HTTPException(status_code=404, detail="no SPY calendar known as of that date")
    own = reference if name == REFERENCE_SYMBOL else _ohlcv_sessions(store, name, resolved)
    tiingo = session_dates(store, name, resolved)
    if own is None and tiingo is None:
        raise HTTPException(status_code=404, detail=f"no OHLCV or Tiingo rows for {name!r}")
    try:
        vix: pd.DatetimeIndex | None = pd.DatetimeIndex(load_vix_close(store, as_of=resolved).index)
    except LookupError:
        vix = None
    coverage = SymbolCoverage(
        symbol=name,
        as_of=resolved,
        ohlcv=_by_year(own, reference),
        tiingo_eod=_by_year(tiingo, reference),
        vix=_by_year(vix, reference),
        quotes_collected=name in COVERAGE_SYMBOLS,
    )
    log_event(
        logger,
        "putlab.coverage",
        symbol=name,
        as_of=resolved,
        has_ohlcv=own is not None,
        has_tiingo=tiingo is not None,
        quotes_collected=coverage.quotes_collected,
        seconds=round(time.monotonic() - started, 3),
    )
    return coverage
