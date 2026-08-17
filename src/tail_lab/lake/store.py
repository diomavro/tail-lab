"""The medallion lakehouse behind a storage-agnostic interface.

Everything above this layer (``ingestion``, ``transforms``, ``research``,
``api``) depends only on the abstract :class:`LakeStore`, never on a
concrete implementation.

The point-in-time / immutability logic — the platform's #1 invariant
(``docs/adr/0009``) — lives in exactly one place: :class:`ParquetSnapshotLakeStore`.
It implements the medallion path scheme, the as-of resolution (latest
bronze snapshot ingested on or before ``as_of``), the immutable-bronze
write (never overwrite an existing snapshot), and a content-hash snapshot
id for reproducibility (``docs/adr/0012``) — all on top of five small
storage primitives a backend must supply (``_exists``, ``_list_subpartitions``,
``_write_bytes``, ``_read_bytes``, ``_location``). A concrete backend never
reimplements the as-of/immutability rules; it only tells the base class how
to touch bytes.

Two concrete backends implement those primitives:

- :class:`LocalParquetLakeStore` — local filesystem under ``root``. The
  DEFAULT backend (no credentials needed), used by CI and local dev.
- :class:`tail_lab.lake.tigris_store.TigrisLakeStore` — S3-compatible
  object storage on Fly Tigris (``docs/adr/0012``). Selected via
  ``TAIL_LAB_LAKE_BACKEND=tigris`` (``tail_lab.config.get_lake_store``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
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

    Write methods return a ``str`` **location** for the data just written —
    an absolute local filesystem path for :class:`LocalParquetLakeStore`, an
    ``s3://...`` URI for an object-storage backend. It is an opaque
    identifier suitable for logging or building a follow-up ``query()``; do
    not assume it is a local filesystem path.
    """

    @abstractmethod
    def write_bronze(self, dataset: str, ingest_date: dt.date, df: pd.DataFrame) -> str:
        """Commit a raw snapshot to bronze. Never overwrites an existing snapshot
        for the same ``(dataset, ingest_date)`` — re-ingesting the same day is a
        no-op that preserves the original data untouched."""

    @abstractmethod
    def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
        """Read the bronze snapshot known as of ``as_of``: the most recent
        snapshot ingested on or before that date. Raises ``LookupError`` if
        no snapshot exists on or before ``as_of``."""

    @abstractmethod
    def bronze_snapshot_id(self, dataset: str, as_of: dt.date) -> str:
        """A stable identifier for the exact bronze snapshot ``read_bronze_as_of``
        would resolve to: the ingest-date partition plus a content hash. This
        is what a reproducible result cites in place of a lakeFS commit id
        (``docs/adr/0012``). Raises ``LookupError`` under the same condition
        as :meth:`read_bronze_as_of`."""

    @abstractmethod
    def write_silver(self, dataset: str, df: pd.DataFrame) -> str:
        """Overwrite the derived silver table for ``dataset``."""

    @abstractmethod
    def read_silver(self, dataset: str) -> pd.DataFrame:
        """Read the current derived silver table for ``dataset``."""

    @abstractmethod
    def write_gold(self, dataset: str, df: pd.DataFrame) -> str:
        """Overwrite the derived gold table for ``dataset``."""

    @abstractmethod
    def read_gold(self, dataset: str) -> pd.DataFrame:
        """Read the current derived gold table for ``dataset``."""

    @abstractmethod
    def query(self, sql: str) -> pd.DataFrame:
        """Run a read-only SQL query (DuckDB) over the lake's parquet files."""


