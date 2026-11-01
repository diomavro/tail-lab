"""API tests for the per-name data-coverage grid (``api/coverage_routes.py``).

Counts are derived here from the dates each dataset was seeded with, not read
back from the route, so a route that miscounts (or counts the wrong dataset)
fails.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tail_lab.api import coverage_routes
from tail_lab.api.main import app
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research import dividends
from tail_lab.research.quote_coverage import COVERAGE_SYMBOLS
from tests.test_api_putlab import _seed_ohlcv, _seed_vix
from tests.test_research_dividends import seed_tiingo_eod

DAY = dt.date(2026, 6, 30)
#: SPY's calendar: 320 business days ending DAY, so it spans 2025 and 2026.
SPY_N = 320
#: A name that began later than SPY (AAPL here: 100 sessions), so it is short.
SHORT_N = 100
#: In the universe, but the lake collects no put quotes for it.
UNCOLLECTED = "xlk"


class RecordingStore(DeltaLakeStore):
    """A real store that records every dataset any read method is asked for."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.reads: list[tuple[str, str]] = []

    def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
        self.reads.append(("read_bronze_as_of", dataset))
        return super().read_bronze_as_of(dataset, as_of)

    def read_bronze_columns_as_of(  # type: ignore[override]
        self, dataset: str, as_of: dt.date, columns: list[str]
    ) -> pd.DataFrame:
        self.reads.append(("read_bronze_columns_as_of", dataset))
        return super().read_bronze_columns_as_of(dataset, as_of, columns)

    def bronze_snapshot_id(self, dataset: str, as_of: dt.date) -> str:
        self.reads.append(("bronze_snapshot_id", dataset))
        return super().bronze_snapshot_id(dataset, as_of)

    def bronze_relation_as_of(self, dataset: str, as_of: dt.date, *, history: bool = False) -> str:
        self.reads.append(("bronze_relation_as_of", dataset))
        return super().bronze_relation_as_of(dataset, as_of, history=history)


def _bdays(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=DAY, periods=n)


def _expected(n: int) -> dict[int, int]:
    """Sessions per calendar year of the ``n``-session calendar."""
    return {int(y): int(c) for y, c in _bdays(n).year.value_counts().items()}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RecordingStore]:
    dividends._memo.clear()
    s = RecordingStore(tmp_path)
    _seed_ohlcv(s, "spy", DAY, n=SPY_N)
    _seed_ohlcv(s, "aapl", DAY, n=SHORT_N)
    _seed_ohlcv(s, UNCOLLECTED, DAY, n=SPY_N)
    _seed_vix(s, DAY, n=SPY_N)
    # `n` is the longest history: aapl's rows are a suffix of spy's calendar.
    seed_tiingo_eod(s, ["spy", "aapl", "dia"], DAY, n=SPY_N)
    app.dependency_overrides[coverage_routes.get_lake_store] = lambda: s
    try:
        yield s
    finally:
        app.dependency_overrides.pop(coverage_routes.get_lake_store, None)
        dividends._memo.clear()


@pytest.fixture
def client(store: RecordingStore) -> TestClient:
    return TestClient(app)


def _grid(rows: list[dict[str, int | None]]) -> dict[int, tuple[int, int | None]]:
    return {r["year"]: (r["sessions_present"], r["sessions_expected"]) for r in rows}  # type: ignore[misc]


def _want(own: dict[int, int]) -> dict[int, tuple[int, int | None]]:
    """The grid a dataset with ``own`` sessions per year should get against
    SPY's SPY_N-session calendar. SPY's first year is partial (its history
    starts mid-year), so it has no honest denominator: ``None``, and the
    dataset's own count -- shown only if the dataset has rows that year."""
    spy = _expected(SPY_N)
    first = min(spy)
    grid: dict[int, tuple[int, int | None]] = {
        y: (own.get(y, 0), n) for y, n in spy.items() if y != first
    }
    if own.get(first, 0):
        grid[first] = (own[first], None)
    return grid


def test_ohlcv_is_counted_per_year_against_spys_sessions(client: TestClient) -> None:
    body = client.get("/api/putlab/coverage/aapl", params={"as_of": DAY.isoformat()}).json()
    spy = _expected(SPY_N)
    short = _expected(SHORT_N)
    assert spy  # the calendar exists
    assert _grid(body["ohlcv"]) == _want(short)
    # The short name really is short in a year it overlaps -- the grid is not
    # reporting everything as complete.
    assert any(e is not None and p < e for p, e in _grid(body["ohlcv"]).values())
    assert [r["year"] for r in body["ohlcv"]] == sorted(_want(short))


def test_tiingo_eod_is_counted_from_its_own_dates_not_ohlcvs(client: TestClient) -> None:
    """AAPL is seeded with a short OHLCV history but a full Tiingo one; the
    grid must follow each dataset's own dates."""
    body = client.get("/api/putlab/coverage/aapl", params={"as_of": DAY.isoformat()}).json()
    spy = _expected(SPY_N)
    assert _grid(body["tiingo_eod"]) == _want(spy)
    assert _grid(body["ohlcv"]) != _grid(body["tiingo_eod"])


def test_vix_is_one_shared_row_the_same_for_every_name(client: TestClient) -> None:
    params = {"as_of": DAY.isoformat()}
    spy = _expected(SPY_N)
    a = client.get("/api/putlab/coverage/aapl", params=params).json()
    b = client.get(f"/api/putlab/coverage/{UNCOLLECTED}", params=params).json()
    assert _grid(a["vix"]) == _grid(b["vix"]) == _want(spy)


