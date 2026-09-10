"""Integration test for :class:`DeltaLakeStore` against the real
``tail-lab-lake`` bucket on Fly Tigris.

Skipped unless ``TAIL_LAB_LAKE_BACKEND=tigris`` and full Tigris credentials
are configured (env vars or ``.env``) — this never runs in CI (no creds
there), only locally / by a human who has the bucket credentials. It writes
under a uniquely-named test dataset and deletes everything it created in a
``finally`` block, so repeated runs never accumulate cruft in the real
bucket.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from collections.abc import Iterator

import pandas as pd
import pyarrow.fs as pafs
import pytest

from tail_lab.config import Settings, get_lake_store, get_settings
from tail_lab.lake.store import DeltaLakeStore, _strip_scheme

_SETTINGS: Settings = get_settings()


def _tigris_configured(settings: Settings) -> bool:
    return (
        settings.lake_backend == "tigris"
        and settings.s3_bucket is not None
        and settings.s3_endpoint_url is not None
        and settings.s3_region is not None
        and settings.aws_access_key_id is not None
        and settings.aws_secret_access_key is not None
    )


pytestmark = pytest.mark.skipif(
    not _tigris_configured(_SETTINGS),
    reason="requires TAIL_LAB_LAKE_BACKEND=tigris and Tigris credentials (env or .env)",
)


@pytest.fixture
def tigris_store() -> Iterator[tuple[DeltaLakeStore, str]]:
    # mypy: only constructed when _tigris_configured() is True, so these are
    # all non-None; assert narrows for the type checker.
    settings = _SETTINGS
    assert settings.s3_bucket is not None
    assert settings.s3_endpoint_url is not None
    assert settings.s3_region is not None
    assert settings.aws_access_key_id is not None
    assert settings.aws_secret_access_key is not None

    dataset = f"_integration_test_{uuid.uuid4().hex[:12]}"
    store = get_lake_store(settings)
    assert isinstance(store, DeltaLakeStore)  # the "tigris" backend, per config
    cleanup_fs = pafs.S3FileSystem(
        access_key=settings.aws_access_key_id,
        secret_key=settings.aws_secret_access_key,
        region=settings.s3_region,
        endpoint_override=_strip_scheme(settings.s3_endpoint_url),
        scheme="https",
    )
    try:
        yield store, dataset
    finally:
        # delete_dir removes the prefix's files AND any virtual directory
        # markers under it in one call — plain per-file deletion leaves
        # empty directory markers behind on Tigris.
        for layer in ("bronze", "silver", "gold"):
            prefix = f"{settings.s3_bucket}/{layer}/{dataset}"
            with contextlib.suppress(FileNotFoundError):
                cleanup_fs.delete_dir(prefix)


def test_tigris_round_trip_write_read_and_as_of(
    tigris_store: tuple[DeltaLakeStore, str],
) -> None:
    store, dataset = tigris_store

    day1 = dt.date(2026, 1, 5)
    day2 = dt.date(2026, 1, 6)
    snapshot_day1 = pd.DataFrame({"date": pd.to_datetime(["2026-01-05"]), "close": [16.0]})
    snapshot_day2 = pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-05", "2026-01-06"]), "close": [16.0, 17.0]}
    )

    location1 = store.write_bronze(dataset, day1, snapshot_day1)
    assert location1.startswith("s3://")

    store.write_bronze(dataset, day2, snapshot_day2)

    # As-of day1 must not see day2's snapshot (no-look-ahead), even though
    # it now exists in the same real bucket, as a later partition of the
    # same Delta table.
    as_of_day1 = store.read_bronze_as_of(dataset, day1)
    assert len(as_of_day1) == 1
    assert as_of_day1["close"].iloc[0] == 16.0

    as_of_day2 = store.read_bronze_as_of(dataset, day2)
    assert len(as_of_day2) == 2

    # Immutability: re-ingesting day1 with different data must be a no-op —
    # no new Delta commit, same partition location, original data intact.
    different = pd.DataFrame({"date": pd.to_datetime(["2026-01-05"]), "close": [999.0]})
    location1_again = store.write_bronze(dataset, day1, different)
    assert location1_again == location1
    assert store.read_bronze_as_of(dataset, day1)["close"].iloc[0] == 16.0

    # Snapshot id is stable and content-addressed, same contract as local.
    snapshot_id = store.bronze_snapshot_id(dataset, day1)
    assert snapshot_id.startswith(f"{dataset}@2026-01-05#")
    assert store.bronze_snapshot_id(dataset, day1) == snapshot_id

    with pytest.raises(LookupError):
        store.read_bronze_as_of(dataset, dt.date(2026, 1, 1))


def test_tigris_query_via_duckdb_delta_scan(tigris_store: tuple[DeltaLakeStore, str]) -> None:
    store, dataset = tigris_store
    df = pd.DataFrame({"date": pd.to_datetime(["2026-01-05"]), "close": [16.0]})
    location = store.write_silver(dataset, df)

    result = store.query(f"SELECT COUNT(*) AS n FROM delta_scan('{location}')")
    assert int(result["n"].iloc[0]) == 1
