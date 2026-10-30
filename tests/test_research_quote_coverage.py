"""Tests for the Marks grid (``research/quote_coverage.py``).

The grid exists to stop a strategy being believed on quotes the lake does not
hold, so the tests pin what counts as a usable session (the marking bar, not
"any row"), that depth and tenor buckets land where a reader expects, and
that nothing ingested after ``as_of`` is counted.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.quote_coverage import (
    COVERAGE_SYMBOLS,
    STORED_DEPTH_PCT,
    compute_quote_coverage,
    depth_rows,
    regime_by_day,
    summarise_source,
)

D1 = dt.date(2020, 3, 2)  # VIX 40 -> crisis
D2 = dt.date(2020, 3, 3)  # VIX 12 -> calm (a 28-point drop clears every band)
D3 = dt.date(2020, 4, 1)


def _quote(
    day: dt.date, strike: float, dte: int, bid: float, ask: float, volume: int = 0
) -> dict[str, object]:
    return {
        "underlying": "spy",
        "quote_date": pd.Timestamp(day),
        "expiration": pd.Timestamp(day + dt.timedelta(days=dte)),
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "volume": volume,
        "spot": 100.0,
    }


def _store(tmp_path: Path) -> DeltaLakeStore:
    store = DeltaLakeStore(tmp_path)
    store.write_bronze(
        "vix",
        dt.date(2026, 1, 1),
        pd.DataFrame({"date": pd.to_datetime([D1, D2, D3]), "close": [40.0, 12.0, 12.0]}),
    )
    store.write_bronze(
        "optionsdx_quotes_spy",
        dt.date(2026, 1, 1),
        pd.DataFrame(
            [
                # 15% OOM, 20 days: usable (spread 0.2/1.1 = 18%) and traded.
                _quote(D1, 85.0, 20, 1.0, 1.2, volume=5),
                # Same bucket on D2, but no bid: a placeholder, not a mark.
                _quote(D2, 85.0, 20, 0.0, 0.05),
                # 15% OOM, 20 days on D3: a bid, but a 100% spread -> unusable.
                _quote(D3, 85.5, 20, 0.5, 1.5, volume=2),
                # 3% OOM, 5 days on D2: usable, the <=2w band.
                _quote(D2, 97.0, 5, 0.4, 0.42),
                # Excluded rows: in the money, expiring today, past 120 days.
                _quote(D1, 105.0, 20, 5.0, 5.1),
                _quote(D1, 90.0, 0, 0.1, 0.11),
                _quote(D1, 90.0, 130, 2.0, 2.1),
            ]
        ),
    )
    return store


def test_counts_a_session_only_when_a_quote_clears_the_marking_bar(tmp_path: Path) -> None:
    coverage = compute_quote_coverage(_store(tmp_path), symbol="SPY", as_of=dt.date(2026, 1, 2))
    (odx,) = coverage.sources
    assert odx.source == "optionsdx"
    assert odx.months == ["2020-03", "2020-04"]
    assert odx.month_sessions == [2, 1]
    # (month, oom, band, usable, traded): the 14-16% bucket is usable once in
    # March (D1) though quoted twice, and traded-but-unusable in April.
    assert sorted(odx.cells) == [(0, 2, 0, 1, 0), (0, 14, 1, 1, 1), (1, 14, 1, 0, 1)]
    assert odx.stored_depth_pct == STORED_DEPTH_PCT["optionsdx"]


def test_depth_rows_split_usable_sessions_by_the_regime_they_were_in(tmp_path: Path) -> None:
    coverage = compute_quote_coverage(_store(tmp_path), symbol="spy", as_of=dt.date(2026, 1, 2))
    rows = {(r.oom, r.band): r for r in coverage.depth}
    deep = rows[(14, "2-6w")]
    assert (deep.usable_sessions, deep.traded_sessions) == (1, 2)
    assert deep.usable_by_regime == {"calm": 0, "elevated": 0, "crisis": 1}
    assert rows[(2, "<=2w")].usable_by_regime == {"calm": 1, "elevated": 0, "crisis": 0}
    assert coverage.regime_sessions == {"calm": 2, "elevated": 0, "crisis": 1}


def test_nothing_ingested_after_as_of_is_counted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(LookupError):
        compute_quote_coverage(store, symbol="spy", as_of=dt.date(2025, 12, 31))

    chain = pd.DataFrame([{**_quote(D1, 80.0, 30, 1.0, 1.1), "underlying": "SPY"}])
    store.write_bronze("option_chain_snapshot", dt.date(2026, 1, 5), chain)
    later = pd.DataFrame([{**_quote(D3, 80.0, 30, 1.0, 1.1), "underlying": "SPY"}])
    store.write_bronze("option_chain_snapshot", dt.date(2026, 1, 9), later)

    before = compute_quote_coverage(store, symbol="spy", as_of=dt.date(2026, 1, 6))
    chain_cov = next(s for s in before.sources if s.source == "chain")
    # Every partition up to as_of is a session (history), none after it.
    assert (chain_cov.first, chain_cov.last, chain_cov.sessions) == (D1, D1, 1)

    after = compute_quote_coverage(store, symbol="spy", as_of=dt.date(2026, 1, 9))
    chain_cov = next(s for s in after.sources if s.source == "chain")
    assert chain_cov.sessions == 2


def test_rejects_names_without_a_meaningful_depth_axis(tmp_path: Path) -> None:
    # VIX options settle on futures: strike / index is not moneyness.
    assert "vix" not in COVERAGE_SYMBOLS
    with pytest.raises(ValueError):
        compute_quote_coverage(_store(tmp_path), symbol="vix", as_of=dt.date(2026, 1, 2))


def test_a_lake_without_quotes_for_the_name_is_an_absence(tmp_path: Path) -> None:
    with pytest.raises(LookupError):
        compute_quote_coverage(_store(tmp_path), symbol="qqq", as_of=dt.date(2026, 1, 2))


def test_summarise_refuses_an_empty_source() -> None:
    with pytest.raises(ValueError):
        summarise_source(
            "chain", pd.DataFrame(columns=["d", "oom", "band", "usable", "traded"]), []
        )


def test_regime_by_day_carries_the_last_label_and_none_before_the_timeline() -> None:
    timeline = pd.Series(["calm", "crisis"], index=pd.to_datetime([D1, D3]))
    labels = regime_by_day(timeline, {dt.date(2020, 3, 1), D2, D3})
    assert labels == {dt.date(2020, 3, 1): None, D2: "calm", D3: "crisis"}
    assert regime_by_day(pd.Series([], dtype=object), {D1}) == {D1: None}


def test_depth_rows_of_nothing_is_nothing() -> None:
    assert (
        depth_rows(
            pd.DataFrame(columns=["d", "oom", "band", "usable", "traded"]),
            pd.Series([], dtype=object),
        )
        == []
    )


def test_a_strike_exactly_on_a_bucket_edge_lands_in_the_deeper_bucket(tmp_path: Path) -> None:
    # 1 - 90/100 is 0.0999... in floating point; truncating it files a strike
    # exactly 10% out under 8%.
    store = DeltaLakeStore(tmp_path)
    store.write_bronze(
        "vix", dt.date(2026, 1, 1), pd.DataFrame({"date": pd.to_datetime([D1]), "close": [20.0]})
    )
    store.write_bronze(
        "optionsdx_quotes_spy",
        dt.date(2026, 1, 1),
        pd.DataFrame([_quote(D1, 90.0, 30, 1.0, 1.1), _quote(D1, 70.0, 30, 0.1, 0.11)]),
    )
    (odx,) = compute_quote_coverage(store, symbol="spy", as_of=dt.date(2026, 1, 2)).sources
    assert sorted(c[1] for c in odx.cells) == [10, 30]


def _edge_store(tmp_path: Path, rows: list[dict[str, object]]) -> DeltaLakeStore:
    store = DeltaLakeStore(tmp_path)
    store.write_bronze(
        "vix", dt.date(2026, 1, 1), pd.DataFrame({"date": pd.to_datetime([D1]), "close": [20.0]})
    )
    store.write_bronze("optionsdx_quotes_spy", dt.date(2026, 1, 1), pd.DataFrame(rows))
    return store


@pytest.mark.parametrize(
    ("dte", "band"),
    [(1, 0), (14, 0), (15, 1), (42, 1), (43, 2), (120, 2)],
)
def test_tenor_bands_close_on_their_last_day(tmp_path: Path, dte: int, band: int) -> None:
    store = _edge_store(tmp_path, [_quote(D1, 90.0, dte, 1.0, 1.1)])
    (odx,) = compute_quote_coverage(store, symbol="spy", as_of=dt.date(2026, 1, 2)).sources
    assert [c[2] for c in odx.cells] == [band]


@pytest.mark.parametrize("dte", [0, 121])
def test_outside_the_tenor_window_is_not_a_session(tmp_path: Path, dte: int) -> None:
    store = _edge_store(tmp_path, [_quote(D1, 90.0, dte, 1.0, 1.1)])
    with pytest.raises(LookupError):
        compute_quote_coverage(store, symbol="spy", as_of=dt.date(2026, 1, 2))


def test_the_marking_bar_and_the_money_line_are_inclusive(tmp_path: Path) -> None:
    rows = [
        _quote(D1, 100.0, 30, 1.0, 1.1),  # at the money: the 0% bucket, counted
        _quote(D1, 80.0, 30, 0.74, 1.26),  # spread 0.52 / 1.00: just past the bar
        _quote(D1, 70.0, 30, 0.75, 1.25),  # spread 0.50 / 1.00: exactly on it, usable
    ]
    (odx,) = compute_quote_coverage(
        _edge_store(tmp_path, rows), symbol="spy", as_of=dt.date(2026, 1, 2)
    ).sources
    assert sorted(odx.cells) == [(0, 0, 1, 1, 0), (0, 30, 1, 1, 0)]


def test_the_archive_is_read_as_one_snapshot_not_pooled_with_superseded_ones(
    tmp_path: Path,
) -> None:
    # Corrections land as a new partition; pooling would count the old rows too.
    store = _edge_store(tmp_path, [_quote(D1, 90.0, 30, 1.0, 1.1)])
    store.write_bronze(
        "optionsdx_quotes_spy", dt.date(2026, 1, 3), pd.DataFrame([_quote(D3, 70.0, 30, 1.0, 1.1)])
    )
    (odx,) = compute_quote_coverage(store, symbol="spy", as_of=dt.date(2026, 1, 4)).sources
    assert (odx.first, odx.last, odx.sessions) == (D3, D3, 1)


def test_a_row_dated_after_as_of_is_dropped_even_inside_an_eligible_partition(
    tmp_path: Path,
) -> None:
    store = _edge_store(tmp_path, [_quote(D1, 90.0, 30, 1.0, 1.1)])
    future = dt.date(2026, 2, 1)
    store.write_bronze(
        "option_chain_snapshot",
        dt.date(2026, 1, 1),
        pd.DataFrame([{**_quote(future, 90.0, 30, 1.0, 1.1), "underlying": "SPY"}]),
    )
    coverage = compute_quote_coverage(store, symbol="spy", as_of=dt.date(2026, 1, 2))
    assert [s.source for s in coverage.sources] == ["optionsdx"]
