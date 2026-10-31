"""The one-off HY OAS archive ingest (docs/adr/0033). Every fixture is
synthetic: the repo is public and the real ICE series is licence-limited."""

from __future__ import annotations

import datetime as dt
import hashlib
import inspect
from pathlib import Path

import pandas as pd
import pytest
import requests

from tail_lab.contracts.credit import (
    ARCHIVE_DATASET,
    ARCHIVE_URL,
    ARCHIVE_VINTAGE,
    HY_SERIES_ID,
)
from tail_lab.ingestion.credit_archive import (
    CreditArchiveRefused,
    ingest_credit_archive,
)
from tail_lab.lake.store import DeltaLakeStore

LIVE_DAY = dt.date(2026, 10, 1)
T1, T2 = dt.date(2026, 10, 31), dt.date(2026, 11, 2)
LIVE_DATES = pd.bdate_range("2024-01-01", periods=300)
LIVE_VALUES = [3.0 + 0.01 * (i % 50) for i in range(len(LIVE_DATES))]
OLDER = pd.bdate_range("2023-06-01", periods=20)
OLD_VALUES = [4.0 + 0.01 * i for i in range(len(OLDER))]


def _csv(rows: list[tuple[str, str]]) -> bytes:
    body = "".join(f"{d},{v}\n" for d, v in rows)
    return f"observation_date,{HY_SERIES_ID}\n{body}".encode()


def _archive_rows(
    n_shared: int = 300, *, shift: float = 0.0, blanks: int = 0
) -> list[tuple[str, str]]:
    rows = [(d.strftime("%Y-%m-%d"), f"{v:.4f}") for d, v in zip(OLDER, OLD_VALUES, strict=True)]
    for i in range(blanks):
        rows[i] = (rows[i][0], "")
    start = len(LIVE_DATES) - n_shared
    for i in range(start, len(LIVE_DATES)):
        value = LIVE_VALUES[i] + (shift if i == start else 0.0)
        rows.append((LIVE_DATES[i].strftime("%Y-%m-%d"), f"{value:.4f}"))
    return rows


def _seed_live(
    store: DeltaLakeStore, day: dt.date = LIVE_DAY, values: list[float] = LIVE_VALUES
) -> None:
    frame = pd.DataFrame(
        {
            "series_id": HY_SERIES_ID,
            "obs_date": LIVE_DATES,
            "value": values,
            "vintage_date": LIVE_DATES,
        }
    )
    store.write_bronze("credit", day, frame)


def _store(tmp_path: Path) -> DeltaLakeStore:
    store = DeltaLakeStore(tmp_path)
    store._RESOLVE_TTL_S = -1.0
    return store


class _Get:
    """A recording stand-in for ``requests.get``."""

    def __init__(self, content: bytes, status: int = 200) -> None:
        self.content, self.status, self.urls = content, status, []

    def __call__(self, url: str, **_: object) -> requests.Response:
        self.urls.append(url)
        resp = requests.Response()
        resp.status_code = self.status
        resp._content = self.content
        resp.url = url
        return resp


def _run(store: DeltaLakeStore, content: bytes, **kw: object):
    get = kw.pop("get", None) or _Get(content)
    kw.setdefault("today", T1)
    kw.setdefault("_expected_sha256", hashlib.sha256(content).hexdigest())
    return ingest_credit_archive(store, get=get, **kw)  # type: ignore[arg-type]


def _reason(content: bytes, store: DeltaLakeStore, **kw: object) -> str:
    with pytest.raises(CreditArchiveRefused) as caught:
        _run(store, content, **kw)
    return caught.value.reason


