"""Point-in-time dividend yields for pricing: the one place research reads
``tiingo_eod`` (`docs/DATA_CONTRACTS.md` #14).

Every pricing entry point that holds the store -- a single backtest, the
universe ranking, a portfolio, the metric screen, the sweep, the roll
schedule, the Surface -- asks :func:`dividend_lookup` for a symbol and passes
the returned callable down to the pure pricing code. One reader, so the
headline and its siblings can never price on different dividend bases.

Reading cost: one projected bronze read per snapshot, then every symbol's
:class:`~tail_lab.transforms.dividend_yield.DividendYields` is built once and
memoised on the lake and snapshot id (the store's own snapshot-id cache makes
that lookup cheap), rather than copying the ~400k-row frame per request.

Absence is a value, never an error: before the first Tiingo ingest (no key,
CI, test lakes) or for a symbol ingestion withheld, every date reads
``q = 0`` labelled ``unknown``. A store *bug* is not absence -- see
:func:`_read`.
"""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from tail_lab.contracts.tiingo_eod import DATASET
from tail_lab.lake.store import LakeStore
from tail_lab.transforms.dividend_yield import (
    DividendYield,
    DividendYields,
    build_dividend_yields,
)

__all__ = [
    "UNKNOWN",
    "DividendLookup",
    "SymbolDividends",
    "dividend_lookup",
    "dividend_snapshot_id",
]

#: ``q`` on a date for one symbol.
DividendLookup = Callable[[dt.date], DividendYield]

UNKNOWN = DividendYield(0.0, "unknown", 0, (), None)

_COLUMNS = ["symbol", "trade_date", "close", "div_cash", "split_factor"]
#: Two, not more: only the newest snapshot serves today's prices, and each
#: holds a ~60 MB table plus its session cache on a 1 GB VM. The second
#: covers a request for an as_of before the latest weekly ingest.
_MEMO_MAX = 2
_lock = threading.Lock()
#: Never pruned, on purpose: a pruned key's old lock can still be held by a
#: thread that fetched it before the prune, so a re-request on a fresh lock
#: would build the same snapshot twice at once (review round 6 reproduced it).
#: Unpruned, this grows by one tiny lock per (lake, weekly snapshot) seen.
_build_locks: dict[tuple[str, str], threading.Lock] = {}
#: Keyed on (lake location, snapshot id): a snapshot id is unique within one
#: lake only -- its digest is column statistics, and two lakes (tests, or a
#: scratch lake beside production) can share one.
_memo: dict[tuple[str, str], Mapping[str, DividendYields]] = {}


@dataclass(frozen=True)
class SymbolDividends:
    """A symbol's yield lookup plus the snapshot it came from (for a
    result's ``snapshot_ids``; ``None`` when there was no snapshot)."""

    lookup: DividendLookup
    snapshot_id: str | None


def _key_lock(key: tuple[str, str]) -> threading.Lock:
    with _lock:
        return _build_locks.setdefault(key, threading.Lock())


def _unknown(_day: dt.date) -> DividendYield:
    return UNKNOWN


def _read(store: LakeStore, as_of: dt.date) -> tuple[Mapping[str, DividendYields], str] | None:
    try:
        snapshot_id = store.bronze_snapshot_id(DATASET, as_of)
    except KeyError:
        # A LookupError subclass, but a store bug (a missing column), not an
        # absent snapshot: never let it become a silent q = 0.
        raise
    except LookupError:
        return None
    key = (store.location, snapshot_id)
    with _lock:
        cached = _memo.get(key)
    if cached is None:
        # Single flight: the universe ranking asks from 8 threads at once, and
        # on a cold cache (every deploy, every weekly ingest) each would read
        # and build the ~400k-row table -- measured +562 MB peak on a 1 GB VM.
        # One builds; the rest wait on this key's lock and then find it
        # memoised. Per key, so a cold read for one lake or snapshot never
        # queues behind another's slow S3 read.
        with _key_lock(key):
            with _lock:
                cached = _memo.get(key)
            if cached is None:
                frame = store.read_bronze_columns_as_of(DATASET, as_of, _COLUMNS)
                cached = build_dividend_yields(frame)
                with _lock:
                    if len(_memo) >= _MEMO_MAX:
                        _memo.pop(next(iter(_memo)))
                    _memo[key] = cached
    return cached, snapshot_id


def dividend_snapshot_id(store: LakeStore, as_of: dt.date) -> str | None:
    """The ``tiingo_eod`` snapshot known on ``as_of`` (``None`` without one),
    for a result's ``snapshot_ids``."""
    found = _read(store, as_of)
    return None if found is None else found[1]


def dividend_lookup(store: LakeStore, symbol: str, as_of: dt.date) -> SymbolDividends:
    """``symbol``'s point-in-time yield lookup from the snapshot known on
    ``as_of``. Dates after ``as_of`` are never answered from rows past it:
    the snapshot itself ends at or before ``as_of``, and
    :meth:`DividendYields.at` reads only rows on or before the date asked."""
    found = _read(store, as_of)
    if found is None:
        return SymbolDividends(_unknown, None)
    yields, snapshot_id = found
    series = yields.get(symbol.lower())
    if series is None:
        return SymbolDividends(_unknown, snapshot_id)
    return SymbolDividends(series.at, snapshot_id)
