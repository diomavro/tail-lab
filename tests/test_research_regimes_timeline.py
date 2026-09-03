"""Tests for the market-regime classifier + timeline
(``contracts/regime.py``, ``research/regimes/timeline.py``)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.contracts.regime import (
    CALM_MAX,
    CREDIT_CALM_MAX,
    CREDIT_ELEVATED_MAX,
    ELEVATED_MAX,
    REGIME_LABELS,
    classify_credit_level,
    classify_vix_level,
)
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.regimes.timeline import (
    HY_OAS_SERIES_ID,
    compute_regime_timeline,
    compute_regime_view,
    label_credit_series,
    label_vix_series,
    load_credit_oas,
    regime_on_or_before,
    regime_segments,
)


def test_regime_labels_are_ordered_by_stress() -> None:
    assert REGIME_LABELS == ("calm", "elevated", "crisis")


def test_classify_vix_level_thresholds_are_half_open() -> None:
    # Boundaries are [lower, upper): the threshold value belongs to the harsher
    # bucket, so exactly-CALM_MAX is already 'elevated', not still 'calm'.
    assert classify_vix_level(CALM_MAX - 0.01) == "calm"
    assert classify_vix_level(CALM_MAX) == "elevated"
    assert classify_vix_level(ELEVATED_MAX - 0.01) == "elevated"
    assert classify_vix_level(ELEVATED_MAX) == "crisis"
    assert classify_vix_level(9.0) == "calm"
    assert classify_vix_level(60.0) == "crisis"


def test_label_vix_series_preserves_index() -> None:
    idx = pd.date_range("2022-01-03", periods=3, freq="B")
    close = pd.Series([12.0, 22.0, 40.0], index=idx)
    labels = label_vix_series(close)
    assert list(labels) == ["calm", "elevated", "crisis"]
    assert labels.index.equals(idx)


def test_classify_credit_level_thresholds_are_half_open() -> None:
    assert classify_credit_level(CREDIT_CALM_MAX - 0.01) == "calm"
    assert classify_credit_level(CREDIT_CALM_MAX) == "elevated"
    assert classify_credit_level(CREDIT_ELEVATED_MAX - 0.01) == "elevated"
    assert classify_credit_level(CREDIT_ELEVATED_MAX) == "crisis"


def test_label_credit_series_preserves_index() -> None:
    idx = pd.date_range("2022-01-03", periods=3, freq="B")
    oas = pd.Series([3.5, 6.0, 9.0], index=idx)
    labels = label_credit_series(oas)
    assert list(labels) == ["calm", "elevated", "crisis"]
    assert labels.index.equals(idx)


def _seed_vix(store: DeltaLakeStore, ingest_date: dt.date, closes: list[float]) -> pd.DatetimeIndex:
    dates = pd.date_range(end=ingest_date, periods=len(closes), freq="B")
    store.write_bronze("vix", ingest_date, pd.DataFrame({"date": dates, "close": closes}))
    return dates


def _seed_credit(
    store: DeltaLakeStore,
    ingest_date: dt.date,
    rows: list[tuple[dt.date, float, dt.date] | tuple[dt.date, float, dt.date, str]],
    *,
    series_id: str = HY_OAS_SERIES_ID,
) -> None:
    """Write a ``credit`` bronze partition from ``(obs_date, value, vintage_date)``
    triples (optionally a 4th element overriding ``series_id`` per row) -- the
    same shape :func:`tail_lab.ingestion.credit.parse_fred_observations`
    produces, so a caller can inject more than one vintage per ``obs_date``."""
    df = pd.DataFrame(
        {
            "series_id": [r[3] if len(r) > 3 else series_id for r in rows],
            "obs_date": [r[0] for r in rows],
            "value": [r[1] for r in rows],
            "vintage_date": [r[2] for r in rows],
        }
    )
    store.write_bronze("credit", ingest_date, df)


def test_compute_regime_timeline_labels_every_date(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest, [12.0, 18.0, 30.0, 15.0])
    timeline = compute_regime_timeline(store, as_of=ingest)
    assert list(timeline) == ["calm", "elevated", "crisis", "calm"]


def test_compute_regime_timeline_respects_no_look_ahead(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    day1, day2 = dt.date(2026, 3, 2), dt.date(2026, 3, 3)
    _seed_vix(store, day1, [12.0, 13.0, 14.0])
    t1 = compute_regime_timeline(store, as_of=day1)
    # A later snapshot that restates everything to crisis must not leak back.
    _seed_vix(store, day2, [40.0, 41.0, 42.0])
    t1_again = compute_regime_timeline(store, as_of=day1)
    assert list(t1_again) == list(t1) == ["calm", "calm", "calm"]
    assert list(compute_regime_timeline(store, as_of=day2)) == ["crisis", "crisis", "crisis"]


def test_compute_regime_timeline_missing_vix_raises(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(LookupError, match="no VIX"):
        compute_regime_timeline(store, as_of=dt.date(2026, 3, 2))


# ---- the credit-spread widening (`docs/END_STATE.md` §1.3) -----------------


def test_load_credit_oas_resolves_the_latest_vintage_per_obs_date(tmp_path: Path) -> None:
    """The load-bearing case the whole vintage column exists for: two prints
    of the same obs_date, and the read must return the later one, not the
    first-seen or the smallest."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 5)
    _seed_credit(
        store,
        ingest,
        [
            (dt.date(2026, 3, 2), 3.51, dt.date(2026, 3, 2)),
            (dt.date(2026, 3, 2), 3.48, dt.date(2026, 3, 4)),  # a same-day revision
            (dt.date(2026, 3, 3), 3.60, dt.date(2026, 3, 3)),
        ],
    )
    oas = load_credit_oas(store, as_of=ingest)
    assert list(oas.index.date) == [dt.date(2026, 3, 2), dt.date(2026, 3, 3)]
    assert oas.iloc[0] == pytest.approx(3.48)  # the later vintage, not 3.51
    assert oas.iloc[1] == pytest.approx(3.60)


