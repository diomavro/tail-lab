"""Future lakeFS Cloud implementation of :class:`LakeStore`.

HUMAN_TODO (per README "Free-data first" principle — this needs an account,
so the agent must never attempt it): create a lakeFS Cloud account backed by
S3-compatible object storage (Fly Tigris or Cloudflare R2), then implement
this class so bronze commits become lakeFS commits on a data branch, and
``read_bronze_as_of`` resolves to the commit whose timestamp is <= ``as_of``
instead of scanning local directories. Nothing else in the codebase changes:
every caller depends on the ``LakeStore`` interface in ``store.py``, not on
this implementation, so swapping ``LocalParquetLakeStore`` for
``LakeFsLakeStore`` behind a config flag is the entire migration.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from tail_lab.lake.store import LakeStore


class LakeFsLakeStore(LakeStore):
    """Not implemented — see module docstring. Placeholder for `docs/adr/0011`."""

    def __init__(self, *_: object, **__: object) -> None:
        raise NotImplementedError(
            "LakeFsLakeStore requires a lakeFS Cloud account (HUMAN_TODO); "
            "use LocalParquetLakeStore until that account exists."
        )

    def write_bronze(self, dataset: str, ingest_date: dt.date, df: pd.DataFrame) -> Path:
        raise NotImplementedError

    def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
        raise NotImplementedError

    def write_silver(self, dataset: str, df: pd.DataFrame) -> Path:
        raise NotImplementedError

    def read_silver(self, dataset: str) -> pd.DataFrame:
        raise NotImplementedError

    def write_gold(self, dataset: str, df: pd.DataFrame) -> Path:
        raise NotImplementedError

    def read_gold(self, dataset: str) -> pd.DataFrame:
        raise NotImplementedError

    def query(self, sql: str) -> pd.DataFrame:
        raise NotImplementedError
