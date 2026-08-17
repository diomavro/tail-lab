"""The lakehouse-backend factory (tail_lab.config.get_lake_store) — backend
selection must default to local (no creds needed) and never construct a
Tigris backend with partial/missing credentials."""

from __future__ import annotations

from pathlib import Path

import pytest

from tail_lab.config import Settings, get_lake_store
from tail_lab.lake.store import LocalParquetLakeStore
from tail_lab.lake.tigris_store import TigrisLakeStore


def test_default_backend_is_local(tmp_path: Path) -> None:
    # _env_file=None: ignore any local .env (e.g. Dio's, which sets
    # TAIL_LAB_LAKE_BACKEND=tigris for his own manual testing) so this test
    # verifies the actual *default*, not whatever the developer's machine
    # happens to have configured.
    settings = Settings(_env_file=None, lake_root=tmp_path)  # type: ignore[call-arg]
    store = get_lake_store(settings)
    assert isinstance(store, LocalParquetLakeStore)
    assert store.root == tmp_path


def test_tigris_backend_requires_all_connection_settings() -> None:
    settings = Settings(_env_file=None, lake_backend="tigris")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="TAIL_LAB_S3_BUCKET"):
        get_lake_store(settings)


def test_tigris_backend_constructs_when_fully_configured() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        lake_backend="tigris",
        s3_bucket="test-bucket",
        s3_endpoint_url="https://fly.storage.tigris.dev",
        s3_region="auto",
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy",
    )
    store = get_lake_store(settings)
    assert isinstance(store, TigrisLakeStore)
