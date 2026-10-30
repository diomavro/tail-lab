"""The Marks surface's route: where the lake holds a real put quote
(``research/quote_coverage.py``).

The grid moves only when a quote source does -- the optionsDX archive is one
fixed partition, the forward sweep adds a session a day -- so a result is
memoised per (symbol, as_of) for hours, and computed under one lock: each
build scans a quote table inside DuckDB, and two at once is the kind of
doubled peak that has OOMed the 1 GB machine before (``OptionsDxQuoteSource``).
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event
from tail_lab.research.quote_coverage import QuoteCoverage, compute_quote_coverage

router = APIRouter()
logger = logging.getLogger("tail_lab.api.marks")

_TTL_S = 6 * 3600.0
_CACHE: dict[tuple[str, dt.date], tuple[float, QuoteCoverage]] = {}
_LOCK = threading.Lock()


def get_lake_store() -> LakeStore:
    # Overridden in tests via dependency_overrides (which bypasses this call).
    return _get_configured_lake_store()


@router.get("/api/putlab/marks")
def putlab_marks(
    symbol: str = Query(description="Underlying ticker, e.g. spy."),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> QuoteCoverage:
    """Sessions of usable put quotes by month, depth out of the money and
    tenor, and by the regime each session was in."""
    resolved = as_of or dt.datetime.now(dt.UTC).date()
    key = (symbol.lower(), resolved)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        started = time.monotonic()
        try:
            coverage = compute_quote_coverage(store, symbol=key[0], as_of=resolved)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _CACHE[key] = (time.monotonic() + _TTL_S, coverage)
    log_event(
        logger,
        "putlab.marks.computed",
        symbol=key[0],
        as_of=resolved,
        sources=[s.source for s in coverage.sources],
        sessions=sum(s.sessions for s in coverage.sources),
        seconds=round(time.monotonic() - started, 2),
    )
    return coverage
