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


def test_different_keys_do_not_block_each_other() -> None:
    """The per-key lock must not become a global one.

    Holding a single lock across a 13-38 s S3 read would serialise every
    unrelated request on the machine — turning a memory fix into a latency
    outage.
    """
    cache: QuoteSourceCache[str] = QuoteSourceCache()
    started = threading.Barrier(2, timeout=5.0)

    def build() -> tuple[str, int]:
        started.wait()  # deadlocks (BrokenBarrier) if the two builds serialise
        return "panel", 10

    threads = [
        threading.Thread(target=lambda k=k: cache.get_or_build(k, build))  # type: ignore[misc]
        for k in ("spy", "qqq")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(cache.cached_keys()) == ["qqq", "spy"]


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


def test_the_default_budget_leaves_room_on_the_real_machine() -> None:
    """400 MB against fly.toml's 1024 MB. Eviction happens AFTER a build, so
    the true peak is the budget plus one source — with the app's own ~252 MB
    baseline, a larger budget would not leave room for that transient."""
    assert DEFAULT_BUDGET_BYTES == 400 * 1024 * 1024
    assert DEFAULT_BUDGET_BYTES * 2 < 1024 * 1024 * 1024


def test_a_nonpositive_budget_is_refused() -> None:
    with pytest.raises(ValueError, match="budget_bytes must be positive"):
        QuoteSourceCache(budget_bytes=0)
