"""RefreshingMemo: the ranking's memo (api/memo.py).

Why each behaviour matters: a cold universe screen takes minutes on the app's
one shared CPU. On 2026-10-08 a plain 120 s TTL meant almost every visit
waited the whole screen, and concurrent visits each started their own,
starving the health check. These tests pin the three properties that fix it:
one compute per key at a time, stale entries served at once while a single
background refresh runs, and a warm start.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import pytest

from tail_lab.api.memo import RefreshingMemo


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Spawner:
    """Collects background work so a test runs it when it chooses."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []

    def __call__(self, fn: Callable[[], None]) -> None:
        self.pending.append(fn)

    def run_all(self) -> None:
        while self.pending:
            self.pending.pop(0)()


def make(clock: Clock, spawn: Spawner) -> RefreshingMemo[str, int]:
    return RefreshingMemo(name="t", fresh_s=10.0, serve_stale_s=100.0, clock=clock, spawn=spawn)


def counter(values: list[int]) -> tuple[Callable[[], int], list[int]]:
    calls: list[int] = []

    def compute() -> int:
        calls.append(1)
        return values[len(calls) - 1]

    return compute, calls


def test_a_fresh_entry_is_served_without_recomputing() -> None:
    clock, spawn = Clock(), Spawner()
    memo = make(clock, spawn)
    compute, calls = counter([1, 2])
    assert memo.get("k", compute) == 1
    clock.now = 9.9
    assert memo.get("k", compute) == 1
    assert len(calls) == 1
    assert spawn.pending == []


def test_a_stale_entry_is_served_at_once_while_one_refresh_runs() -> None:
    clock, spawn = Clock(), Spawner()
    memo = make(clock, spawn)
    compute, calls = counter([1, 2])
    memo.get("k", compute)
    clock.now = 50.0
    # Served immediately, not after a recompute...
    assert memo.get("k", compute) == 1
    assert memo.get("k", compute) == 1
    # ...with exactly one refresh queued, however many stale reads arrive.
    assert len(spawn.pending) == 1
    assert len(calls) == 1
    spawn.run_all()
    assert memo.get("k", compute) == 2
    assert len(calls) == 2


def test_past_the_stale_window_the_caller_recomputes() -> None:
    clock, spawn = Clock(), Spawner()
    memo = make(clock, spawn)
    compute, calls = counter([1, 2])
    memo.get("k", compute)
    clock.now = 100.0
    assert memo.get("k", compute) == 2
    assert len(calls) == 2
    assert spawn.pending == []


def test_concurrent_misses_share_one_compute() -> None:
    memo: RefreshingMemo[str, int] = RefreshingMemo(name="t", fresh_s=10.0, serve_stale_s=100.0)
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow() -> int:
        calls.append(1)
        started.set()
        release.wait(5)
        return 7

    results: list[int] = []
    leader = threading.Thread(target=lambda: results.append(memo.get("k", slow)))
    leader.start()
    assert started.wait(5)
    followers = [
        threading.Thread(target=lambda: results.append(memo.get("k", slow))) for _ in range(4)
    ]
    for t in followers:
        t.start()
    release.set()
    for t in [leader, *followers]:
        t.join(5)
    assert results == [7] * 5
    assert len(calls) == 1


def test_a_failed_compute_stores_nothing_and_the_next_caller_retries() -> None:
    clock, spawn = Clock(), Spawner()
    memo = make(clock, spawn)

    def boom() -> int:
        raise LookupError("no data")

    with pytest.raises(LookupError):
        memo.get("k", boom)
    compute, calls = counter([3])
    assert memo.get("k", compute) == 3
    assert len(calls) == 1


def test_a_failed_background_refresh_keeps_serving_the_stale_value_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock, spawn = Clock(), Spawner()
    memo = make(clock, spawn)
    memo.get("k", lambda: 1)
    clock.now = 50.0

    def boom() -> int:
        raise RuntimeError("lake read failed")

    assert memo.get("k", boom) == 1
    with caplog.at_level(logging.ERROR, logger="tail_lab.api.memo"):
        spawn.run_all()
    assert "memo.refresh_failed" in caplog.text
    assert "lake read failed" in caplog.text
    assert memo.get("k", boom) == 1  # still served; a new refresh is queued
    assert len(spawn.pending) == 1


def test_warm_computes_in_the_background_once() -> None:
    clock, spawn = Clock(), Spawner()
    memo = make(clock, spawn)
    compute, calls = counter([5])
    memo.warm("k", compute)
    memo.warm("k", compute)  # already in flight: not a second screen
    assert len(spawn.pending) == 1
    spawn.run_all()
    assert memo.get("k", compute) == 5
    memo.warm("k", compute)  # already cached
    assert spawn.pending == []
    assert len(calls) == 1


def test_a_miss_waits_for_a_warm_compute_already_running() -> None:
    memo: RefreshingMemo[str, int] = RefreshingMemo(name="t", fresh_s=10.0, serve_stale_s=100.0)
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow() -> int:
        calls.append(1)
        started.set()
        release.wait(5)
        return 9

    memo.warm("k", slow)
    assert started.wait(5)
    result: list[int] = []
    reader = threading.Thread(target=lambda: result.append(memo.get("k", slow)))
    reader.start()
    release.set()
    reader.join(5)
    assert result == [9]
    assert len(calls) == 1


