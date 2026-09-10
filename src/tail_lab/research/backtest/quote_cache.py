"""A byte-budgeted, thread-safe cache of quote sources.

Building a :class:`~tail_lab.research.backtest.quote_fills.QuoteSource` is
expensive in both dimensions that matter on a small machine, and the two
failures pull in opposite directions:

* **Too little caching and it is unservable.** ``from_store`` for SPY measured
  13-38 s against S3 -- always past the 5 s health-check timeout in
  ``fly.toml``, and far past any usefulness of the routes' 120 s response TTL
  if paid per request.
* **Too much caching and the machine dies.** Every Put Lab route is a sync
  ``def``, so Starlette runs up to 40 of them in a threadpool. Two SIMULTANEOUS
  constructions were measured at **~1066 MB** against a **1024 MB** cap, and a
  naive per-``(symbol, as_of)`` dict is a monotonic ~300 MB-per-asset leak:
  five optionsDX assets plus ``option_quotes`` measured 682 MB resident, on top
  of the app's own ~252 MB.

So the cache has to bound BYTES, not entries. That is the specific lesson from
``LakeStore._frame_cache``, which is an LRU capped at 256 *entries* -- sized
for the ~1k-row datasets it was written for, and therefore no bound at all once
a single entry can be 300 MB.

Two properties beyond eviction, both load-bearing under a threadpool:

* **One build per key, ever.** A per-key lock means the second thread asking
  for SPY waits for the first thread's build instead of starting its own. That
  is what turns the measured ~1066 MB concurrent peak back into one build's
  worth, and it is the whole reason this is not a plain ``functools.lru_cache``
  (which happily runs the same expensive call on N threads at once).
* **The budget is fed a deliberate OVER-estimate.** A source reports its own
  footprint, and that report is `memory_usage(deep=True)` scaled by a measured
  correction -- it is an estimate, not a measurement, and the two error
  directions are not symmetric. Overstating evicts a little too eagerly and
  costs a rebuild; understating fails to evict at all, which is how three
  sources sat at 94% of a 400 MB budget while the process peaked at 1195 MB on
  a 1024 MB machine.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["DEFAULT_BUDGET_BYTES", "QuoteSourceCache"]

_LOGGER = logging.getLogger(__name__)

#: Total bytes of quote sources allowed resident at once.
#:
#: 400 MB against the machine's 1024 MB (``fly.toml``), measured to leave room
#: for the app's own ~252 MB baseline, the routes' response caches, and the
#: transient cost of building the NEXT source before the old one is evicted --
#: eviction happens after a build, so the true peak is the budget plus one
#: source. One optionsDX panel is ~193 MB and ``option_quotes`` is ~2 MB, so
#: this holds two large panels or one plus everything small.
DEFAULT_BUDGET_BYTES = 400 * 1024 * 1024

#: How many sources may be UNDER CONSTRUCTION at once, across all keys.
#:
#: The per-key lock stops two threads building the same symbol. It does nothing
#: about two threads building DIFFERENT symbols, which is the failure that
#: motivated this class in the first place -- five assets are five keys. With a
#: budget sized for two sources, three concurrent requests for three different
#: symbols were measured peaking at 4.7x the budget.
#:
#: 1, deliberately: a build is 13-38 s of S3 I/O and ~400 MB of transient, so
#: the honest bound is budget + ONE in-flight build. Raising it means
#: `budget + n * max_source` must still clear the cap, and at ~200 MB a source
#: on a 1024 MB machine there is no room for a second.
MAX_CONCURRENT_BUILDS = 1

#: Ceiling on entries, alongside the byte budget. Byte pressure never evicts
#: zero-byte entries (an empty panel reports 0), so without this they
#: accumulate without bound -- `_frame_cache`'s bug inverted.
MAX_ENTRIES = 32


@dataclass
class _Entry[T]:
    value: T
    nbytes: int


class QuoteSourceCache[T]:
    """LRU over a BYTE budget, with one build per key even under threads."""

    def __init__(
        self,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
        max_concurrent_builds: int = MAX_CONCURRENT_BUILDS,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        if budget_bytes <= 0:
            raise ValueError("budget_bytes must be positive")
        if max_concurrent_builds <= 0:
            raise ValueError("max_concurrent_builds must be positive")
        self._budget = budget_bytes
        self._max_entries = max_entries
        #: Bounds builds across DISTINCT keys, which the per-key lock does not.
        #: That gap was the whole original failure: five assets are five keys,
        #: so five builds ran in parallel and the peak was five panels, not one.
        #: Measured with a budget sized for two sources, three concurrent
        #: requests for three different symbols peaked at 4.7x the budget.
        self._build_slots = threading.Semaphore(max_concurrent_builds)
        self._entries: OrderedDict[str, _Entry[T]] = OrderedDict()
        self._resident = 0
        self._guard = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    @property
    def resident_bytes(self) -> int:
        with self._guard:
            return self._resident

    def cached_keys(self) -> list[str]:
        with self._guard:
            return list(self._entries)

    def get_or_build(self, key: str, build: Callable[[], tuple[T, int]]) -> T:
        """Return the cached value for ``key``, building it at most once.

        ``build`` returns ``(value, nbytes)``. It is called OUTSIDE the cache's
        own lock -- holding a global lock across a 13-38 s S3 read would
        serialise every unrelated request on the machine -- but INSIDE a lock
        held per key, so two threads asking for the same symbol produce one
        build and one panel rather than two of each.
        """
        with self._guard:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry.value
            key_lock = self._key_locks.setdefault(key, threading.Lock())

        with self._build_slots, key_lock:
            # Re-check: another thread may have finished building while this
            # one waited on the key lock. Without this the winner's work is
            # discarded and the panel is built twice anyway.
            with self._guard:
                entry = self._entries.get(key)
                if entry is not None:
                    self._entries.move_to_end(key)
                    return entry.value

            value, nbytes = build()
            if nbytes < 0:
                raise ValueError(f"build() reported negative bytes for {key!r}: {nbytes}")

            with self._guard:
                # A single source larger than the whole budget is served but
                # never retained: caching it would evict everything and still
                # overshoot, so the honest answer is to pay for it each time
                # rather than pretend the budget holds.
                if nbytes <= self._budget:
                    # Idempotent: subtract any entry already under this key
                    # before inserting. Without this, a thread that waited on
                    # the key lock while its target was evicted re-inserts and
                    # the byte count is over-stated PERMANENTLY -- reproduced
                    # deterministically at 800 resident against 400 actually
                    # held. The shipped code was correct only because
                    # `_key_locks` was never pruned, an invariant nothing
                    # stated; pruning it (below) is safe only once this is.
                    previous = self._entries.pop(key, None)
                    if previous is not None:
                        self._resident -= previous.nbytes
                    self._entries[key] = _Entry(value, nbytes)
                    self._resident += nbytes
                    self._evict_to_budget()
                else:
                    # Never retained, so there is nothing for the lock to
                    # protect past this call -- no entry means no "second
                    # thread must wait for the first build" scenario worth
                    # remembering. Without this pop, a key that is never
                    # retained never gets its lock pruned either, since
                    # `_drop_oldest` only prunes keys it evicts FROM
                    # `_entries` -- an unbounded `_key_locks` leak on the
                    # exact keys `MAX_ENTRIES` cannot see.
                    self._key_locks.pop(key, None)
                    # Loudly, every time. Without this the symptom is a route
                    # that is inexplicably slow forever -- the source is
                    # rebuilt on EVERY call at 13-38s, and nothing else in the
                    # process would say so. docs/STANDARDS.md §f: "an increment
                    # that adds automated behavior without logging what it does
                    # at runtime is incomplete".
                    _LOGGER.warning(
                        "quote_cache.too_large_to_retain key=%s bytes=%d budget=%d "
                        "-- this source will be REBUILT on every call",
                        key,
                        nbytes,
                        self._budget,
                    )
                return value

    def _evict_to_budget(self) -> None:
        # Entry ceiling as well as a byte budget. Byte pressure alone never
        # fires on zero-byte entries -- an empty panel reports 0 -- so 50,000
        # of them accumulated with `resident == 0`. That is the same
        # unbounded-in-one-dimension bug this class exists to fix in
        # `_frame_cache`, just inverted.
        while len(self._entries) > self._max_entries:
            self._drop_oldest()
        while self._resident > self._budget and len(self._entries) > 1:
            self._drop_oldest()

    def _drop_oldest(self) -> None:
        key, evicted = self._entries.popitem(last=False)
        self._resident -= evicted.nbytes
        self._key_locks.pop(key, None)
        # Eviction is the signal that the budget is actually binding. A cache
        # that thrashes -- evicting the thing it is about to be asked for
        # again -- looks identical to a working one from outside unless it
        # says so.
        _LOGGER.info(
            "quote_cache.evicted key=%s bytes=%d resident=%d budget=%d entries=%d",
            key,
            evicted.nbytes,
            self._resident,
            self._budget,
            len(self._entries),
        )
