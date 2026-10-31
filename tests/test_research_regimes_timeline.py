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
    CreditHistoryError,
    NoCreditHistory,
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
    dataset: str = "credit",
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
    store.write_bronze(dataset, ingest_date, df)


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
    with pytest.raises(NoCreditHistory, match="no credit history"):
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
    with pytest.raises(NoCreditHistory, match=HY_OAS_SERIES_ID):
        load_credit_oas(store, as_of=ingest)


def test_load_credit_oas_respects_no_look_ahead(tmp_path: Path) -> None:
    """Same positive-control shape as the VIX no-look-ahead test above: a
    later snapshot restating credit to crisis levels must not leak into an
    earlier as-of read."""
    store = DeltaLakeStore(tmp_path)
    day1, day2 = dt.date(2026, 3, 2), dt.date(2026, 3, 3)
    _seed_credit(store, day1, [(dt.date(2026, 3, 2), 3.5, dt.date(2026, 3, 2))])
    before = load_credit_oas(store, as_of=day1)
    _seed_credit(store, day2, [(dt.date(2026, 3, 2), 12.0, dt.date(2026, 3, 3))])
    after_but_same_as_of = load_credit_oas(store, as_of=day1)
    assert list(after_but_same_as_of) == list(before) == [3.5]
    assert list(load_credit_oas(store, as_of=day2)) == [12.0]


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
    # The first day has no credit print published before it; the rest do.
    assert list(combined) == ["calm", "elevated", "elevated"]
    # And the reverse never happens: crisis-VIX cannot be talked down by calm credit.
    _seed_vix(store, dt.date(2026, 3, 6), [40.0])
    _seed_credit(store, dt.date(2026, 3, 6), [(dt.date(2026, 3, 6), 3.0, dt.date(2026, 3, 6))])
    still_crisis = compute_regime_timeline(store, as_of=dt.date(2026, 3, 6))
    assert still_crisis.iloc[-1] == "crisis"


def test_compute_regime_timeline_credit_spike_escalates_only_the_next_session(
    tmp_path: Path,
) -> None:
    """Day *t*'s HY OAS is published the morning of *t+1*, and the lake's
    ``vintage_date`` says *t* anyway. A crisis-level print on Friday must
    leave Friday as VIX alone labels it and escalate Monday, the first close
    at which anyone had the number."""
    store = DeltaLakeStore(tmp_path)
    thu, fri, mon = dt.date(2026, 3, 5), dt.date(2026, 3, 6), dt.date(2026, 3, 9)
    vix_dates = _seed_vix(store, mon, [12.0, 12.0, 12.0])
    assert list(vix_dates.date) == [thu, fri, mon]
    _seed_credit(store, mon, [(thu, 3.0, thu), (fri, 9.0, fri)])  # 9.0 is credit-"crisis"
    combined = compute_regime_timeline(store, as_of=mon)
    assert list(combined) == ["calm", "calm", "crisis"]


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


# ---- the union read, the archive/live split and the seam (docs/adr/0033) ----

END = dt.date(2000, 1, 4)  # a synthetic archive_end
A1, A2 = dt.date(2026, 10, 1), dt.date(2026, 10, 2)
AS_OF = dt.date(2026, 10, 5)


def _day(n: int) -> dt.date:
    return dt.date(2000, 1, 3) + dt.timedelta(days=n)


def _rows(*pairs: tuple[dt.date, float]) -> list[tuple[dt.date, float, dt.date]]:
    return [(d, v, d) for d, v in pairs]


def _fresh(tmp_path: Path) -> DeltaLakeStore:
    store = DeltaLakeStore(tmp_path)
    store._RESOLVE_TTL_S = -1.0
    return store


def test_history_survives_a_bad_newest_partition(tmp_path: Path) -> None:
    """WHY (17): FRED's window slides, so one partition holds only ~3 years. A
    newest partition that is IG-only (or short) must not erase the older
    partitions' obs -- reading only the newest partition was the old bug that
    left the credit half of the regime blind to 2008, 2011, 2016 and 2020."""
    store = _fresh(tmp_path)
    _seed_credit(store, A1, _rows((_day(0), 9.0), (_day(1), 9.0)))
    # The newest HY partition is short (one obs), and a later one is IG-only.
    _seed_credit(store, A2, _rows((_day(1), 9.0)))
    _seed_credit(store, AS_OF, [(_day(1), 1.2, _day(1))], series_id="BAMLC0A0CM")
    oas = load_credit_oas(store, as_of=AS_OF)
    assert list(oas.index.date) == [_day(0), _day(1)]
    assert list(oas) == [9.0, 9.0]
    vix_dates = pd.bdate_range(_day(0), periods=4)
    store.write_bronze("vix", AS_OF, pd.DataFrame({"date": vix_dates, "close": [12.0] * 4}))
    assert list(compute_regime_timeline(store, as_of=AS_OF))[1:] == ["crisis"] * 3


