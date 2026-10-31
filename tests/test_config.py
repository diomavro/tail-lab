"""The lakehouse-backend factory (tail_lab.config.get_lake_store) — backend
selection must default to local (no creds needed) and never construct a
Tigris backend with partial/missing credentials."""

from __future__ import annotations

from pathlib import Path

import pytest

from tail_lab.config import Settings, get_lake_store, get_quarantine_blobs
from tail_lab.lake.blob_store import BlobStore
from tail_lab.lake.store import DeltaLakeStore


def test_default_backend_is_local(tmp_path: Path) -> None:
    # _env_file=None: ignore any local .env (e.g. Dio's, which sets
    # TAIL_LAB_LAKE_BACKEND=tigris for his own manual testing) so this test
    # verifies the actual *default*, not whatever the developer's machine
    # happens to have configured.
    settings = Settings(_env_file=None, lake_root=tmp_path)  # type: ignore[call-arg]
    store = get_lake_store(settings)
    assert isinstance(store, DeltaLakeStore)
    assert store.root == tmp_path
    assert store.storage_options is None


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
    assert isinstance(store, DeltaLakeStore)
    assert store.root == "s3://test-bucket"
    assert store.storage_options is not None
    assert store.storage_options["AWS_S3_ALLOW_UNSAFE_RENAME"] == "true"
    assert store.storage_options["AWS_ACCESS_KEY_ID"] == "dummy"


def test_quarantine_blobs_share_the_lake_root(tmp_path: Path) -> None:
    """A blob written through ``get_quarantine_blobs`` is readable at the
    root ``get_lake_store`` reports. Explicit settings only: the zero-arg
    getters read ``.env``, which on Dio's checkout is production."""
    settings = Settings(_env_file=None, lake_backend="local", lake_root=tmp_path)  # type: ignore[call-arg]
    get_quarantine_blobs(settings).write_json("ops/quarantine/x.json", {"ok": True})
    assert BlobStore(get_lake_store(settings).location).read_json("ops/quarantine/x.json") == {
        "ok": True
    }


def test_quarantine_blobs_use_the_same_tigris_root_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, tuple[object, object]] = {}

    def recorder(name: str) -> object:
        def build(root: object, *, storage_options: object = None) -> object:
            seen[name] = (root, storage_options)
            return object()

        return build

    monkeypatch.setattr("tail_lab.config.BlobStore", recorder("blobs"))
    monkeypatch.setattr("tail_lab.config.DeltaLakeStore", recorder("lake"))
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        lake_backend="tigris",
        s3_bucket="b",
        s3_endpoint_url="https://fly.storage.tigris.dev",
        s3_region="auto",
        aws_access_key_id="id",
        aws_secret_access_key="secret",
    )
    get_quarantine_blobs(settings)
    get_lake_store(settings)
    assert seen["blobs"] == seen["lake"]
    assert seen["blobs"][0] == "s3://b"