def test_happy_path_writes_one_partition_stamped_with_the_archive_vintage(tmp_path: Path) -> None:
    """WHY (test 15): the archive's vintage is its capture date, not the
    obs date (which would claim each print was known the day it was struck)
    nor the ingest date."""
    store = _store(tmp_path)
    _seed_live(store)
    get = _Get(_csv(_archive_rows()))
    result = _run(store, get.content, get=get)
    assert get.urls == [ARCHIVE_URL]
    assert result.skipped is False
    assert store.bronze_ingest_dates(ARCHIVE_DATASET) == [T1]
    frame = store.read_bronze_as_of(ARCHIVE_DATASET, T1)
    assert len(frame) == len(OLDER) + 300
    assert set(frame["series_id"]) == {HY_SERIES_ID}
    assert set(frame["vintage_date"].dt.date) == {ARCHIVE_VINTAGE}


def test_same_day_partition_is_refused_before_anything_else(tmp_path: Path) -> None:
    """WHY (test 9): bronze is immutable, so a same-day write would be a SILENT
    no-op that reports success; and it is checked before the "already done"
    skip, which would otherwise swallow it. Ignored or not."""
    store = _store(tmp_path)
    _seed_live(store)
    _run(store, _csv(_archive_rows()))

    def boom(url: str, **_: object) -> requests.Response:
        raise AssertionError("must not fetch")

    content = _csv(_archive_rows())
    assert _reason(content, store, get=boom) == "same_day_partition"
    ignored = frozenset({(ARCHIVE_DATASET, T1)})
    assert _reason(content, store, get=boom, ignored=ignored) == "same_day_partition"


def test_hash_mismatch_is_refused(tmp_path: Path) -> None:
    """WHY (test 10): only the pinned bytes are accepted; a changed capture is
    a reviewed code change, never a warning. And the public signature offers
    no way to pass a URL or a hash."""
    store = _store(tmp_path)
    _seed_live(store)
    content = _csv(_archive_rows())
    assert _reason(content, store, _expected_sha256="0" * 64) == "hash_mismatch"
    assert store.bronze_ingest_dates(ARCHIVE_DATASET) == []
    params = set(inspect.signature(ingest_credit_archive).parameters)
    assert params == {"store", "get", "today", "ignored", "_expected_sha256"}


def test_default_hash_is_the_pinned_one(tmp_path: Path) -> None:
    """Without the private seam, synthetic bytes can never match the pin."""
    store = _store(tmp_path)
    _seed_live(store)
    content = _csv(_archive_rows())
    with pytest.raises(CreditArchiveRefused) as caught:
        ingest_credit_archive(store, get=_Get(content), today=T1)
    assert caught.value.reason == "hash_mismatch"


def test_a_429_raises_http_error_before_hashing(tmp_path: Path) -> None:
    """WHY (test 11): Wayback's 429 is an HTML page; hashing it would report a
    misleading hash_mismatch instead of the rate limit."""
    store = _store(tmp_path)
    _seed_live(store)
    get = _Get(b"<html>slow down</html>", status=429)
    with pytest.raises(requests.HTTPError):
        ingest_credit_archive(store, get=get, today=T1)


def test_one_invalid_value_refuses_the_whole_ingest_and_quarantines_nothing(
    tmp_path: Path,
) -> None:
    """WHY (test 12): the archive is a single reviewed artefact; a partial
    history would be silently served as complete."""
    store = _store(tmp_path)
    _seed_live(store)
    rows = _archive_rows()
    rows[2] = (rows[2][0], "60.0")  # above the 50% ceiling
    assert _reason(_csv(rows), store) == "invalid_rows"
    assert store.bronze_ingest_dates(ARCHIVE_DATASET) == []
    assert store.bronze_ingest_dates(f"{ARCHIVE_DATASET}__quarantine") == []


def test_garbage_value_is_invalid_not_blank(tmp_path: Path) -> None:
    """Only "" and FRED's "." are blanks; text in a value cell must not be
    quietly dropped as one."""
    store = _store(tmp_path)
    _seed_live(store)
    rows = _archive_rows()
    rows[3] = (rows[3][0], "n/a")
    assert _reason(_csv(rows), store) == "invalid_rows"


