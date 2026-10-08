"""A result memo for reads too slow to compute on a visitor's request.

The universe ranking backtests ~70 names on a shared-cpu-1x machine and takes
minutes cold. Its old memo was a plain 120 s TTL, so almost every visit
landed on an expired entry and waited the full screen, and every concurrent
caller started a screen of its own -- which, on one CPU, slowed all of them
and failed Fly's health check (2026-10-08). Three changes, each logged
(docs/STANDARDS.md §f):

* **Single flight.** Callers that miss on the same key wait for one compute
  instead of starting their own.
* **Stale while revalidate.** Past ``fresh_s`` an entry is still served at
  once while ONE background refresh recomputes it, so a just-landed ingest
  shows up after one refresh without anyone waiting on it. Past
  ``serve_stale_s`` it is recomputed in the caller, as a miss.
* **Warm.** :meth:`RefreshingMemo.warm` starts a background compute for a key
  nobody has asked for yet -- the app's startup uses it, since every deploy
  restarts the process with an empty memo.

Keys must carry everything the value depends on that can change the answer
(for the ranking: its parameters and the as-of date), because a stale entry
is served without recomputing.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass

from tail_lab.observability import log_event

logger = logging.getLogger("tail_lab.api.memo")


def _spawn_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


@dataclass(frozen=True)
class _Entry[V]:
    value: V
    computed_at: float


class RefreshingMemo[K: Hashable, V]:
    """Memo with single-flight computes, stale-while-revalidate and warming.

    ``clock`` and ``spawn`` are injectable so tests can step time and run
    background work deterministically.
    """

    def __init__(
        self,
        *,
        name: str,
        fresh_s: float,
        serve_stale_s: float,
        clock: Callable[[], float] = time.monotonic,
        spawn: Callable[[Callable[[], None]], None] = _spawn_thread,
    ) -> None:
        if serve_stale_s < fresh_s:
            raise ValueError("serve_stale_s must be at least fresh_s")
        self._name = name
        self._fresh_s = fresh_s
        self._serve_stale_s = serve_stale_s
        self._clock = clock
        self._spawn = spawn
        self._lock = threading.Lock()
        self._entries: dict[K, _Entry[V]] = {}
        #: Keys being computed now, each with the event its waiters block on.
        self._inflight: dict[K, threading.Event] = {}
        #: Background refreshes run one at a time across ALL keys: each one is
        #: a whole screen on a single shared CPU, and several readers on
        #: several keys would otherwise screen at once. Callers that miss still
        #: compute in their own request.
        self._background_slot = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get(self, key: K, compute: Callable[[], V]) -> V:
        """The value for ``key``: memoised, stale-served, or computed once."""
        while True:
            with self._lock:
                entry = self._entries.get(key)
                age = None if entry is None else self._clock() - entry.computed_at
                if entry is not None and age is not None and age < self._serve_stale_s:
                    if age >= self._fresh_s:
                        self._start_background(key, compute, reason="stale")
                    return entry.value
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    leader = True
                else:
                    leader = False
            if leader:
                return self._compute_and_store(key, compute, event, reason="miss")
            event.wait()
            # Round again: the value if the compute stored one; if it failed,
            # exactly one waiter becomes the next leader and the rest wait on
            # it -- never every waiter screening at once.

    def warm(self, key: K, compute: Callable[[], V]) -> None:
        """Start a background compute for ``key`` unless one exists or runs."""
        with self._lock:
            if key in self._entries:
                return
            self._start_background(key, compute, reason="warm")

    def _start_background(self, key: K, compute: Callable[[], V], *, reason: str) -> None:
        """Spawn one refresh for ``key``. Caller holds the lock."""
        if key in self._inflight:
            return
        event = threading.Event()

        def run() -> None:
            try:
                with self._background_slot:
                    self._compute_and_store(key, compute, event, reason=reason)
            except Exception:
                # Deliberately broad (Rule 14's process-boundary case): this is
                # a background worker with no caller to raise to. Not silent --
                # the traceback is logged; the stale entry, if any, keeps being
                # served, and the next stale read tries again.
                logger.exception(
                    "event=memo.refresh_failed memo=%s key=%s reason=%s", self._name, key, reason
                )

        try:
            self._spawn(run)
        except RuntimeError:
            # "can't start new thread": nothing will ever set this event, so it
            # must not be registered -- a key stuck in flight hangs every
            # later miss. The stale entry, if any, keeps being served.
            logger.exception(
                "event=memo.spawn_failed memo=%s key=%s reason=%s", self._name, key, reason
            )
            return
        self._inflight[key] = event

    def _compute_and_store(
        self, key: K, compute: Callable[[], V], event: threading.Event, *, reason: str
    ) -> V:
        started = self._clock()
        log_event(logger, "memo.compute_start", memo=self._name, key=key, reason=reason)
        try:
            value = compute()
        except BaseException:
            with self._lock:
                self._inflight.pop(key, None)
            event.set()
            raise
        # Store BEFORE releasing the waiters: a waiter woken onto an empty
        # memo would compute again, which is the duplicate work this prevents.
        with self._lock:
            now = self._clock()
            # Drop what can no longer be served, so a long-running process does
            # not keep one entry per day per strike the readers ever clicked.
            for old in [
                k for k, e in self._entries.items() if now - e.computed_at >= self._serve_stale_s
            ]:
                del self._entries[old]
            self._entries[key] = _Entry(value=value, computed_at=now)
            self._inflight.pop(key, None)
        event.set()
        log_event(
            logger,
            "memo.compute_done",
            memo=self._name,
            key=key,
            reason=reason,
            seconds=round(self._clock() - started, 1),
        )
        return value
