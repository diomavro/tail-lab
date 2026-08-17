"""Application settings (pydantic-settings, env-overridable) and the
lakehouse-backend factory.

``TAIL_LAB_LAKE_BACKEND`` selects the concrete :class:`~tail_lab.lake.store.LakeStore`:
``"local"`` (default — no credentials needed, what CI and plain local dev
use) or ``"tigris"`` (S3-compatible object storage on Fly Tigris,
``docs/adr/0012``). The Tigris connection settings below read the *plain*
AWS-style env var names (``AWS_ACCESS_KEY_ID`` etc.) rather than the
``TAIL_LAB_`` prefix, since those are the names Fly sets as secrets and the
names already used in the local ``.env`` — one set of credentials, not two.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from tail_lab.lake.store import LakeStore, LocalParquetLakeStore
from tail_lab.lake.tigris_store import TigrisLakeStore


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
    return LocalParquetLakeStore(settings.lake_root)


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
    return TigrisLakeStore(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key_id=settings.aws_access_key_id,
        secret_access_key=settings.aws_secret_access_key,
    )