@pytest.mark.parametrize(
    ("shift", "expected"),
    [(0.004, None), (0.006, "overlap_mismatch")],
    ids=["0.004-passes", "0.006-refuses"],
)
def test_overlap_tolerance_is_0_005(tmp_path: Path, shift: float, expected: str | None) -> None:
    """WHY (test 13): the gate is what ties the archive to the live series; a
    looser tolerance lets a different vintage or series through."""
    store = _store(tmp_path)
    _seed_live(store)
    content = _csv(_archive_rows(shift=shift))
    if expected is None:
        assert _run(store, content).skipped is False
    else:
        assert _reason(content, store) == expected


@pytest.mark.parametrize(
    ("n_shared", "expected"), [(249, "thin_overlap"), (250, None)], ids=["249", "250"]
)
def test_overlap_needs_at_least_250_shared_obs(
    tmp_path: Path, n_shared: int, expected: str | None
) -> None:
    """WHY: a handful of coincident obs proves nothing (and a vacuous join
    shares none at all)."""
    store = _store(tmp_path)
    _seed_live(store)
    content = _csv(_archive_rows(n_shared))
    if expected is None:
        assert _run(store, content).skipped is False
    else:
        assert _reason(content, store) == expected


def test_no_live_hy_is_refused(tmp_path: Path) -> None:
    """Nothing to check the archive against: refuse rather than trust it."""
    store = _store(tmp_path)
    assert _reason(_csv(_archive_rows()), store) == "no_live_hy"
    ig_only = pd.DataFrame(
        {
            "series_id": "BAMLC0A0CM",
            "obs_date": LIVE_DATES[:3],
            "value": 1.0,
            "vintage_date": LIVE_DATES[:3],
        }
    )
    store.write_bronze("credit", LIVE_DAY, ig_only)
    assert _reason(_csv(_archive_rows()), store) == "no_live_hy"


def test_gate_honours_the_ignore_list(tmp_path: Path) -> None:
    """WHY: a bad live partition (here a shifted level) must be droppable by a
    reviewed PR; and until it is dropped, the gate must see it."""
    store = _store(tmp_path)
    _seed_live(store, LIVE_DAY)  # good
    _seed_live(store, dt.date(2026, 10, 2), [v + 1.0 for v in LIVE_VALUES])  # bad, newer
    content = _csv(_archive_rows())
    assert _reason(content, store) == "overlap_mismatch"
    ignored = frozenset({("credit", dt.date(2026, 10, 2))})
    assert _run(store, content, ignored=ignored).skipped is False


def test_blank_rows_are_dropped_exactly(tmp_path: Path) -> None:
    """WHY (test 14): a blank is "no print", not a 0.0 spread (which would be a
    fake crash-free reading) and not a NaN (which fails validation)."""
    store = _store(tmp_path)
    _seed_live(store)
    result = _run(store, _csv(_archive_rows(blanks=3)))
    assert result.dropped_blank == 3
    frame = store.read_bronze_as_of(ARCHIVE_DATASET, T1)
    assert len(frame) == len(OLDER) - 3 + 300
    assert frame["value"].notna().all()
    assert (frame["value"] > 0).all()


def test_a_later_run_is_a_noop_until_the_partition_is_ignored(tmp_path: Path) -> None:
    """WHY (test 16): re-running must not stack a second copy (the make target
    is human-run and may be repeated); but once the first partition is ignored
    as defective, a later run must write its replacement."""
    store = _store(tmp_path)
    _seed_live(store)
    content = _csv(_archive_rows())
    _run(store, content)
    again = _run(store, content, today=T2)
    assert again.skipped is True
    assert store.bronze_ingest_dates(ARCHIVE_DATASET) == [T1]
    ignored = frozenset({(ARCHIVE_DATASET, T1)})
    replaced = _run(store, content, today=T2, ignored=ignored)
    assert replaced.skipped is False
    assert store.bronze_ingest_dates(ARCHIVE_DATASET) == [T1, T2]