def test_quotes_collected_is_membership_of_the_coverage_symbols(client: TestClient) -> None:
    assert "aapl" in COVERAGE_SYMBOLS and UNCOLLECTED not in COVERAGE_SYMBOLS
    params = {"as_of": DAY.isoformat()}
    assert client.get("/api/putlab/coverage/AAPL", params=params).json()["quotes_collected"] is True
    body = client.get(f"/api/putlab/coverage/{UNCOLLECTED}", params=params).json()
    assert body["quotes_collected"] is False
    # Still a full grid: a name without collected quotes has price history.
    assert body["ohlcv"] != []


def test_response_shape(client: TestClient) -> None:
    body = client.get("/api/putlab/coverage/spy", params={"as_of": DAY.isoformat()}).json()
    assert set(body) == {"symbol", "as_of", "ohlcv", "tiingo_eod", "vix", "quotes_collected"}
    assert body["symbol"] == "spy"
    assert body["as_of"] == DAY.isoformat()
    assert set(body["ohlcv"][0]) == {"year", "sessions_present", "sessions_expected"}


def test_a_name_with_no_snapshot_is_404(client: TestClient) -> None:
    resp = client.get("/api/putlab/coverage/nope", params={"as_of": DAY.isoformat()})
    assert resp.status_code == 404
    assert "nope" in resp.json()["detail"]


def test_a_name_with_only_one_dataset_has_an_empty_grid_for_the_other(
    client: TestClient,
) -> None:
    """Either dataset is enough for a 200 (Tiingo-only DIA here)."""
    body = client.get("/api/putlab/coverage/dia", params={"as_of": DAY.isoformat()}).json()
    assert body["ohlcv"] == []
    assert _grid(body["tiingo_eod"]) != {}


def test_no_spy_calendar_is_404(tmp_path: Path) -> None:
    s = DeltaLakeStore(tmp_path)
    _seed_ohlcv(s, "aapl", DAY, n=SHORT_N)
    app.dependency_overrides[coverage_routes.get_lake_store] = lambda: s
    try:
        resp = TestClient(app).get("/api/putlab/coverage/aapl", params={"as_of": DAY.isoformat()})
    finally:
        app.dependency_overrides.pop(coverage_routes.get_lake_store, None)
    assert resp.status_code == 404


@pytest.mark.parametrize("bad", ["a/b", "..%2Fx", "x" * 11, "1abc"])
def test_a_symbol_that_cannot_be_a_ticker_is_refused(client: TestClient, bad: str) -> None:
    assert client.get(f"/api/putlab/coverage/{bad}").status_code in (404, 422)
    assert client.get(f"/api/putlab/coverage/{bad}").status_code != 200


def test_it_never_reads_the_chain_or_the_optionsdx_panel(
    client: TestClient, store: RecordingStore
) -> None:
    """The grid is cheap by contract: no quote table is scanned for it. SPY and
    AAPL are both panel names, so a read through the backtests' price loader
    (which splices the panel in) would show here."""
    for name in ("spy", "aapl", UNCOLLECTED):
        assert (
            client.get(
                f"/api/putlab/coverage/{name}", params={"as_of": DAY.isoformat()}
            ).status_code
            == 200
        )
    datasets = {dataset for _method, dataset in store.reads}
    assert datasets  # the recorder saw the reads that did happen
    assert datasets <= {"ohlcv_spy", "ohlcv_aapl", f"ohlcv_{UNCOLLECTED}", "vix", "tiingo_eod"}
    assert not any("option" in d for d in datasets)


def test_tiingo_is_read_once_for_all_symbols(client: TestClient, store: RecordingStore) -> None:
    """The ~400k-row table is memoised on the snapshot: a second name's grid is
    served from the first's read, never a re-read per symbol."""
    dividends._memo.clear()
    for name in ("spy", "aapl"):
        client.get(f"/api/putlab/coverage/{name}", params={"as_of": DAY.isoformat()})
    assert [r for r in store.reads if r[0] == "read_bronze_columns_as_of"] == [
        ("read_bronze_columns_as_of", "tiingo_eod")
    ]


def test_history_before_spys_ohlcv_is_shown_not_cut(tmp_path: Path) -> None:
    """Review finding (2026-11-01): the grid used to list only SPY's OHLCV
    years, so a dataset reaching back further (Tiingo is ingested from 1993,
    VIX decades back) read as starting in 2016. Years with no full SPY year
    must still appear -- with their own count and no denominator."""
    s = DeltaLakeStore(tmp_path)
    _seed_ohlcv(s, "spy", DAY, n=SPY_N)
    _seed_ohlcv(s, "aapl", DAY, n=SPY_N + 600)  # starts ~2.3 years before SPY
    app.dependency_overrides[coverage_routes.get_lake_store] = lambda: s
    try:
        body = (
            TestClient(app)
            .get("/api/putlab/coverage/aapl", params={"as_of": DAY.isoformat()})
            .json()
        )
    finally:
        app.dependency_overrides.pop(coverage_routes.get_lake_store, None)
    grid = _grid(body["ohlcv"])
    spy_first = min(_expected(SPY_N))
    early = [y for y in grid if y < spy_first]
    assert early, "years before SPY's history must be listed"
    assert all(grid[y][1] is None and grid[y][0] > 0 for y in early)
    assert grid[spy_first][1] is None  # SPY's partial first year has no denominator


def test_present_never_exceeds_expected(client: TestClient) -> None:
    """Where a denominator exists, present counts only sessions on SPY's
    calendar: an extra date the reference lacks must not push a year over
    100%."""
    for name in ("spy", "aapl", UNCOLLECTED):
        body = client.get(f"/api/putlab/coverage/{name}", params={"as_of": DAY.isoformat()}).json()
        for row in body["ohlcv"] + body["tiingo_eod"] + body["vix"]:
            if row["sessions_expected"] is not None:
                assert row["sessions_present"] <= row["sessions_expected"]
