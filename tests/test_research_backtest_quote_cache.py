"""The cache exists because both failure modes were measured, not imagined.

Without it, per-request construction is unservable (13-38 s for SPY against a
5 s health-check timeout); with a naive dict it is a ~300 MB-per-asset leak,
and two SIMULTANEOUS constructions peaked at 1318 MB against a 1024 MB cap.
Every Put Lab route is a sync ``def``, so Starlette runs up to 40 in a
threadpool -- concurrency here is the normal case, not the edge one.
"""

from __future__ import annotations

import threading
import time

import pytest

from tail_lab.research.backtest.quote_cache import DEFAULT_BUDGET_BYTES, QuoteSourceCache


def test_a_second_request_for_the_same_key_reuses_the_first_build() -> None:
    """The cheap half: a hit must not rebuild."""
    cache: QuoteSourceCache[str] = QuoteSourceCache()
    builds: list[str] = []

    def build() -> tuple[str, int]:
        builds.append("spy")
        return "panel", 10

    assert cache.get_or_build("spy", build) == "panel"
    assert cache.get_or_build("spy", build) == "panel"
    assert builds == ["spy"]


def test_concurrent_requests_for_one_key_build_exactly_once() -> None:
    """The property the 1318 MB measurement is about.

    `functools.lru_cache` would NOT give this: it happily runs the same
    expensive call on N threads at once and keeps whichever finishes last. With
    a 300 MB panel and a 1024 MB machine, two concurrent builds is the
    difference between serving and being OOM-killed, so 'build once' has to
    hold under threads or the cache has not solved the problem it exists for.
    """
    cache: QuoteSourceCache[str] = QuoteSourceCache()
    builds: list[int] = []
    lock = threading.Lock()

    def build() -> tuple[str, int]:
        with lock:
            builds.append(1)
        time.sleep(0.15)  # long enough that every thread is inside get_or_build
        return "panel", 10

    threads = [threading.Thread(target=lambda: cache.get_or_build("spy", build)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(builds) == 1, f"built {sum(builds)} times under 8 threads"


def test_builds_for_DIFFERENT_keys_are_bounded_too() -> None:
    """The per-key lock does not bound anything across keys, and that gap WAS
    the original failure.

    Five assets are five keys, so five builds ran in parallel and the peak was
    five panels, not one — measured, with a budget sized for two sources, three
    concurrent requests for three different symbols peaked at 4.7x the budget.
    An earlier version of this file asserted the opposite (that different keys
    never wait on each other) and so actively pinned the unbounded behaviour.
    """
    cache: QuoteSourceCache[str] = QuoteSourceCache(max_concurrent_builds=1)
    live = 0
    peak = 0
    lock = threading.Lock()

    def build() -> tuple[str, int]:
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return "panel", 10

    threads = [
        threading.Thread(target=lambda k=k: cache.get_or_build(k, build))  # type: ignore[misc]
        for k in ("spy", "qqq", "iwm", "gld")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 1, f"{peak} builds ran at once; the budget bounds only one"
    assert sorted(cache.cached_keys()) == ["gld", "iwm", "qqq", "spy"]


def test_a_cache_HIT_never_waits_behind_someone_elses_build() -> None:
    """Bounding builds must not bound reads.

    A build is 13-38 s of S3 I/O. If a hit on an already-resident source had to
    queue behind it, the memory fix would have bought a latency outage — every
    request on the machine serialised behind one cold asset.
    """
    cache: QuoteSourceCache[str] = QuoteSourceCache(max_concurrent_builds=1)
    cache.get_or_build("warm", lambda: ("warm", 10))

    building = threading.Event()
    release = threading.Event()

    def slow_build() -> tuple[str, int]:
        building.set()
        release.wait(timeout=5.0)
        return "cold", 10

    slow = threading.Thread(target=lambda: cache.get_or_build("cold", slow_build))
    slow.start()
    assert building.wait(timeout=5.0)

    started = time.monotonic()
    assert cache.get_or_build("warm", lambda: ("SHOULD NOT REBUILD", 10)) == "warm"
    assert time.monotonic() - started < 0.5, "a hit queued behind an in-flight build"

    release.set()
    slow.join()


def test_eviction_is_by_BYTES_not_by_entry_count() -> None:
    """The specific lesson from `LakeStore._frame_cache`, which caps at 256
    ENTRIES — sized for ~1k-row datasets and therefore no bound at all once a
    single entry can be 300 MB."""
    cache: QuoteSourceCache[str] = QuoteSourceCache(budget_bytes=250)

    for key, nbytes in (("a", 100), ("b", 100), ("c", 100)):
        cache.get_or_build(key, lambda v=key, n=nbytes: (v, n))  # type: ignore[misc]

    # Three entries would fit a count-based cache; 300 bytes does not fit 250.
    assert cache.resident_bytes <= 250
    assert "a" not in cache.cached_keys(), "the least-recently-used entry survived"
    assert cache.cached_keys() == ["b", "c"]


def test_a_hit_refreshes_recency_so_the_wrong_entry_is_not_evicted() -> None:
    cache: QuoteSourceCache[str] = QuoteSourceCache(budget_bytes=250)
    cache.get_or_build("a", lambda: ("a", 100))
    cache.get_or_build("b", lambda: ("b", 100))
    cache.get_or_build("a", lambda: ("a", 100))  # a is now the most recent
    cache.get_or_build("c", lambda: ("c", 100))

    assert "b" not in cache.cached_keys()
    assert sorted(cache.cached_keys()) == ["a", "c"]


def test_a_source_bigger_than_the_whole_budget_is_served_but_not_retained() -> None:
    """Caching it would evict everything and still overshoot. Paying for it
    each time is slower and honest; pretending the budget holds is neither."""
    cache: QuoteSourceCache[str] = QuoteSourceCache(budget_bytes=100)
    assert cache.get_or_build("huge", lambda: ("panel", 500)) == "panel"
    assert cache.cached_keys() == []
    assert cache.resident_bytes == 0


def test_a_source_bigger_than_the_budget_does_not_leak_its_key_lock() -> None:
    """`_entries` staying at 0 is not enough -- `_key_locks` gets a new entry
    per distinct key regardless of whether the build lands in `_entries`, and
    `_drop_oldest` only prunes locks for keys it evicts FROM `_entries`. A key
    that is never retained must still have its lock pruned, or `_key_locks`
    grows without bound on exactly the keys `MAX_ENTRIES` cannot see."""
    cache: QuoteSourceCache[str] = QuoteSourceCache(budget_bytes=100, max_entries=32)

    for i in range(200):
        cache.get_or_build(f"huge{i}", lambda: ("panel", 500))

    assert cache.cached_keys() == []
    assert len(cache._key_locks) == 0


def test_the_default_budget_leaves_room_on_the_real_machine() -> None:
    """400 MB against fly.toml's 1024 MB. Eviction happens AFTER a build, so
    the true peak is the budget plus one source — with the app's own ~252 MB
    baseline, a larger budget would not leave room for that transient."""
    assert DEFAULT_BUDGET_BYTES == 400 * 1024 * 1024
    assert DEFAULT_BUDGET_BYTES * 2 < 1024 * 1024 * 1024


def test_a_nonpositive_budget_is_refused() -> None:
    with pytest.raises(ValueError, match="budget_bytes must be positive"):
        QuoteSourceCache(budget_bytes=0)
