"""The OHLCV refresh must cover the whole screening universe, and say so when it doesn't.

Why these matter: from mid-September to 2026-11-04, 46 of the 70 screening
names stopped getting prices because the refresh defaulted to the 24 chain
names. Every run exited green, and the ranking kept scoring those names on
bars ~32 sessions old. These pin both halves of the fix -- the list, and the
check that would have turned that stall red on day six.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.contracts.option_chain import DEFAULT_SNAPSHOT_SYMBOLS
from tail_lab.contracts.options_calendar import universe_symbols
from tail_lab.lake.store import DeltaLakeStore

AS_OF = dt.date(2026, 11, 4)


def _load() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "ohlcv_freshness.py"
    spec = importlib.util.spec_from_file_location("ohlcv_freshness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["ohlcv_freshness"] = module
    spec.loader.exec_module(module)
    return module


fresh = _load()


def _seed(store: DeltaLakeStore, symbol: str, sessions: pd.DatetimeIndex) -> None:
    store.write_bronze(
        dataset_id(symbol),
        AS_OF,
        pd.DataFrame(
            {
                "symbol": symbol.upper(),
                "trade_date": sessions,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
                "adj_close": 100.0,
            }
        ),
    )


SESSIONS = pd.bdate_range("2026-09-01", "2026-11-03")


@pytest.fixture
def lake(tmp_path: Path) -> DeltaLakeStore:
    store = DeltaLakeStore(tmp_path)
    _seed(store, "spy", SESSIONS)
    return store


def test_the_list_is_the_whole_universe_not_the_chain_sweep() -> None:
    """The regression itself: the default was the 24 chain names. A
    screening-only name like XLK must be in it, and so must every chain name
    (a collect-only chain name still needs prices)."""
    names = fresh.refresh_symbols()
    assert "xlk" in names and "xlk" not in DEFAULT_SNAPSHOT_SYMBOLS
    assert set(universe_symbols()) <= set(names)
    assert {s.lower() for s in DEFAULT_SNAPSHOT_SYMBOLS} <= set(names)
    assert list(names) == sorted(set(names))


def test_current_names_pass(lake: DeltaLakeStore) -> None:
    _seed(lake, "xlk", SESSIONS)
    assert (
        fresh.stale_symbols(lake, ["spy", "xlk"], as_of=AS_OF, market_session=SESSIONS[-1].date())
        == []
    )


def test_a_name_frozen_mid_september_is_named(lake: DeltaLakeStore) -> None:
    _seed(lake, "xlk", SESSIONS[SESSIONS <= "2026-09-17"])
    failures = fresh.stale_symbols(lake, ["xlk"], as_of=AS_OF, market_session=None)
    assert len(failures) == 1
    assert failures[0].startswith("xlk: newest bar 2026-09-17")
    assert f"{int((SESSIONS > '2026-09-17').sum())} sessions behind" in failures[0]


def test_the_lag_boundary_is_inclusive(lake: DeltaLakeStore) -> None:
    """Exactly MAX_LAG_SESSIONS behind still passes; one more fails."""
    limit = fresh.MAX_LAG_SESSIONS
    _seed(lake, "ok", SESSIONS[: len(SESSIONS) - limit])
    _seed(lake, "late", SESSIONS[: len(SESSIONS) - limit - 1])
    failures = fresh.stale_symbols(lake, ["ok", "late"], as_of=AS_OF, market_session=None)
    assert [f.split(":")[0] for f in failures] == ["late"]


def test_a_name_with_no_bronze_at_all_fails(lake: DeltaLakeStore) -> None:
    failures = fresh.stale_symbols(lake, ["vrt"], as_of=AS_OF, market_session=None)
    assert failures == ["vrt: no OHLCV in bronze"]


def test_a_frozen_yardstick_is_caught_against_nasdaq(lake: DeltaLakeStore) -> None:
    """If SPY itself froze, every name would compare as fresh against it. The
    market session from Nasdaq is the witness that SPY is current."""
    _seed(lake, "xlk", SESSIONS)
    later = (SESSIONS[-1] + pd.offsets.BDay(2)).date()
    failures = fresh.stale_symbols(lake, ["xlk"], as_of=AS_OF, market_session=later)
    assert len(failures) == 1 and failures[0].startswith("spy: newest bar")


def test_a_missing_yardstick_fails_rather_than_passing_everything(tmp_path: Path) -> None:
    failures = fresh.stale_symbols(
        DeltaLakeStore(tmp_path), ["xlk"], as_of=AS_OF, market_session=None
    )
    assert failures and failures[0].startswith("spy: no OHLCV")


def test_a_same_day_rerun_after_the_close_is_not_a_false_red(lake: DeltaLakeStore) -> None:
    """Bronze is immutable: the evening re-run keeps the morning's SPY
    partition (yesterday's bar) while Nasdaq already reports today. That is
    one business day, not a freeze."""
    _seed(lake, "xlk", SESSIONS)
    today = (SESSIONS[-1] + pd.offsets.BDay(1)).date()
    assert fresh.stale_symbols(lake, ["xlk"], as_of=AS_OF, market_session=today) == []


def _patch_main(
    monkeypatch: pytest.MonkeyPatch, store: DeltaLakeStore, names: tuple[str, ...]
) -> None:
    monkeypatch.setattr(fresh, "get_lake_store", lambda: store)
    monkeypatch.setattr(fresh, "latest_market_session", lambda: SESSIONS[-1].date())
    monkeypatch.setattr(fresh, "refresh_symbols", lambda: names)


def test_main_check_exits_nonzero_and_names_the_laggard(
    lake: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exit code IS the alert: the refresh marks the run failed only on it."""
    _seed(lake, "xlk", SESSIONS[SESSIONS <= "2026-09-17"])
    _patch_main(monkeypatch, lake, ("spy", "xlk"))
    assert fresh.main(["--check"]) == 1
    assert "STALE xlk" in capsys.readouterr().out


def test_main_check_covers_the_whole_universe(
    lake: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A name present in the universe but never ingested must fail the run --
    the check reads refresh_symbols(), not whatever the loop iterated."""
    _patch_main(monkeypatch, lake, ("spy", "vrt"))
    assert fresh.main(["--check"]) == 1
    assert "STALE vrt: no OHLCV in bronze" in capsys.readouterr().out


def test_main_check_passes_a_current_lake(
    lake: DeltaLakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(lake, "xlk", SESSIONS)
    _patch_main(monkeypatch, lake, ("spy", "xlk"))
    assert fresh.main(["--check"]) == 0
