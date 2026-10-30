"""The Marks surface's route: where the lake holds a real put quote
(``research/quote_coverage.py``).

The grid moves only when a quote source does -- the optionsDX archive is one
fixed partition, the forward sweep adds a session a day -- so a result is
memoised per (symbol, as_of), and computed under one lock: each build scans a
quote table inside DuckDB, and two at once is the kind of doubled peak that
has OOMed the 1 GB machine before (``OptionsDxQuoteSource``). The memo is
bounded, because ``as_of`` is caller-chosen and every value is a new key.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.lake.store import LAKE_READ_ERRORS, LakeStore
from tail_lab.observability import log_event
from tail_lab.research.quote_coverage import (
    COVERAGE_SYMBOLS,
    QuoteCoverage,
    compute_quote_coverage,
)

router = APIRouter()
logger = logging.getLogger("tail_lab.api.marks")

#: A pinned past as_of never changes; it is held for hours.
_TTL_PINNED_S = 6 * 3600.0
#: "Today" gains a partition at the 21:30 UTC sweep, so it is re-read soon.
_TTL_TODAY_S = 1800.0
#: A lake fault: the Delta/Arrow/OS families, plus DuckDB's own scan errors.
_READ_ERRORS: tuple[type[Exception], ...] = (*LAKE_READ_ERRORS, duckdb.Error)
#: Most entries held at once; past it the soonest-to-expire is dropped.
_MAX_ENTRIES = 32
_CACHE: dict[tuple[str, dt.date], tuple[float, QuoteCoverage]] = {}
_LOCK = threading.Lock()


def get_lake_store() -> LakeStore:
    # Overridden in tests via dependency_overrides (which bypasses this call).
    return _get_configured_lake_store()


def _remember(key: tuple[str, dt.date], ttl: float, coverage: QuoteCoverage) -> None:
    now = time.monotonic()
    for stale in [k for k, (expires, _) in _CACHE.items() if expires <= now]:
        del _CACHE[stale]
    while len(_CACHE) >= _MAX_ENTRIES:
        del _CACHE[min(_CACHE, key=lambda k: _CACHE[k][0])]
    _CACHE[key] = (now + ttl, coverage)


@router.get("/api/putlab/marks")
def putlab_marks(
    symbol: str = Query(description="Underlying ticker, e.g. spy."),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> QuoteCoverage:
    """Sessions of usable put quotes by month, depth out of the money and
    tenor, and by the regime each session was in."""
    name = symbol.lower()
    if name not in COVERAGE_SYMBOLS:
        raise HTTPException(status_code=422, detail=f"no quote coverage for {name!r}")
    resolved = as_of or dt.datetime.now(dt.UTC).date()
    key = (name, resolved)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        started = time.monotonic()
        try:
            coverage = compute_quote_coverage(store, symbol=name, as_of=resolved)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except _READ_ERRORS as exc:
            log_event(
                logger, "putlab.marks.read_failed", symbol=name, as_of=resolved, error=repr(exc)
            )
            raise HTTPException(status_code=503, detail="the quote lake could not be read") from exc
        # By the date read, not by how it was asked for: an explicit as_of of
        # today shares the key, and must not pin today's grid for hours.
        today = dt.datetime.now(dt.UTC).date()
        _remember(key, _TTL_TODAY_S if resolved >= today else _TTL_PINNED_S, coverage)
    log_event(
        logger,
        "putlab.marks.computed",
        symbol=name,
        as_of=resolved,
        sources=[s.source for s in coverage.sources],
        sessions=sum(s.sessions for s in coverage.sources),
        seconds=round(time.monotonic() - started, 2),
    )
    return coverage
