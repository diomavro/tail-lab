"""Object-storage-backed :class:`LakeStore` on Fly Tigris (S3-compatible).

Replaces the retired lakeFS-Cloud plan (``docs/adr/0006``, ``docs/adr/0012``):
no versioning service, no git-style data branching — just immutable Parquet
on object storage, read/written directly. All the point-in-time and
immutable-bronze guarantees are unchanged and come from the same shared base
as :class:`tail_lab.lake.store.LocalParquetLakeStore` —
:class:`~tail_lab.lake.store.ParquetSnapshotLakeStore`. This module supplies
only the five storage primitives that base class needs, backed by
``pyarrow.fs.S3FileSystem`` (already a project dependency, so no new one is
needed for object storage), plus DuckDB ``httpfs``/S3 configuration for
``query()``.

Selected via ``TAIL_LAB_LAKE_BACKEND=tigris`` — see
``tail_lab.config.get_lake_store``. Never constructed with hardcoded
credentials; all connection info comes from :class:`tail_lab.config.Settings`.
"""

from __future__ import annotations

import duckdb
import pyarrow.fs as pafs

from tail_lab.lake.store import ParquetSnapshotLakeStore


def _strip_scheme(endpoint_url: str) -> str:
    """``pyarrow.fs.S3FileSystem``'s ``endpoint_override`` wants a bare
    ``host[:port]``, not a ``https://`` URL."""
    return endpoint_url.removeprefix("https://").removeprefix("http://")


class TigrisLakeStore(ParquetSnapshotLakeStore):
    """S3-compatible object-storage backend on Fly Tigris.

    ``bucket`` is a top-level object-storage bucket; every medallion key
    (``bronze/<dataset>/<date>/data.parquet``, ``{silver,gold}/<dataset>/data.parquet``)
    lives under it. Bronze immutability holds because the primitive
    ``_exists``/``_write_bytes`` pair are the only things
    :class:`~tail_lab.lake.store.ParquetSnapshotLakeStore` uses to decide
    whether a write is a no-op — identical logic to the local backend,
    just against object-storage keys instead of filesystem paths.
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._endpoint_host = _strip_scheme(endpoint_url)
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._fs = pafs.S3FileSystem(
            access_key=access_key_id,
            secret_key=secret_access_key,
            region=region,
            endpoint_override=self._endpoint_host,
            scheme="https",
        )

    def _bucket_key(self, key: str) -> str:
        return f"{self._bucket}/{key}"

    # ---- ParquetSnapshotLakeStore primitives -------------------------------

    def _exists(self, key: str) -> bool:
        info = self._fs.get_file_info(self._bucket_key(key))
        return bool(info.type == pafs.FileType.File)

    def _list_subpartitions(self, prefix: str) -> list[str]:
        selector = pafs.FileSelector(self._bucket_key(prefix), recursive=False)
        try:
            entries = self._fs.get_file_info(selector)
        except FileNotFoundError:
            return []
        return [entry.base_name for entry in entries]

    def _write_bytes(self, key: str, data: bytes) -> None:
        with self._fs.open_output_stream(self._bucket_key(key)) as f:
            f.write(data)

    def _read_bytes(self, key: str) -> bytes:
        with self._fs.open_input_stream(self._bucket_key(key)) as f:
            result: bytes = f.read()
            return result

    def _location(self, key: str) -> str:
        return f"s3://{self._bucket_key(key)}"

    # ---- query() ------------------------------------------------------------

    def _configure_connection(self, con: duckdb.DuckDBPyConnection) -> None:
        con.execute("INSTALL httpfs;")
        con.execute("LOAD httpfs;")
        con.execute("SET s3_endpoint=?;", [self._endpoint_host])
        con.execute("SET s3_region=?;", [self._region])
        con.execute("SET s3_access_key_id=?;", [self._access_key_id])
        con.execute("SET s3_secret_access_key=?;", [self._secret_access_key])
        con.execute("SET s3_url_style='path';")
        con.execute("SET s3_use_ssl=true;")