def test_load_credit_oas_ignores_other_series_in_the_shared_dataset(tmp_path: Path) -> None:
    """The bronze ``credit`` dataset carries HY and IG OAS side by side
    (`docs/DATA_CONTRACTS.md` #4) -- the read must select HY only."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_credit(
        store,
        ingest,
        [
            (dt.date(2026, 3, 2), 3.51, dt.date(2026, 3, 2)),
            (dt.date(2026, 3, 2), 1.20, dt.date(2026, 3, 2), "BAMLC0A0CM"),  # IG OAS, same date
        ],
    )
    oas = load_credit_oas(store, as_of=ingest)
    assert list(oas) == [3.51]


def test_load_credit_oas_missing_dataset_raises(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(LookupError, match="no credit snapshot"):
        load_credit_oas(store, as_of=dt.date(2026, 3, 2))


def test_load_credit_oas_dataset_without_hy_oas_raises(tmp_path: Path) -> None:
    """A ``credit`` partition can exist with only IG OAS in it (a partial
    ingest, or a future series added to the family) -- the read must fail
    loudly rather than silently return nothing."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_credit(
        store, ingest, [(dt.date(2026, 3, 2), 1.20, dt.date(2026, 3, 2))], series_id="BAMLC0A0CM"
    )
    with pytest.raises(LookupError, match=HY_OAS_SERIES_ID):
        load_credit_oas(store, as_of=ingest)


def test_load_credit_oas_respects_no_look_ahead(tmp_path: Path) -> None:
    """Same positive-control shape as the VIX no-look-ahead test above: a
    later snapshot restating credit to crisis levels must not leak into an
    earlier as-of read."""
    store = DeltaLakeStore(tmp_path)
    day1, day2 = dt.date(2026, 3, 2), dt.date(2026, 3, 3)
    _seed_credit(store, day1, [(dt.date(2026, 3, 2), 3.5, dt.date(2026, 3, 2))])
    before = load_credit_oas(store, as_of=day1)
    _seed_credit(store, day2, [(dt.date(2026, 3, 2), 19.9, dt.date(2026, 3, 3))])
    after_but_same_as_of = load_credit_oas(store, as_of=day1)
    assert list(after_but_same_as_of) == list(before) == [3.5]
    assert list(load_credit_oas(store, as_of=day2)) == [19.9]


