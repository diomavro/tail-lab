"""Minimal JSON blob storage on the same root as the medallion lakehouse.

Kept separate from :class:`~tail_lab.lake.store.DeltaLakeStore`: Delta
tables are the right abstraction for the append-only, partitioned bronze/
silver/gold datasets the research pipeline reads, but they're overkill for
a handful of individually-addressable JSON records (e.g. the ops feedback
records ``feedback/store.py`` persists) that just need get/write/list-by-
prefix. This is deliberately kept inside ``lake/`` rather than a new module
elsewhere so the "who talks to object storage" surface stays in one place
(``docs/adr/0012``, ``docs/adr/0013``) — same root, same credentials, same
local-vs-Tigris selection made once in ``tail_lab.config``, just via
``pyarrow.fs`` instead of delta-rs/DuckDB since there is no Delta table
here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pyarrow.fs as pafs

from tail_lab.lake.store import _strip_scheme


class BlobStore:
    """Read/write/list individual JSON blobs.

    Rooted at a local directory or an ``s3://<bucket>`` URI — mirrors
    :class:`~tail_lab.lake.store.DeltaLakeStore`'s constructor shape and
    backend selection so callers building both from the same
    ``tail_lab.config.Settings`` don't need two different conventions.
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
        self._fs, self._base_path = self._build_filesystem()

    def _build_filesystem(self) -> tuple[pafs.FileSystem, str]:
        if self._is_s3:
            assert self._storage_options is not None
            assert isinstance(self._root, str)
            fs: pafs.FileSystem = pafs.S3FileSystem(
                access_key=self._storage_options["AWS_ACCESS_KEY_ID"],
                secret_key=self._storage_options["AWS_SECRET_ACCESS_KEY"],
                region=self._storage_options["AWS_REGION"],
                endpoint_override=_strip_scheme(self._storage_options["AWS_ENDPOINT_URL"]),
                scheme="https",
            )
            return fs, self._root.removeprefix("s3://")
        assert isinstance(self._root, Path)
        return pafs.LocalFileSystem(), str(self._root)

    def _full_path(self, key: str) -> str:
        return f"{self._base_path}/{key}"

    def write_json(self, key: str, obj: Mapping[str, Any]) -> None:
        """Write ``obj`` as the JSON blob at ``key``, overwriting any
        existing blob at that key. Creates any missing parent "directory"."""
        path = self._full_path(key)
        parent = path.rsplit("/", 1)[0]
        self._fs.create_dir(parent, recursive=True)
        data = json.dumps(dict(obj), indent=2).encode("utf-8")
        with self._fs.open_output_stream(path) as f:
            f.write(data)

    def read_json(self, key: str) -> dict[str, Any]:
        """Read and parse the JSON blob at ``key``. Raises ``LookupError``
        if no blob exists at that key."""
        path = self._full_path(key)
        try:
            with self._fs.open_input_stream(path) as f:
                data = f.read()
        except FileNotFoundError as exc:
            raise LookupError(f"no blob at {key!r}") from exc
        result: dict[str, Any] = json.loads(data)
        return result

    def list_json(self, prefix: str) -> list[dict[str, Any]]:
        """Parse and return every ``.json`` blob under ``prefix``. Returns
        an empty list if the prefix doesn't exist yet."""
        base = self._full_path(prefix.rstrip("/"))
        selector = pafs.FileSelector(base, recursive=True, allow_not_found=True)
        infos = self._fs.get_file_info(selector)
        records: list[dict[str, Any]] = []
        for info in infos:
            if info.type != pafs.FileType.File or not info.path.endswith(".json"):
                continue
            with self._fs.open_input_stream(info.path) as f:
                records.append(json.loads(f.read()))
        return records