def _seed_split(store: DeltaLakeStore) -> None:
    """Archive a1 good, a2 newer and bad; live l1 good, l2 newer and bad."""
    _seed_credit(store, A1, _rows((_day(0), 3.0), (END, 3.1)), dataset="credit_archive")
    _seed_credit(store, A2, _rows((_day(0), 7.7), (END, 7.7)), dataset="credit_archive")
    _seed_credit(store, A1, _rows((_day(2), 4.0)))
    _seed_credit(store, A2, _rows((_day(2), 9.0)))


def test_the_archive_ignore_list_reaches_the_loader(tmp_path: Path) -> None:
    """WHY (18): a bad archive partition must be droppable by a reviewed PR;
    an ignore list applied only to ``credit`` would leave it serving."""
    store = _fresh(tmp_path)
    _seed_split(store)
    served = load_credit_oas(store, as_of=AS_OF)
    assert list(served.iloc[:2]) == [7.7, 7.7]  # positive control: newest archive wins
    ignored = frozenset({("credit_archive", A2)})
    assert list(load_credit_oas(store, as_of=AS_OF, ignored=ignored).iloc[:2]) == [3.0, 3.1]


def test_the_live_ignore_list_reaches_the_loader(tmp_path: Path) -> None:
    """WHY (19): same, for a bad live partition that is the newest holder of
    an obs after ``archive_end``."""
    store = _fresh(tmp_path)
    _seed_split(store)
    assert load_credit_oas(store, as_of=AS_OF).iloc[-1] == 9.0
    ignored = frozenset({("credit", A2)})
    assert load_credit_oas(store, as_of=AS_OF, ignored=ignored).iloc[-1] == 4.0


def test_a_store_bug_propagates_through_compute_regime_timeline(tmp_path: Path) -> None:
    """WHY (20): KeyError IS a LookupError, so an ``except LookupError`` at the
    loader or the timeline would turn a broken store into silent VIX-only
    labels (precedent: test_api_putlab's hedge-overlay store-bug test)."""
    store = _fresh(tmp_path)
    _seed_vix(store, AS_OF, [12.0, 12.0])

    def broken(*args: object, **kwargs: object) -> pd.DataFrame:
        raise KeyError("store bug")

    store.read_bronze_history_as_of = broken  # type: ignore[method-assign]
    with pytest.raises(KeyError, match="store bug"):
        compute_regime_timeline(store, as_of=AS_OF)


def test_the_archive_respects_as_of(tmp_path: Path) -> None:
    """WHY (21): an as-of before the archive was ingested must see no archive
    (point-in-time, ADR 0009), so live serves every obs it holds."""
    store = _fresh(tmp_path)
    _seed_credit(store, A1, _rows((_day(0), 4.5), (END, 4.5), (_day(2), 4.6)))
    _seed_credit(store, A2, _rows((_day(0), 3.0), (END, 3.1)), dataset="credit_archive")
    before = load_credit_oas(store, as_of=A1)
    assert list(before) == [4.5, 4.5, 4.6]
    after = load_credit_oas(store, as_of=A2)
    assert list(after) == [3.0, 3.1, 4.6]


@pytest.mark.parametrize(("gap_days", "ok"), [(7, True), (8, False)])
def test_seam_boundary_is_seven_days(tmp_path: Path, gap_days: int, ok: bool) -> None:
    """WHY (22a): 7 days sits above the largest real gap (4) and below a missing
    window; both edges are pinned so the bound cannot drift silently."""
    store = _fresh(tmp_path)
    _seed_credit(store, A1, _rows((_day(0), 3.0), (END, 3.1)), dataset="credit_archive")
    _seed_credit(store, A1, _rows((END + dt.timedelta(days=gap_days), 4.0)))
    if ok:
        assert load_credit_oas(store, as_of=AS_OF).iloc[-1] == 4.0
    else:
        with pytest.raises(CreditHistoryError):
            load_credit_oas(store, as_of=AS_OF)


def test_an_archive_with_no_live_hy_is_a_seam_breach(tmp_path: Path) -> None:
    store = _fresh(tmp_path)
    _seed_credit(store, A1, _rows((_day(0), 3.0), (END, 3.1)), dataset="credit_archive")
    with pytest.raises(CreditHistoryError):
        load_credit_oas(store, as_of=AS_OF)
    _seed_credit(store, A1, [(END, 1.0, END)], series_id="BAMLC0A0CM")  # IG only
    with pytest.raises(CreditHistoryError):
        load_credit_oas(store, as_of=AS_OF)


