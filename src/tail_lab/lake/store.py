"""The medallion lakehouse behind a storage-agnostic interface.

Everything above this layer (``ingestion``, ``transforms``, ``research``,
``api``) depends only on the abstract :class:`LakeStore`, never on a
concrete implementation.

The point-in-time / immutability logic — the platform's #1 invariant
(``docs/adr/0009``) — lives in exactly one place: :class:`DeltaLakeStore`.
Bronze is one **Delta table per dataset**, partitioned by ``ingest_date``
(``docs/adr/0013``): each :meth:`DeltaLakeStore.write_bronze` call appends a
new ``ingest_date`` partition; an ingest date that already has a partition is
a no-op (immutable bronze). ``read_bronze_as_of`` resolves the latest
``ingest_date`` partition on or before the requested ``as_of`` and reads only
that partition. Silver/gold are single Delta tables per dataset, overwritten
on every write (matching the previous Parquet backends' semantics).

One implementation works against both a local filesystem root (the default,
no credentials, used by CI and local dev) and an ``s3://`` root on Fly Tigris
(``docs/adr/0012``, ``docs/adr/0013``) — selected by whether ``root`` is
prefixed with ``s3://`` and whether ``storage_options`` is supplied, both
decided once in :func:`tail_lab.config.get_lake_store`. Delta-rs
(the ``deltalake`` package) supplies ACID writes, time travel, and a
standard on-disk format DuckDB reads natively via its ``delta`` extension;
see ``docs/adr/0013`` for the rationale over hand-rolled immutable Parquet.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import TableNotFoundError

#: The three medallion layers, in read/write order.
Layer = str  # "bronze" | "silver" | "gold"

#: The bronze partition column. Not a data column of any dataset — added on
#: write, stripped on read — so callers never see it in a returned frame.
_INGEST_DATE_COL = "ingest_date"


class LakeStore(ABC):
    """Abstract medallion lakehouse: immutable bronze, derived silver/gold.

    Point-in-time correctness (the #1 invariant, README) lives entirely in
    :meth:`read_bronze_as_of`: it MUST NOT return rows from any bronze
    snapshot ingested after ``as_of``. Every downstream read (silver, gold,
    research metrics) is derived from a bronze-as-of read, so the guarantee
    propagates.

    Write methods return a ``str`` **location** for the data just written —
    an absolute local filesystem path for the local backend, an ``s3://...``
    URI for the Tigris backend. It is an opaque identifier suitable for
    logging or building a follow-up ``query()``; do not assume it is a local
    filesystem path.
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
    def read_bronze_column_as_of(self, dataset: str, as_of: dt.date, column: str) -> pd.Series:
        """One column of the snapshot :meth:`read_bronze_as_of` would return.

        Exists because that method materialises and caches the WHOLE partition,
        which is right for the ~1k-row datasets it was built for and wrong for
        the vendor quote panels: SPY alone is 3.3M rows / ~680 MB, and the app
        runs on a 1 GB machine. A caller that needs one column must be able to
        ask for one column.

        Raises ``LookupError`` as :meth:`read_bronze_as_of` does, and ``KeyError``
        if the snapshot has no such column.
        """

    @abstractmethod
    def read_bronze_columns_as_of(
        self, dataset: str, as_of: dt.date, columns: list[str]
    ) -> pd.DataFrame:
        """Plural sibling of :meth:`read_bronze_column_as_of`: several columns
        projected out of the Parquet scan in one read, instead of one whole
        partition.

        Built for ``OptionsDxQuoteSource.from_store``: the optionsDX quote
        datasets are 3.28M rows / ~351 MB (SPY, measured) across twelve
        columns, but a fill/mark only ever needs a handful of them (the
        symbol filter's ``underlying``, the session index's ``quote_date``,
        and the five columns ``fill``/``mark`` actually read). Materialising
        the other columns just to discard them is exactly the allocation that
        OOMs a 1 GB machine (``fly.toml``) before a single backtest request
        is served — see ``OptionsDxQuoteSource``'s class docstring for the
        measured before/after.

        Same contract as the singular method: raises ``LookupError`` if no
        snapshot exists on or before ``as_of``, and ``KeyError`` for any
        column not present in the dataset's schema.
        """

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
        """Run a read-only SQL query (DuckDB) over the lake's Delta tables."""


def _strip_scheme(endpoint_url: str) -> str:
    """DuckDB's S3 secret ``ENDPOINT`` (and ``pyarrow.fs.S3FileSystem``'s
    ``endpoint_override``) want a bare ``host[:port]``, not a ``https://`` URL."""
    return endpoint_url.removeprefix("https://").removeprefix("http://")


class DeltaLakeStore(LakeStore):
    """Delta Lake (delta-rs, no Spark) implementation of :class:`LakeStore`.

    Works against either a local filesystem ``root`` (pass a ``Path`` /
    plain string, no ``storage_options``) or an ``s3://<bucket>`` ``root``
    on Fly Tigris (pass ``storage_options`` built from AWS_* credentials —
    see :func:`tail_lab.config.get_lake_store`). The as-of resolution, the
    never-overwrite immutability rule, and the content-hash snapshot id are
    implemented once, here, identically for both.

    **Bronze model.** One Delta table per dataset at ``<root>/bronze/<dataset>``,
    partitioned by an ``ingest_date`` column added on write and stripped on
    read. Each ``write_bronze`` call appends a new partition; a partition
    that already exists is left untouched (immutable bronze) and the write
    is a no-op. This means each ingest still stores that day's *full*
    snapshot (not a diff) — same storage-growth tradeoff the previous
    Parquet-per-day layout had. A future optimization (out of scope here,
    tracked in ``AGENT_TODO.md``) could switch to a diff/merge write once
    the snapshot semantics are no longer load-bearing for a given dataset.

    **As-of reads.** ``read_bronze_as_of`` lists the table's ``ingest_date``
    partitions from the Delta log (via ``get_add_actions`` — log metadata
    only, no data read), picks the latest one on or before ``as_of``, and
    reads only that partition.

    **Snapshot id.** Content-addressed: sha256 of the resolved partition's
    canonical Parquet bytes, truncated to 16 hex chars — the same shape the
    Parquet backends used, computed from the logical row content instead of
    a single physical file's bytes (robust to a future ``OPTIMIZE``/compaction
    changing file layout without changing content).

    **Silver/gold.** A single non-partitioned Delta table per dataset,
    overwritten (``mode="overwrite"``) on every write — same "current
    derived view" semantics as before, now with the crash-safety of a Delta
    commit instead of a bare file overwrite.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        storage_options: Mapping[str, str] | None = None,
    ) -> None:
        root_str = str(root)
        self._is_s3 = root_str.startswith("s3://")
        self._root: Path | str = root_str.rstrip("/") if self._is_s3 else Path(root)
        self._storage_options: dict[str, str] | None = (
            dict(storage_options) if storage_options is not None else None
        )
        # Bronze partitions are immutable, so a partition read is cached by its
        # resolved (dataset, snapshot_date) — self-invalidating: a new ingest
        # produces a new snapshot_date -> cache miss -> fresh read, while a
        # historical as-of read stays cached forever. as-of is resolved on
        # every call (a cheap Delta-log metadata read), memoized briefly so the
        # leaderboard's 35 resolutions don't each hit S3.
        self._frame_cache: OrderedDict[tuple[str, str], pd.DataFrame] = OrderedDict()
        self._resolve_cache: dict[tuple[str, str], tuple[float, dt.date]] = {}

    @property
    def root(self) -> Path | str:
        return self._root

    @property
    def storage_options(self) -> dict[str, str] | None:
        return self._storage_options

    # ---- medallion path scheme --------------------------------------------

    def _table_uri(self, key: str) -> str:
        if self._is_s3:
            return f"{self._root}/{key}"
        assert isinstance(self._root, Path)
        return str(self._root / key)

    @staticmethod
    def _bronze_table_key(dataset: str) -> str:
        return f"bronze/{dataset}"

    @staticmethod
    def _derived_table_key(layer: Layer, dataset: str) -> str:
        return f"{layer}/{dataset}"

    @staticmethod
    def _partition_location(table_uri: str, ingest_date: dt.date) -> str:
        return f"{table_uri}/{_INGEST_DATE_COL}={ingest_date.isoformat()}"

    # ---- parquet <-> bytes, for the content-hash snapshot id --------------

    @staticmethod
    def _serialize(df: pd.DataFrame) -> bytes:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        return buf.getvalue()

    # ---- Delta table access -------------------------------------------------

    def _open_delta_table(self, table_uri: str) -> DeltaTable | None:
        try:
            return DeltaTable(table_uri, storage_options=self._storage_options)
        except TableNotFoundError:
            return None

    def _existing_bronze_ingest_date_strings(self, table_uri: str) -> set[str]:
        table = self._open_delta_table(table_uri)
        if table is None:
            return set()
        actions = table.get_add_actions(flatten=True)
        col = f"partition.{_INGEST_DATE_COL}"
        if col not in actions.column_names:
            return set()
        return {v for v in actions.column(col).to_pylist() if v is not None}

    # ---- as-of resolution -------------------------------------------------

    def _list_bronze_ingest_dates(self, table_uri: str) -> list[dt.date]:
        dates: list[dt.date] = []
        for raw in self._existing_bronze_ingest_date_strings(table_uri):
            try:
                dates.append(dt.date.fromisoformat(raw))
            except ValueError:
                # Defensive: a partition value that isn't a parseable ISO
                # date is ignored rather than crashing as-of resolution.
                continue
        return sorted(dates)

    def _resolve_bronze_snapshot(self, table_uri: str, dataset: str, as_of: dt.date) -> dt.date:
        dates = self._list_bronze_ingest_dates(table_uri)
        if not dates:
            raise LookupError(f"no bronze data for dataset {dataset!r}")
        eligible = [d for d in dates if d <= as_of]
        if not eligible:
            raise LookupError(
                f"no bronze snapshot for dataset {dataset!r} on or before {as_of.isoformat()}"
            )
        return max(eligible)

    def _read_bronze_partition(self, table_uri: str, snapshot_date: dt.date) -> pd.DataFrame:
        table = DeltaTable(table_uri, storage_options=self._storage_options)
        df = table.to_pandas(partitions=[(_INGEST_DATE_COL, "=", snapshot_date.isoformat())])
        return df.drop(columns=[_INGEST_DATE_COL]).reset_index(drop=True)

    # ---- LakeStore implementation ------------------------------------------

    def write_bronze(self, dataset: str, ingest_date: dt.date, df: pd.DataFrame) -> str:
        table_uri = self._table_uri(self._bronze_table_key(dataset))
        if ingest_date.isoformat() in self._existing_bronze_ingest_date_strings(table_uri):
            # Immutable: never overwrite an existing snapshot. Re-ingesting
            # the same day is a no-op that preserves the original data.
            return self._partition_location(table_uri, ingest_date)
        to_write = df.copy()
        to_write[_INGEST_DATE_COL] = ingest_date.isoformat()
        write_deltalake(
            table_uri,
            to_write,
            mode="append",
            partition_by=[_INGEST_DATE_COL],
            storage_options=self._storage_options,
        )
        return self._partition_location(table_uri, ingest_date)

    #: Seconds a resolved snapshot_date is trusted before re-checking the Delta
    #: log — bounds how long a just-landed ingest stays invisible.
    _RESOLVE_TTL_S = 45.0
    #: Max distinct (dataset, snapshot) frames kept in memory.
    _FRAME_CACHE_MAX = 256

    def _resolve_cached(self, table_uri: str, dataset: str, as_of: dt.date) -> dt.date:
        key = (dataset, as_of.isoformat())
        hit = self._resolve_cache.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        snapshot_date = self._resolve_bronze_snapshot(table_uri, dataset, as_of)
        self._resolve_cache[key] = (time.monotonic() + self._RESOLVE_TTL_S, snapshot_date)
        return snapshot_date

    def _cached_partition(self, dataset: str, as_of: dt.date) -> tuple[dt.date, pd.DataFrame]:
        """Resolve the as-of snapshot and return its (immutable) partition,
        served from an in-process cache keyed by the resolved snapshot_date."""
        table_uri = self._table_uri(self._bronze_table_key(dataset))
        snapshot_date = self._resolve_cached(table_uri, dataset, as_of)
        key = (dataset, snapshot_date.isoformat())
        cached = self._frame_cache.get(key)
        if cached is not None:
            self._frame_cache.move_to_end(key)
            return snapshot_date, cached
        df = self._read_bronze_partition(table_uri, snapshot_date)
        self._frame_cache[key] = df
        self._frame_cache.move_to_end(key)
        while len(self._frame_cache) > self._FRAME_CACHE_MAX:
            self._frame_cache.popitem(last=False)
        return snapshot_date, df

    def read_bronze_as_of(self, dataset: str, as_of: dt.date) -> pd.DataFrame:
        # .copy() so a caller mutating the frame can't corrupt the cache; cheap
        # (a ~1k-row frame) next to the S3 read it replaces. Callers reading the
        # multi-million-row quote panels must use read_bronze_column_as_of --
        # for those this copy is hundreds of MB, not a rounding error.
        return self._cached_partition(dataset, as_of)[1].copy()

    def read_bronze_column_as_of(self, dataset: str, as_of: dt.date, column: str) -> pd.Series:
        """Projected read of a single column. Thin wrapper over
        :meth:`read_bronze_columns_as_of`, which is where the projected-read
        seam (cache reuse, schema check, ``to_pandas(columns=...)``) lives.
        """
        return self.read_bronze_columns_as_of(dataset, as_of, [column])[column]

    def read_bronze_columns_as_of(
        self, dataset: str, as_of: dt.date, columns: list[str]
    ) -> pd.DataFrame:
        """Projected read: pushes the column list into the Parquet scan.

        Deliberately does NOT populate ``_frame_cache`` -- that cache holds
        whole partitions, and seeding it from a projected read would hand the
        next full reader a frame missing most of its columns. It does reuse the
        cached partition when one is already resident, since the expensive part
        (the S3 read) has then already happened.
        """
        table_uri = self._table_uri(self._bronze_table_key(dataset))
        snapshot_date = self._resolve_cached(table_uri, dataset, as_of)
        cached = self._frame_cache.get((dataset, snapshot_date.isoformat()))
        if cached is not None:
            return cached[list(columns)].copy()
        table = DeltaTable(table_uri, storage_options=self._storage_options)
        # Checked against the schema first, once for all requested columns:
        # pyarrow raises ArrowInvalid for an unknown projection, and this
        # method's contract (and its callers' error handling) is KeyError.
        available = set(table.schema().to_arrow().names)
        missing = [c for c in columns if c not in available]
        if missing:
            raise KeyError(f"dataset {dataset!r} has no column(s) {missing!r}")
        df = table.to_pandas(
            partitions=[(_INGEST_DATE_COL, "=", snapshot_date.isoformat())],
            columns=list(columns),
        )
        return df.reset_index(drop=True)

    #: Add-action fields that describe the data rather than when it was
    #: uploaded. `modification_time` is excluded because re-uploading identical
    #: bytes changes it, which would make the id unstable for no reason.
    _SNAPSHOT_ID_FIELDS_EXCLUDED = ("modification_time",)

    def bronze_snapshot_id(self, dataset: str, as_of: dt.date) -> str:
        """Identify the resolved partition from the Delta LOG, never its data.

        This used to materialise the whole partition and sha256 its Parquet
        bytes. That is fine for the ~1k-row datasets it was written for and
        fatal for the quote panels: the SPY optionsDX partition is 3.28M rows,
        and hashing it means a full read (measured 1.6 s and a 235 MB buffer)
        plus pinning the frame in `_frame_cache`, which evicts by COUNT and so
        never releases it. `docs/STANDARDS.md` §f requires every backtest to log
        its input snapshots, so the very act of being provenance-correct would
        have undone the projection work and OOM'd a 1 GB machine.

        The log already carries what an id needs: per-file path, size, row count
        and per-column min/max/null-count statistics. Reading it costs 0.94 s
        against S3 and touches no data.

        **What this trades.** The old digest was addressed on logical CONTENT,
        so a compaction that rewrote files without changing rows produced the
        same id. This one includes the file paths, so such a rewrite produces a
        different id. That is the right way round for this repo: bronze is
        immutable and written once per `ingest_date` (`write_bronze` no-ops on
        an existing partition), nothing here runs `OPTIMIZE`, and an id that
        changes when the physical snapshot changes is a stricter provenance
        claim than one that does not. An id computed from a projection could
        not have made either claim.
        """
        table_uri = self._table_uri(self._bronze_table_key(dataset))
        snapshot_date = self._resolve_cached(table_uri, dataset, as_of)
        table = DeltaTable(table_uri, storage_options=self._storage_options)
        actions = pa.table(table.get_add_actions(flatten=True)).to_pydict()
        prefix = f"{_INGEST_DATE_COL}={snapshot_date.isoformat()}/"
        keep = [i for i, path in enumerate(actions["path"]) if path.startswith(prefix)]
        material = {
            name: [values[i] for i in keep]
            for name, values in sorted(actions.items())
            if name not in self._SNAPSHOT_ID_FIELDS_EXCLUDED
        }
        digest = hashlib.sha256(repr(material).encode()).hexdigest()[:16]
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
        table_uri = self._table_uri(self._derived_table_key(layer, dataset))
        write_deltalake(
            table_uri,
            df,
            mode="overwrite",
            storage_options=self._storage_options,
        )
        return table_uri

    def _read_derived(self, layer: Layer, dataset: str) -> pd.DataFrame:
        table_uri = self._table_uri(self._derived_table_key(layer, dataset))
        table = self._open_delta_table(table_uri)
        if table is None:
            raise LookupError(f"no {layer} data for dataset {dataset!r}")
        return table.to_pandas()

    def query(self, sql: str) -> pd.DataFrame:
        con = duckdb.connect(database=":memory:")
        try:
            self._configure_connection(con)
            return con.execute(sql).fetchdf()
        finally:
            con.close()

    def _configure_connection(self, con: duckdb.DuckDBPyConnection) -> None:
        con.execute("INSTALL delta;")
        con.execute("LOAD delta;")
        if self._storage_options is None:
            return
        # The `delta` extension resolves S3 credentials through DuckDB's
        # secrets manager, NOT the `httpfs` extension's `SET s3_*` pragmas
        # (those only apply to `read_parquet`/`read_csv` over httpfs) — the
        # delta-rs/S3 quirk this migration had to solve. Without a secret,
        # `delta_scan()` against Tigris falls through delta-rs's default AWS
        # credential chain to an IMDS (EC2 instance-metadata) lookup, which
        # hangs/fails outside AWS.
        con.execute("INSTALL httpfs;")
        con.execute("LOAD httpfs;")
        con.execute(
            "CREATE OR REPLACE SECRET tail_lab_s3 ("
            "TYPE S3, KEY_ID ?, SECRET ?, REGION ?, ENDPOINT ?, "
            "URL_STYLE 'path', USE_SSL true"
            ");",
            [
                self._storage_options["AWS_ACCESS_KEY_ID"],
                self._storage_options["AWS_SECRET_ACCESS_KEY"],
                self._storage_options["AWS_REGION"],
                _strip_scheme(self._storage_options["AWS_ENDPOINT_URL"]),
            ],
        )
