"""The medallion lakehouse behind a storage-agnostic interface.

Everything above this layer (``ingestion``, ``transforms``, ``research``,
``api``) depends only on the abstract :class:`LakeStore`, never on a
concrete implementation. :class:`LocalParquetLakeStore` is the implementation
available today: bronze is immutable parquet under
``data/bronze/<dataset>/<ingest_date>/data.parquet``; silver/gold are
derived parquet under ``data/{silver,gold}/<dataset>/data.parquet``, and
:meth:`LocalParquetLakeStore.query` reads them with DuckDB.

A ``LakeFsLakeStore`` implementation (versioning the same three layers as
lakeFS Cloud commits on S3-compatible object storage, per the README) drops
in later behind the same interface once that account exists — see
``lakefs_stub.py``. See ``docs/adr/0011`` (HUMAN_TODO: create lakeFS Cloud
account, wire ``LakeFsLakeStore`` in).
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from pathlib import Path

import duckdb
import pandas as pd

#: The three medallion layers, in read/write order.
Layer = str  # "bronze" | "silver" | "gold"


class LakeStore(ABC):
    """Abstract medallion lakehouse: immutable bronze, derived silver/gold.

    Point-in-time correctness (the #1 invariant, README) lives entirely in
    :meth:`read_bronze_as_of`: it MUST NOT return rows from any bronze
    snapshot ingested after ``as_of``. Every downstream read (silver, gold,
    research metrics) is derived from a bronze-as-of read, so the guarantee
    propagates.
    """

    @abstractmethod
    def write_bronze(self, dataset: str, ingest_date: dt.date, df: pd.DataFrame) -> Path:
        """Commit a raw snapshot to bronze. Never overwrites an existing snapshot
        for the same ``(dataset, ingest_date)`` — re-ingesting the same day is a
        no-op that preserves the original file untouched."""

    @abstractmethod
    def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
        """Read the bronze snapshot known as of ``as_of``: the most recent
        snapshot ingested on or before that date. Raises ``LookupError`` if
        no snapshot exists on or before ``as_of``."""

    @abstractmethod
    def write_silver(self, dataset: str, df: pd.DataFrame) -> Path:
        """Overwrite the derived silver table for ``dataset``."""

    @abstractmethod
    def read_silver(self, dataset: str) -> pd.DataFrame:
        """Read the current derived silver table for ``dataset``."""

    @abstractmethod
    def write_gold(self, dataset: str, df: pd.DataFrame) -> Path:
        """Overwrite the derived gold table for ``dataset``."""

    @abstractmethod
    def read_gold(self, dataset: str) -> pd.DataFrame:
        """Read the current derived gold table for ``dataset``."""

    @abstractmethod
    def query(self, sql: str) -> pd.DataFrame:
        """Run a read-only SQL query (DuckDB) over the lake's parquet files."""


class LocalParquetLakeStore(LakeStore):
    """Local-disk implementation: bronze/silver/gold as parquet under ``root``,
    queried with an in-process DuckDB connection. No external account needed."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _bronze_dir(self, dataset: str, ingest_date: dt.date) -> Path:
        return self._root / "bronze" / dataset / ingest_date.isoformat()

    def _bronze_dataset_dir(self, dataset: str) -> Path:
        return self._root / "bronze" / dataset

    def _derived_path(self, layer: Layer, dataset: str) -> Path:
        return self._root / layer / dataset / "data.parquet"

    def write_bronze(self, dataset: str, ingest_date: dt.date, df: pd.DataFrame) -> Path:
        partition_dir = self._bronze_dir(dataset, ingest_date)
        target = partition_dir / "data.parquet"
        if target.exists():
            # Immutable: never overwrite an existing snapshot. Re-ingesting
            # the same day is a no-op that preserves the original file.
            return target
        partition_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(target, index=False)
        return target

    def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
        dataset_dir = self._bronze_dataset_dir(dataset)
        if not dataset_dir.exists():
            raise LookupError(f"no bronze data for dataset {dataset!r}")
        candidates = sorted(
            dt.date.fromisoformat(p.name)
            for p in dataset_dir.iterdir()
            if p.is_dir() and (p / "data.parquet").exists()
        )
        eligible = [d for d in candidates if d <= as_of]
        if not eligible:
            raise LookupError(
                f"no bronze snapshot for dataset {dataset!r} on or before {as_of.isoformat()}"
            )
        snapshot_date = max(eligible)
        path = self._bronze_dir(dataset, snapshot_date) / "data.parquet"
        return pd.read_parquet(path)

    def write_silver(self, dataset: str, df: pd.DataFrame) -> Path:
        return self._write_derived("silver", dataset, df)

    def read_silver(self, dataset: str) -> pd.DataFrame:
        return self._read_derived("silver", dataset)

    def write_gold(self, dataset: str, df: pd.DataFrame) -> Path:
        return self._write_derived("gold", dataset, df)

    def read_gold(self, dataset: str) -> pd.DataFrame:
        return self._read_derived("gold", dataset)

    def _write_derived(self, layer: Layer, dataset: str, df: pd.DataFrame) -> Path:
        path = self._derived_path(layer, dataset)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        return path

    def _read_derived(self, layer: Layer, dataset: str) -> pd.DataFrame:
        path = self._derived_path(layer, dataset)
        if not path.exists():
            raise LookupError(f"no {layer} data for dataset {dataset!r}")
        return pd.read_parquet(path)

    def query(self, sql: str) -> pd.DataFrame:
        con = duckdb.connect(database=":memory:")
        try:
            return con.execute(sql).fetchdf()
        finally:
            con.close()
