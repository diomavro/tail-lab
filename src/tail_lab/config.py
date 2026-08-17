"""Application settings (pydantic-settings, env-overridable)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAIL_LAB_")

    #: Root directory of the local parquet lake. Relative paths resolve
    #: against the process working directory (the repo root, by convention).
    lake_root: Path = Path("data")


def get_settings() -> Settings:
    return Settings()
