"""Application settings (pydantic-settings, env-overridable) and the
lakehouse-backend factory.

``TAIL_LAB_LAKE_BACKEND`` selects how the single :class:`~tail_lab.lake.store.DeltaLakeStore`
is rooted: ``"local"`` (default — no credentials needed, what CI and plain
local dev use) roots it at a local filesystem directory; ``"tigris"``
(S3-compatible object storage on Fly Tigris, ``docs/adr/0012``, ``docs/adr/0013``)
roots it at ``s3://<bucket>`` with ``storage_options`` built from the AWS_*
settings below. The Tigris connection settings read the *plain* AWS-style
env var names (``AWS_ACCESS_KEY_ID`` etc.) rather than the ``TAIL_LAB_``
prefix, since those are the names Fly sets as secrets and the names already
used in the local ``.env`` — one set of credentials, not two.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from tail_lab.lake.store import DeltaLakeStore, LakeStore


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TAIL_LAB_", env_file=".env", extra="ignore", populate_by_name=True
    )

    #: Root directory of the local parquet lake. Relative paths resolve
    #: against the process working directory (the repo root, by convention).
    #: Only used by the ``"local"`` backend.
    lake_root: Path = Path("data")

    #: Directory of the built React SPA to serve (env: TAIL_LAB_STATIC_DIR).
    #: When None, the app falls back to the in-repo ``frontend/dist`` (local
    #: dev / source runs). The Docker image sets it explicitly, because the
    #: package is pip-installed away from the repo root so the relative
    #: fallback cannot find ``dist``.
    static_dir: Path | None = None

    #: Which concrete LakeStore backend to use. "local" needs no credentials
    #: and is the default so CI and plain local dev never need any; "tigris"
    #: reads/writes S3-compatible object storage (see the fields below).
    lake_backend: Literal["local", "tigris"] = "local"

    #: Tigris bucket name (env: TAIL_LAB_S3_BUCKET). Required for "tigris".
    s3_bucket: str | None = None

    #: The remaining Tigris/S3 connection settings deliberately read the
    #: plain AWS_* env var names, not the TAIL_LAB_ prefix — see module
    #: docstring.
    s3_endpoint_url: str | None = Field(default=None, validation_alias="AWS_ENDPOINT_URL_S3")
    s3_region: str | None = Field(default=None, validation_alias="AWS_REGION")
    aws_access_key_id: str | None = Field(default=None, validation_alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(
        default=None, validation_alias="AWS_SECRET_ACCESS_KEY"
    )


def get_settings() -> Settings:
    return Settings()


def get_lake_store(settings: Settings | None = None) -> LakeStore:
    """Construct the configured :class:`~tail_lab.lake.store.LakeStore` backend.

    Never hardcodes credentials — everything comes from ``settings`` (or a
    freshly loaded :class:`Settings` if omitted).
    """
    settings = settings or get_settings()

    if settings.lake_backend == "local":
        return _local_lake_store(settings)
    return _tigris_lake_store(settings)


def _local_lake_store(settings: Settings) -> LakeStore:
    return DeltaLakeStore(settings.lake_root)


def _tigris_lake_store(settings: Settings) -> LakeStore:
    missing = [
        name
        for name, value in [
            ("TAIL_LAB_S3_BUCKET", settings.s3_bucket),
            ("AWS_ENDPOINT_URL_S3", settings.s3_endpoint_url),
            ("AWS_REGION", settings.s3_region),
            ("AWS_ACCESS_KEY_ID", settings.aws_access_key_id),
            ("AWS_SECRET_ACCESS_KEY", settings.aws_secret_access_key),
        ]
        if value is None
    ]
    if missing:
        raise ValueError(
            "TAIL_LAB_LAKE_BACKEND=tigris requires the following env vars, "
            f"none of which are set: {', '.join(missing)}"
        )
    # Narrow str | None -> str for mypy: `missing` above already proved none
    # of these are None.
    assert settings.s3_bucket is not None
    assert settings.s3_endpoint_url is not None
    assert settings.s3_region is not None
    assert settings.aws_access_key_id is not None
    assert settings.aws_secret_access_key is not None
    storage_options = {
        "AWS_ACCESS_KEY_ID": settings.aws_access_key_id,
        "AWS_SECRET_ACCESS_KEY": settings.aws_secret_access_key,
        "AWS_ENDPOINT_URL": settings.s3_endpoint_url,
        "AWS_REGION": settings.s3_region,
        # delta-rs writing to S3 with a single writer needs either this flag
        # or the (newer, not-yet-default-everywhere) conditional-put path —
        # safe here because bronze ingestion is single-writer (the daily
        # agent / `make ingest-vix`), never concurrent (docs/adr/0013).
        "AWS_S3_ALLOW_UNSAFE_RENAME": "true",
    }
    return DeltaLakeStore(f"s3://{settings.s3_bucket}", storage_options=storage_options)