def test_entries_past_the_stale_window_are_pruned() -> None:
    clock, spawn = Clock(), Spawner()
    memo = make(clock, spawn)
    memo.get("old", lambda: 1)
    clock.now = 150.0
    memo.get("new", lambda: 2)
    compute, calls = counter([3])
    assert memo.get("old", compute) == 3  # recomputed: the old entry was dropped
    assert len(calls) == 1


def test_the_stale_window_cannot_be_shorter_than_the_fresh_one() -> None:
    with pytest.raises(ValueError, match="serve_stale_s"):
        RefreshingMemo(name="t", fresh_s=10.0, serve_stale_s=5.0)


def test_a_failed_leader_hands_over_to_one_waiter_not_to_all() -> None:
    """One transient error mid-screen must not turn every queued reader into
    a screen of its own -- the stampede single flight exists to stop."""
    memo: RefreshingMemo[str, int] = RefreshingMemo(name="t", fresh_s=10.0, serve_stale_s=100.0)
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    running = [0]
    peak = [0]
    calls: list[int] = []

    def compute() -> int:
        with lock:
            calls.append(1)
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            first = len(calls) == 1
        try:
            if first:
                started.set()
                release.wait(5)
                raise OSError("transient lake read")
            return 4
        finally:
            with lock:
                running[0] -= 1

    results: list[object] = []

    def read() -> None:
        try:
            results.append(memo.get("k", compute))
        except OSError as exc:
            results.append(exc)

    leader = threading.Thread(target=read)
    leader.start()
    assert started.wait(5)
    waiters = [threading.Thread(target=read) for _ in range(6)]
    for t in waiters:
        t.start()
    release.set()
    for t in [leader, *waiters]:
        t.join(5)
    assert not any(t.is_alive() for t in [leader, *waiters])
    assert len(calls) == 2  # the failed one, then exactly one retry
    assert peak[0] == 1  # never two screens at once
    assert sum(isinstance(r, OSError) for r in results) == 1
    assert results.count(4) == 6


def test_a_refresh_whose_thread_cannot_start_does_not_wedge_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()

    def no_threads(fn: Callable[[], None]) -> None:
        raise RuntimeError("can't start new thread")

    memo: RefreshingMemo[str, int] = RefreshingMemo(
        name="t", fresh_s=10.0, serve_stale_s=100.0, clock=clock, spawn=no_threads
    )
    memo.get("k", lambda: 1)
    clock.now = 50.0
    with caplog.at_level(logging.ERROR, logger="tail_lab.api.memo"):
        assert memo.get("k", lambda: 2) == 1  # stale still served, no raise
    assert "memo.spawn_failed" in caplog.text
    clock.now = 150.0
    # Past the stale window the miss computes rather than waiting forever on
    # a refresh that never started.
    assert memo.get("k", lambda: 3) == 3


def test_background_refreshes_run_one_at_a_time_across_keys() -> None:
    """Each refresh is a whole screen on one CPU; readers on several keys
    must not set several running at once."""
    clock = Clock()
    threads: list[threading.Thread] = []

    def spawn(fn: Callable[[], None]) -> None:
        t = threading.Thread(target=fn)
        threads.append(t)
        t.start()

    memo: RefreshingMemo[str, int] = RefreshingMemo(
        name="t", fresh_s=10.0, serve_stale_s=100.0, clock=clock, spawn=spawn
    )
    for k in ("a", "b", "c"):
        memo.get(k, lambda: 0)
    clock.now = 50.0
    lock = threading.Lock()
    running = [0]
    peak = [0]

    def slow() -> int:
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        threading.Event().wait(0.05)
        with lock:
            running[0] -= 1
        return 1

    for k in ("a", "b", "c"):
        memo.get(k, slow)
    for t in threads:
        t.join(5)
    assert len(threads) == 3
    assert peak[0] == 1


def test_a_warm_does_not_queue_behind_other_keys_refreshes() -> None:
    """The midnight warm must not wait out a stale refresh of yesterday's key
    -- a reader of the new day would wait for both screens."""
    clock = Clock()

    def spawn(fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, daemon=True).start()

    memo: RefreshingMemo[str, int] = RefreshingMemo(
        name="t", fresh_s=10.0, serve_stale_s=100.0, clock=clock, spawn=spawn
    )
    memo.get("yesterday", lambda: 0)
    clock.now = 50.0
    refresh_started = threading.Event()
    release_refresh = threading.Event()

    def long_refresh() -> int:
        refresh_started.set()
        release_refresh.wait(5)
        return 1

    memo.get("yesterday", long_refresh)  # stale: its refresh takes the slot
    assert refresh_started.wait(5)
    warmed = threading.Event()

    def warm_compute() -> int:
        warmed.set()
        return 2

    memo.warm("today", warm_compute)
    try:
        assert warmed.wait(2), "the warm queued behind the stale refresh"
    finally:
        release_refresh.set()
    assert memo.get("today", lambda: 3) == 2


def test_a_misconfigured_lake_does_not_stop_the_app_starting(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from fastapi.testclient import TestClient

    from tail_lab.api import main
    from tail_lab.config import Settings

    def broken() -> object:
        raise ValueError("TAIL_LAB_LAKE_BACKEND=tigris requires the following env vars")

    monkeypatch.setattr(main, "get_settings", lambda: Settings(warm_ranking=True))
    monkeypatch.setattr(main, "putlab_lake_store", broken)
    with caplog.at_level(logging.ERROR, logger="tail_lab.api.main"), TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200
    assert "api.warm_ranking_failed" in caplog.text