class ParquetSnapshotLakeStore(LakeStore):
    """Shared as-of / immutability / medallion-path logic for any Parquet-backed
    lakehouse, parameterized over a minimal storage primitive set.

    Subclasses implement exactly five primitives operating on a ``key``
    (a POSIX-style relative path such as ``"bronze/vix/2026-01-05/data.parquet"``):

    - :meth:`_exists` — does this key exist?
    - :meth:`_list_subpartitions` — immediate child names under a key prefix.
    - :meth:`_write_bytes` / :meth:`_read_bytes` — raw byte I/O.
    - :meth:`_location` — the backend-specific location string for a key.

    Everything else (as-of resolution, the never-overwrite rule, parquet
    (de)serialization, the snapshot id, and ``query()``'s connection setup
    hook) is implemented here, once.
    """

    # ---- medallion path scheme ------------------------------------------------

    @staticmethod
    def _bronze_prefix(dataset: str) -> str:
        return f"bronze/{dataset}"

    @classmethod
    def _bronze_key(cls, dataset: str, ingest_date: dt.date) -> str:
        return f"{cls._bronze_prefix(dataset)}/{ingest_date.isoformat()}/data.parquet"

    @staticmethod
    def _derived_key(layer: Layer, dataset: str) -> str:
        return f"{layer}/{dataset}/data.parquet"

    # ---- storage primitives concrete backends must supply ---------------------

    @abstractmethod
    def _exists(self, key: str) -> bool: ...

    @abstractmethod
    def _list_subpartitions(self, prefix: str) -> list[str]:
        """Immediate child names under ``prefix`` (a key with no trailing
        slash). Returns ``[]`` if ``prefix`` doesn't exist."""

    @abstractmethod
    def _write_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def _read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def _location(self, key: str) -> str: ...

    # ---- parquet <-> bytes, centralized so both backends serialize identically

    @staticmethod
    def _serialize(df: pd.DataFrame) -> bytes:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        return buf.getvalue()

    @staticmethod
    def _deserialize(data: bytes) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(data))

    # ---- as-of resolution -------------------------------------------------

    def _list_bronze_ingest_dates(self, dataset: str) -> list[dt.date]:
        names = self._list_subpartitions(self._bronze_prefix(dataset))
        dates: list[dt.date] = []
        for name in names:
            try:
                candidate = dt.date.fromisoformat(name)
            except ValueError:
                continue
            if self._exists(self._bronze_key(dataset, candidate)):
                dates.append(candidate)
        return sorted(dates)

    def _resolve_bronze_snapshot(self, dataset: str, as_of: dt.date) -> dt.date:
        dates = self._list_bronze_ingest_dates(dataset)
        if not dates:
            raise LookupError(f"no bronze data for dataset {dataset!r}")
        eligible = [d for d in dates if d <= as_of]
        if not eligible:
            raise LookupError(
                f"no bronze snapshot for dataset {dataset!r} on or before {as_of.isoformat()}"
            )
        return max(eligible)

    # ---- LakeStore implementation ------------------------------------------

    def write_bronze(self, dataset: str, ingest_date: dt.date, df: pd.DataFrame) -> str:
        key = self._bronze_key(dataset, ingest_date)
        if self._exists(key):
            # Immutable: never overwrite an existing snapshot. Re-ingesting
            # the same day is a no-op that preserves the original data.
            return self._location(key)
        self._write_bytes(key, self._serialize(df))
        return self._location(key)

    def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
        snapshot_date = self._resolve_bronze_snapshot(dataset, as_of)
        return self._deserialize(self._read_bytes(self._bronze_key(dataset, snapshot_date)))

    def bronze_snapshot_id(self, dataset: str, as_of: dt.date) -> str:
        snapshot_date = self._resolve_bronze_snapshot(dataset, as_of)
        key = self._bronze_key(dataset, snapshot_date)
        digest = hashlib.sha256(self._read_bytes(key)).hexdigest()[:16]
        return f"{dataset}@{snapshot_date.isoformat()}#{digest}"

    def write_silver(self, dataset: str, df: pd.DataFrame) -> str:
        return self._write_derived("silver", dataset, df)

    def read_silver(self, dataset: str) -> pd.DataFrame:
        return self._read_derived("silver", dataset)

    def write_gold(self, dataset: str, df: pd.DataFrame) -> str:
        return self._write_derived("gold", dataset, df)

    def read_gold(self, dataset: str) -> pd.DataFrame:
        return self._read_derived("gold", dataset)

    def _write_derived(self, layer: Layer, dataset: str, df: pd.DataFrame) -> str:
        key = self._derived_key(layer, dataset)
        self._write_bytes(key, self._serialize(df))
        return self._location(key)

    def _read_derived(self, layer: Layer, dataset: str) -> pd.DataFrame:
        key = self._derived_key(layer, dataset)
        if not self._exists(key):
            raise LookupError(f"no {layer} data for dataset {dataset!r}")
        return self._deserialize(self._read_bytes(key))

    def query(self, sql: str) -> pd.DataFrame:
        con = duckdb.connect(database=":memory:")
        try:
            self._configure_connection(con)
            return con.execute(sql).fetchdf()
        finally:
            con.close()

    def _configure_connection(self, con: duckdb.DuckDBPyConnection) -> None:
        """Hook for backends that need to prepare the DuckDB session before
        ``query()`` runs ``sql`` (e.g. installing ``httpfs`` and setting S3
        credentials). No-op for local disk."""


class LocalParquetLakeStore(ParquetSnapshotLakeStore):
    """Local-disk implementation: bronze/silver/gold as parquet under ``root``,
    queried with an in-process DuckDB connection. No external account needed."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _full_path(self, key: str) -> Path:
        return self._root / key

    def _exists(self, key: str) -> bool:
        return self._full_path(key).exists()

    def _list_subpartitions(self, prefix: str) -> list[str]:
        directory = self._full_path(prefix)
        if not directory.exists():
            return []
        return [p.name for p in directory.iterdir() if p.is_dir()]

    def _write_bytes(self, key: str, data: bytes) -> None:
        path = self._full_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _read_bytes(self, key: str) -> bytes:
        return self._full_path(key).read_bytes()

    def _location(self, key: str) -> str:
        return str(self._full_path(key))