def test_credit_history_error_is_not_a_lookup_error() -> None:
    """A LookupError becomes a 404 or a silent VIX-only fallback; a seam breach
    is a data fault and must surface as a 500."""
    assert not issubclass(CreditHistoryError, LookupError)
    assert issubclass(NoCreditHistory, LookupError)


def test_the_seam_survives_the_window_sliding_past_it(tmp_path: Path) -> None:
    """WHY (23): FRED's window will pass ``archive_end``, but the older
    partitions still hold the obs right after it. Judging the seam from the
    newest partition alone would raise on a healthy lake."""
    store = _fresh(tmp_path)
    _seed_credit(store, A1, _rows((_day(0), 3.0), (END, 3.1)), dataset="credit_archive")
    _seed_credit(
        store, A1, _rows((END + dt.timedelta(days=1), 4.0), (END + dt.timedelta(days=2), 4.1))
    )
    far = END + dt.timedelta(days=400)
    _seed_credit(store, A2, _rows((far, 5.0), (far + dt.timedelta(days=1), 5.1)))
    oas = load_credit_oas(store, as_of=AS_OF)
    assert oas.loc[pd.Timestamp(END + dt.timedelta(days=1))] == 4.0


def _vix_on(store: DeltaLakeStore, dates: pd.DatetimeIndex, as_of: dt.date) -> None:
    store.write_bronze("vix", as_of, pd.DataFrame({"date": dates, "close": [12.0] * len(dates)}))


def test_a_stale_tail_or_source_hole_falls_back_to_vix(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """WHY (24): a print counts for 10 business days from its PUBLICATION date
    (obs + 1 day). Beyond that credit has no opinion and VIX decides, with one
    warning per call. Unlimited forward-fill would carry a dead feed forever."""
    store = _fresh(tmp_path)
    fri, wed = dt.date(2025, 1, 3), dt.date(2025, 1, 22)
    vix_dates = pd.bdate_range("2025-01-02", "2025-01-24")
    _vix_on(store, vix_dates, dt.date(2025, 2, 1))
    _seed_credit(store, dt.date(2025, 2, 1), _rows((fri, 9.0), (wed, 9.0)))
    with caplog.at_level("WARNING", logger="tail_lab.research.regimes.timeline"):
        out = compute_regime_timeline(store, as_of=dt.date(2025, 2, 1))
    by_day = {d.date(): label for d, label in out.items()}
    assert by_day[dt.date(2025, 1, 2)] == "calm"  # before the first print: no opinion
    assert by_day[dt.date(2025, 1, 3)] == "calm"  # print published Sat 01-04
    assert by_day[dt.date(2025, 1, 6)] == "crisis"
    assert by_day[dt.date(2025, 1, 20)] == "crisis"  # busday_count = 10: still counts
    assert by_day[dt.date(2025, 1, 21)] == "calm"  # 11: VIX only
    assert by_day[dt.date(2025, 1, 22)] == "calm"  # 12: VIX only
    assert by_day[dt.date(2025, 1, 23)] == "crisis"  # the new print is out
    warnings = [r.getMessage() for r in caplog.records if "regime.credit_stale" in r.getMessage()]
    assert len(warnings) == 1
    assert "dates=2" in warnings[0]


def test_dates_before_the_first_print_log_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A VIX date that never had an opinion is not "stale"; warning on it
    would cry wolf on every call for the start of the history."""
    store = _fresh(tmp_path)
    _vix_on(store, pd.bdate_range("2025-01-02", "2025-01-03"), dt.date(2025, 2, 1))
    _seed_credit(store, dt.date(2025, 2, 1), _rows((dt.date(2025, 1, 3), 9.0)))
    with caplog.at_level("WARNING", logger="tail_lab.research.regimes.timeline"):
        out = compute_regime_timeline(store, as_of=dt.date(2025, 2, 1))
    assert list(out) == ["calm", "calm"]
    assert not [r for r in caplog.records if "credit_stale" in r.getMessage()]


def test_no_credit_and_no_archive_is_vix_only(tmp_path: Path) -> None:
    """WHY (25): the one graceful fallback goes through NoCreditHistory, not
    through a seam error."""
    store = _fresh(tmp_path)
    vix = _seed_vix(store, AS_OF, [12.0, 30.0, 18.0])
    expected = label_vix_series(pd.Series([12.0, 30.0, 18.0], index=vix))
    assert list(compute_regime_timeline(store, as_of=AS_OF)) == list(expected)


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
