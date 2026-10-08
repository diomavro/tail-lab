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