def test_compute_regime_timeline_falls_back_to_vix_only_without_credit(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 5)
    _seed_vix(store, ingest, [12.0, 12.0])
    assert list(compute_regime_timeline(store, as_of=ingest)) == ["calm", "calm"]


def test_compute_regime_timeline_credit_stress_escalates_a_calm_vix_day(tmp_path: Path) -> None:
    """Proves the widening does real work: a VIX-calm day becomes elevated
    once HY OAS alone is stressed, not just cosmetically re-labelled."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 5)
    vix_dates = _seed_vix(store, ingest, [12.0, 12.0, 12.0])
    _seed_credit(
        store,
        ingest,
        [(d.date(), 6.0, d.date()) for d in vix_dates],  # 6.0 is credit-"elevated"
    )
    combined = compute_regime_timeline(store, as_of=ingest)
    assert set(combined) == {"elevated"}
    # And the reverse never happens: crisis-VIX cannot be talked down by calm credit.
    _seed_vix(store, dt.date(2026, 3, 6), [40.0])
    _seed_credit(store, dt.date(2026, 3, 6), [(dt.date(2026, 3, 6), 3.0, dt.date(2026, 3, 6))])
    still_crisis = compute_regime_timeline(store, as_of=dt.date(2026, 3, 6))
    assert still_crisis.iloc[-1] == "crisis"


def test_compute_regime_timeline_credit_before_its_own_history_defaults_calm(
    tmp_path: Path,
) -> None:
    """A VIX date earlier than the first HY OAS print has no credit opinion
    yet -- combination must still be total, defaulting to the least severe
    label rather than raising or dropping the date."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 5)
    vix_dates = _seed_vix(store, ingest, [12.0, 12.0, 12.0])
    _seed_credit(store, ingest, [(vix_dates[-1].date(), 3.0, vix_dates[-1].date())])
    combined = compute_regime_timeline(store, as_of=ingest)
    assert list(combined) == ["calm", "calm", "calm"]


def test_regime_on_or_before_uses_last_trading_day() -> None:
    idx = pd.to_datetime(["2022-03-04", "2022-03-07", "2022-03-08"])  # Fri, Mon, Tue
    timeline = pd.Series(["calm", "elevated", "crisis"], index=idx)
    # A Saturday resolves to Friday's regime (last trading day on or before).
    assert regime_on_or_before(timeline, dt.date(2022, 3, 5)) == "calm"
    assert regime_on_or_before(timeline, dt.date(2022, 3, 8)) == "crisis"
    with pytest.raises(LookupError, match="predates"):
        regime_on_or_before(timeline, dt.date(2022, 1, 1))


def test_regime_segments_run_length_encodes() -> None:
    idx = pd.date_range("2026-01-01", periods=5, freq="B")
    timeline = pd.Series(["calm", "calm", "crisis", "calm", "calm"], index=idx)
    segs = regime_segments(timeline)
    assert [(s.regime, s.n_days) for s in segs] == [("calm", 2), ("crisis", 1), ("calm", 2)]
    assert segs[0].start == idx[0].date()
    assert segs[-1].end == idx[-1].date()


def test_compute_regime_view_end_to_end(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed_vix(store, ingest, [12.0, 12.0, 30.0, 20.0])  # calm calm crisis elevated
    view = compute_regime_view(store, as_of=ingest)
    assert view.current == "elevated"  # last bar
    assert view.day_counts == {"calm": 2, "crisis": 1, "elevated": 1}
    assert [s.regime for s in view.segments] == ["calm", "crisis", "elevated"]
