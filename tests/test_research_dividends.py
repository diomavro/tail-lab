"""The research-side dividend reader: one snapshot, memoised, absence as a value.

Why it matters: every pricing entry point asks this module for ``q``. If it
raised on an absent snapshot the whole app would 404 before the first Tiingo
ingest; if it swallowed a store bug the bug would surface as a silent q = 0 --
the exact under-pricing this dataset exists to remove.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.tiingo_eod import DATASET
from tail_lab.lake.store import DeltaLakeStore, LakeStore
from tail_lab.research import dividends
from tail_lab.research.dividends import dividend_lookup, dividend_snapshot_id

DAY = dt.date(2026, 10, 9)


def seed_tiingo_eod(
    store: LakeStore,
    symbols: Sequence[str],
    ingest_date: dt.date,
    *,
    annual_yield: float = 0.06,
    close: float = 100.0,
    n: int = 1300,
) -> None:
    """Quarterly payers at a flat ``close`` whose indicated yield is
    ``annual_yield`` -- shared by every pricing-path test that needs a known
    q > 0. One call per test: bronze is immutable, so a second write on the
    same date is a no-op and would silently drop its symbols."""
    dates = pd.bdate_range(end=ingest_date, periods=n)
    frames = []
    for symbol in symbols:
        frame = pd.DataFrame(
            {
                "symbol": symbol.lower(),
                "trade_date": dates,
                "close": close,
                "adj_close": close,
                "div_cash": 0.0,
                "split_factor": 1.0,
            }
        )
        frame.loc[np.arange(30, n, 63), "div_cash"] = close * annual_yield / 4
        frames.append(frame)
    store.write_bronze(DATASET, ingest_date, pd.concat(frames, ignore_index=True))


def _clear_memo() -> None:
    dividends._memo.clear()


def test_no_snapshot_reads_unknown_at_q_zero(tmp_path: Path) -> None:
    _clear_memo()
    store = DeltaLakeStore(tmp_path)
    found = dividend_lookup(store, "spy", DAY)
    assert found.snapshot_id is None
    assert (found.lookup(DAY).q, found.lookup(DAY).source) == (0.0, "unknown")
    assert dividend_snapshot_id(store, DAY) is None


def test_a_seeded_symbol_reads_its_measured_yield(tmp_path: Path) -> None:
    _clear_memo()
    store = DeltaLakeStore(tmp_path)
    seed_tiingo_eod(store, ["hyg"], DAY, annual_yield=0.06)
    found = dividend_lookup(store, "HYG", DAY)
    got = found.lookup(DAY)
    assert got.source == "measured"
    assert got.q == pytest.approx(-np.log1p(-0.06))
    assert found.snapshot_id is not None and found.snapshot_id.startswith("tiingo_eod@")


def test_a_symbol_missing_from_the_snapshot_is_unknown(tmp_path: Path) -> None:
    _clear_memo()
    store = DeltaLakeStore(tmp_path)
    seed_tiingo_eod(store, ["hyg"], DAY)
    found = dividend_lookup(store, "spy", DAY)
    assert found.lookup(DAY).source == "unknown"
    assert found.snapshot_id is not None  # the snapshot exists; the name does not


def test_a_store_bug_is_raised_not_turned_into_q_zero() -> None:
    _clear_memo()

    class _Broken(DeltaLakeStore):
        def bronze_snapshot_id(self, dataset: str, as_of: dt.date) -> str:
            raise KeyError("dataset 'tiingo_eod' has no column(s) ['div_cash']")

    with pytest.raises(KeyError):
        dividend_lookup(_Broken(Path("/nonexistent")), "spy", DAY)


def test_the_snapshot_is_read_once_and_memoised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_memo()
    store = DeltaLakeStore(tmp_path)
    seed_tiingo_eod(store, ["hyg"], DAY)
    reads = 0
    real = store.read_bronze_columns_as_of

    def counting(*args: object, **kwargs: object) -> pd.DataFrame:
        nonlocal reads
        reads += 1
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "read_bronze_columns_as_of", counting)
    for symbol in ("hyg", "spy", "hyg"):
        dividend_lookup(store, symbol, DAY)
    assert reads == 1


def test_two_lakes_with_matching_statistics_never_share_yields(tmp_path: Path) -> None:
    # The snapshot id is a digest of column statistics: two lakes whose rows
    # differ only inside the min/max envelope share an id. The memo must key on
    # the lake too, or one lake's yields are served for the other.
    _clear_memo()
    a, b = DeltaLakeStore(tmp_path / "a"), DeltaLakeStore(tmp_path / "b")
    seed_tiingo_eod(a, ["hyg"], DAY, annual_yield=0.06)
    seed_tiingo_eod(b, ["hyg"], DAY, annual_yield=0.06)
    frame = b.read_bronze_as_of(DATASET, DAY)
    assert a.bronze_snapshot_id(DATASET, DAY) == b.bronze_snapshot_id(DATASET, DAY)
    # Same stats, different interior: move the last dividend two weeks earlier.
    lake_c = DeltaLakeStore(tmp_path / "c")
    paid = frame.index[frame["div_cash"] > 0][-1]
    moved = frame.copy()
    moved.loc[paid, "div_cash"] = 0.0
    moved.loc[paid - 10, "div_cash"] = frame.loc[paid, "div_cash"]
    lake_c.write_bronze(DATASET, DAY, moved)
    assert a.bronze_snapshot_id(DATASET, DAY) == lake_c.bronze_snapshot_id(DATASET, DAY)
    first = dividend_lookup(a, "hyg", DAY).lookup(DAY)
    other = dividend_lookup(lake_c, "hyg", DAY).lookup(DAY)
    assert first.payments[-1].ex_date != other.payments[-1].ex_date


def test_the_memo_stays_bounded(tmp_path: Path) -> None:
    _clear_memo()
    for i in range(dividends._MEMO_MAX + 3):
        store = DeltaLakeStore(tmp_path / str(i))
        seed_tiingo_eod(store, ["hyg"], DAY)
        dividend_lookup(store, "hyg", DAY)
    assert len(dividends._memo) == dividends._MEMO_MAX


def test_a_new_store_object_for_the_same_lake_hits_the_memo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production builds a fresh DeltaLakeStore per request (config.get_lake_store):
    # the memo must still hit, or every request rebuilds every symbol's yields.
    _clear_memo()
    seed_tiingo_eod(DeltaLakeStore(tmp_path), ["hyg"], DAY)
    reads = 0
    real = DeltaLakeStore.read_bronze_columns_as_of

    def counting(self: DeltaLakeStore, *args: object, **kwargs: object) -> pd.DataFrame:
        nonlocal reads
        reads += 1
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(DeltaLakeStore, "read_bronze_columns_as_of", counting)
    for _ in range(3):
        dividend_lookup(DeltaLakeStore(tmp_path), "hyg", DAY)
    assert reads == 1


def test_eight_threads_on_a_cold_cache_build_the_table_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The ranking calls this from an 8-thread pool on every deploy and after
    # every weekly ingest: unguarded, each thread read and built the table at
    # once (+562 MB peak measured on a 1 GB VM).
    from concurrent.futures import ThreadPoolExecutor

    _clear_memo()
    seed_tiingo_eod(DeltaLakeStore(tmp_path), ["hyg", "spy"], DAY)
    builds = 0
    real = dividends.build_dividend_yields

    def counting(frame: pd.DataFrame) -> object:
        nonlocal builds
        builds += 1
        import time

        time.sleep(0.2)  # widen the race window
        return real(frame)

    monkeypatch.setattr(dividends, "build_dividend_yields", counting)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda s: dividend_lookup(DeltaLakeStore(tmp_path), s, DAY), ["hyg", "spy"] * 8
            )
        )
    assert builds == 1


def test_a_cold_build_for_one_lake_never_blocks_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The build lock is per (lake, snapshot): a slow S3 read for one must not
    # queue a cold request for another behind it.
    import threading

    _clear_memo()
    a, b = tmp_path / "a", tmp_path / "b"
    seed_tiingo_eod(DeltaLakeStore(a), ["hyg"], DAY)
    seed_tiingo_eod(DeltaLakeStore(b), ["hyg"], DAY, annual_yield=0.03)
    gate, entered = threading.Event(), threading.Event()
    real = dividends.build_dividend_yields

    def slow_for_a(frame: pd.DataFrame) -> object:
        if frame["div_cash"].max() > 1.0:  # lake a's 6% payments are 1.50
            entered.set()
            gate.wait(10)
        return real(frame)

    monkeypatch.setattr(dividends, "build_dividend_yields", slow_for_a)
    blocked = threading.Thread(target=lambda: dividend_lookup(DeltaLakeStore(a), "hyg", DAY))
    blocked.start()
    assert entered.wait(5)
    other = threading.Thread(target=lambda: dividend_lookup(DeltaLakeStore(b), "hyg", DAY))
    other.start()
    other.join(timeout=5)
    try:
        assert not other.is_alive(), "lake b waited behind lake a's build"
    finally:
        gate.set()
        blocked.join(timeout=5)


def test_a_re_requested_snapshot_reuses_its_build_lock(tmp_path: Path) -> None:
    # The lock map is never pruned: a lock a thread already holds must stay the
    # one every later request for that key waits on, or the snapshot could be
    # built twice at once after an evict and re-request.
    _clear_memo()
    stores = [DeltaLakeStore(tmp_path / str(i)) for i in range(dividends._MEMO_MAX + 2)]
    for store in stores:
        seed_tiingo_eod(store, ["hyg"], DAY)
    dividend_lookup(stores[0], "hyg", DAY)
    key = next(iter(dividends._memo))
    first = dividends._build_locks[key]
    for store in stores[1:]:
        dividend_lookup(store, "hyg", DAY)  # evicts the first snapshot
    assert key not in dividends._memo
    dividend_lookup(stores[0], "hyg", DAY)  # re-requested
    assert dividends._build_locks[key] is first


def test_the_memo_keeps_the_previous_snapshot_for_an_older_as_of(tmp_path: Path) -> None:
    # Two entries: after a weekly ingest, a request as of last week must still
    # hit the memo, not rebuild a ~60 MB table (dividends._MEMO_MAX).
    _clear_memo()
    store = DeltaLakeStore(tmp_path)
    week1, week2 = dt.date(2026, 10, 3), dt.date(2026, 10, 10)
    seed_tiingo_eod(store, ["spy"], week1)
    seed_tiingo_eod(store, ["spy"], week2)
    dividend_lookup(store, "spy", week1)
    dividend_lookup(store, "spy", week2)
    assert len(dividends._memo) == 2
    builds: list[str] = []
    real = store.read_bronze_columns_as_of

    def counted(*args: object, **kwargs: object) -> pd.DataFrame:
        builds.append("read")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    store.read_bronze_columns_as_of = counted  # type: ignore[method-assign]
    dividend_lookup(store, "spy", week1)
    dividend_lookup(store, "spy", week2)
    assert builds == []


def test_only_spys_rows_keep_adj_close_in_the_memo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the Book's SPY leg reads ``adj_close``. The yields hold views of
    the frame they are built from, so building every symbol's yields on a
    frame that still carries it kept ~70 symbols' copies alive in a memo
    sized for a 1 GB VM (measured +27%)."""
    _clear_memo()
    store = DeltaLakeStore(tmp_path)
    seed_tiingo_eod(store, ["spy", "aapl"], DAY)
    built_on: list[list[str]] = []
    real = dividends.build_dividend_yields

    def spying(frame: pd.DataFrame) -> object:
        built_on.append(list(frame.columns))
        return real(frame)

    monkeypatch.setattr(dividends, "build_dividend_yields", spying)
    history = dividends.index_history(store, DAY)
    assert history is not None
    assert "adj_close" in history.rows.columns
    assert set(history.rows["symbol"]) == {"spy"}
    assert built_on and all("adj_close" not in cols for cols in built_on)
    assert dividend_lookup(store, "aapl", DAY).lookup(DAY).source == "measured"
