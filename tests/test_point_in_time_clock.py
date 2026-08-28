"""Regression test for the deployment bug: ``ingest_vix``'s default
``ingest_date`` and the API's default ``as_of`` both used local-tz
``date.today()`` while the server runs UTC, so freshly-ingested data was
invisible to a same-day default read whenever local date != UTC date.

Both call sites now derive the default from ``dt.datetime.now(dt.UTC).date()``
(see ``ingestion/vix.py`` and ``api/main.py`` comments). This test pins that
choice directly: it swaps in a fake clock, under a simulated local
timezone that is a full calendar day behind UTC, and proves both defaults
still resolve to the *UTC* date and therefore agree with each other. If
either call site ever regresses back to a local-tz-sensitive default
(``dt.date.today()`` or ``dt.datetime.now()`` with no ``tz``), this test
fails.
"""

from __future__ import annotations

import datetime as dt
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import tail_lab.api.main as api_main
import tail_lab.ingestion.vix as ingestion_vix
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.transforms.vix import STRETCH_WINDOW

# A fixed instant chosen close to UTC midnight: under a simulated local
# timezone UTC-12h, the local calendar date is one day *behind* the UTC
# calendar date -- exactly the shape of the real bug (a local "today" that
# lags the UTC "today" the ingest timestamp was actually stamped with).
_INSTANT_UTC = dt.datetime(2026, 1, 6, 3, 30, tzinfo=dt.UTC)
_SIMULATED_LOCAL_OFFSET = dt.timezone(dt.timedelta(hours=-12))

EXPECTED_UTC_DATE = _INSTANT_UTC.date()
BUGGY_LOCAL_DATE = _INSTANT_UTC.astimezone(_SIMULATED_LOCAL_OFFSET).date()


def _assert_dates_differ_by_construction() -> None:
    # Sanity-check the fixture itself: if this ever failed, the rest of the
    # test would pass for the wrong reason (no actual tz skew simulated).
    assert EXPECTED_UTC_DATE - dt.timedelta(days=1) == BUGGY_LOCAL_DATE


class _FixedClock:
    """Stand-in for ``datetime.datetime`` exposing ``.now(tz)`` (the two
    default-date call sites under test) and ``.fromtimestamp`` (needed
    unchanged by ``parse_yahoo_chart``, which this fixture's payload flows
    through). ``.now`` returns a value derived from the fixed instant above
    -- the UTC value when asked for UTC, and the "local" value (one day
    behind) when asked with no tz, matching what ``dt.date.today()``/naive
    ``dt.datetime.now()`` would see under the simulated local timezone.
    """

    fromtimestamp = staticmethod(dt.datetime.fromtimestamp)

    @classmethod
    def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:
        if tz is None:
            return _INSTANT_UTC.astimezone(_SIMULATED_LOCAL_OFFSET).replace(tzinfo=None)
        return _INSTANT_UTC.astimezone(tz)


@pytest.fixture
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Rebind the ``dt`` name inside the ingestion and api modules to a fake
    namespace whose ``datetime.now`` is the fixed clock above. Only rebinds
    the module-local attribute (each module's own globals dict) -- the real
    stdlib ``datetime`` module used everywhere else is untouched."""
    fake_dt = types.SimpleNamespace(datetime=_FixedClock, UTC=dt.UTC, date=dt.date)
    monkeypatch.setattr(ingestion_vix, "dt", fake_dt)
    monkeypatch.setattr(api_main, "dt", fake_dt)
    yield


def _fixture_raw(n: int, *, start: dt.date) -> dict[str, Any]:
    """A minimal valid Yahoo-chart-shaped payload with ``n`` daily rows
    ending at ``start`` (inclusive), all at UTC midnight so parsing is
    unambiguous regardless of the fake clock above (gmtoffset=0)."""
    dates = [start - dt.timedelta(days=n - 1 - i) for i in range(n)]
    timestamps = [
        int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC).timestamp()) for d in dates
    ]
    closes = [15.0 + i * 0.01 for i in range(n)]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": 0},
                    "timestamp": timestamps,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


def test_ingest_default_and_api_default_both_derive_from_utc_clock(
    fixed_clock: None, tmp_path: Path
) -> None:
    _assert_dates_differ_by_construction()

    store = DeltaLakeStore(tmp_path)

    # 1) ingest_vix with no explicit ingest_date: must commit bronze under
    #    the UTC date, not the (one-day-earlier) simulated local date.
    raw = _fixture_raw(STRETCH_WINDOW + 1, start=EXPECTED_UTC_DATE)
    result = ingestion_vix.ingest_vix(store, raw=raw)
    assert EXPECTED_UTC_DATE.isoformat() in result.bronze_path
    assert BUGGY_LOCAL_DATE.isoformat() not in result.bronze_path

    # 2) The API's default as_of (no ?as_of= query param) must resolve to
    #    the *same* UTC date, so the snapshot just committed under today's
    #    UTC date is immediately visible -- this is the exact bug shape: a
    #    freshly-ingested snapshot invisible to a same-day default read.
    api_main.get_lake_store.cache_clear()
    api_main.app.dependency_overrides[api_main.get_lake_store] = lambda: store
    try:
        with TestClient(api_main.app) as client:
            resp = client.get("/api/vix/stretch")
    finally:
        api_main.app.dependency_overrides.pop(api_main.get_lake_store, None)

    assert resp.status_code == 200, (
        "default as_of did not see the snapshot ingest_vix just committed under "
        "today's UTC date -- the two default-date derivations disagree"
    )
    assert resp.json()["date"] == EXPECTED_UTC_DATE.isoformat()


def test_ingest_default_disagrees_with_naive_local_clock(fixed_clock: None) -> None:
    """Directly pins that ``dt.datetime.now(dt.UTC).date()`` (what production
    code uses) and a naive local-tz ``dt.datetime.now().date()`` (the old,
    buggy shape) are NOT the same value under the simulated skew -- proving
    this fixture actually exercises the risk a regression would reintroduce."""
    utc_default = ingestion_vix.dt.datetime.now(ingestion_vix.dt.UTC).date()
    naive_local_default = ingestion_vix.dt.datetime.now().date()
    assert utc_default == EXPECTED_UTC_DATE
    assert naive_local_default == BUGGY_LOCAL_DATE
    assert utc_default != naive_local_default
