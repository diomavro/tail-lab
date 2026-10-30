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

It also holds the Deribit writers' quarantine path (``docs/adr/0032``):
:func:`commit_then_quarantine` writes a book's dataset first and then its
per-capture quarantine blob through :func:`write_quarantine`, so the route
and ``--local`` cannot drift on that order.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

import pandas as pd
import pyarrow.fs as pafs

from tail_lab.lake.store import LAKE_WRITE_ERRORS, LakeStore, _strip_scheme


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

    def write_json(self, key: str, obj: Mapping[str, object]) -> None:
        """Write ``obj`` as the JSON blob at ``key``, overwriting any
        existing blob at that key. Creates any missing parent "directory"."""
        path = self._full_path(key)
        parent = path.rsplit("/", 1)[0]
        self._fs.create_dir(parent, recursive=True)
        data = json.dumps(dict(obj), indent=2).encode("utf-8")
        with self._fs.open_output_stream(path) as f:
            f.write(data)

    def read_json(self, key: str) -> dict[str, object]:
        """Read and parse the JSON blob at ``key``. Raises ``LookupError``
        if no blob exists at that key, ``ValueError`` if it is not a JSON
        object."""
        path = self._full_path(key)
        try:
            with self._fs.open_input_stream(path) as f:
                data = f.read()
        except FileNotFoundError as exc:
            raise LookupError(f"no blob at {key!r}") from exc
        return _json_object(json.loads(data), key)

    def list_json(self, prefix: str) -> list[dict[str, object]]:
        """Parse and return every ``.json`` blob under ``prefix``. Returns
        an empty list if the prefix doesn't exist yet."""
        base = self._full_path(prefix.rstrip("/"))
        selector = pafs.FileSelector(base, recursive=True, allow_not_found=True)
        infos = self._fs.get_file_info(selector)
        records: list[dict[str, object]] = []
        for info in infos:
            if info.type != pafs.FileType.File or not info.path.endswith(".json"):
                continue
            with self._fs.open_input_stream(info.path) as f:
                records.append(_json_object(json.loads(f.read()), info.path))
        return records


def write_quarantine(
    blobs: BlobStore,
    key: str,
    doc: Mapping[str, object],
    *,
    log_lines: Sequence[Mapping[str, str]],
    log: Callable[[Mapping[str, str]], None],
    on_error: Callable[[BaseException], None],
) -> bool:
    """Write a quarantine blob, best-effort but never silent; return whether
    it was written.

    The ONE helper both Deribit writers (the route and ``--local``) call
    after the dataset write. On exactly :data:`LAKE_WRITE_ERRORS` it calls
    ``on_error(exc)`` once -- each caller logs the cause, so the fallback
    never hides why -- and then ``log(line)`` for every prebuilt line, one
    per quarantined row, which is then the only copy of those rows. Anything
    else propagates: the caller proved the document serialisable before
    writing anything, so a ``TypeError`` here would be a bug.

    Takes prebuilt ``log_lines`` and a ``log`` callback rather than building
    them, so ``lake`` gains no import from a higher layer (``ARCHITECTURE.md``: ``lake``
    imports only ``contracts``).
    """
    try:
        blobs.write_json(key, doc)
    except LAKE_WRITE_ERRORS as exc:
        on_error(exc)
        for line in log_lines:
            log(line)
        return False
    return True


class QuarantineWrite(NamedTuple):
    """Everything :func:`write_quarantine` needs, bundled so a writer hands
    :func:`commit_then_quarantine` one value with its own log event bound."""

    key: str
    doc: Mapping[str, object]
    log_lines: Sequence[Mapping[str, str]]
    log: Callable[[Mapping[str, str]], None]
    on_error: Callable[[BaseException], None]


def commit_then_quarantine(
    store: LakeStore,
    blobs: BlobStore,
    *,
    dataset: str,
    ingest_date: dt.date,
    valid: pd.DataFrame,
    quarantine: QuarantineWrite | None,
) -> tuple[str | None, bool | None]:
    """``(bronze_path, quarantine_written)``: the dataset first (skipped when
    ``valid`` is empty -- an empty write breaks a Delta table), then the
    quarantine blob, best-effort.

    A dataset write that raises may still have landed (delta-rs finding its
    own commit, or a failed post-commit checkpoint), and a retry would then
    answer "already captured" -- so the quarantine is attempted BEFORE the
    dataset's error propagates, and it is that error that propagates. Shared
    by both Deribit writers so the order cannot drift between them.
    """
    bronze_path = None
    if not valid.empty:
        try:
            bronze_path = store.write_bronze(dataset, ingest_date, valid)
        except LAKE_WRITE_ERRORS:
            if quarantine is not None:
                _write(blobs, quarantine)
            raise
    if quarantine is None:
        return bronze_path, None
    return bronze_path, _write(blobs, quarantine)


def _write(blobs: BlobStore, quarantine: QuarantineWrite) -> bool:
    return write_quarantine(
        blobs,
        quarantine.key,
        quarantine.doc,
        log_lines=quarantine.log_lines,
        log=quarantine.log,
        on_error=quarantine.on_error,
    )


def _json_object(parsed: object, where: str) -> dict[str, object]:
    """Every blob here is written by ``write_json`` from a mapping, so one
    that parses to anything else is corrupt -- say so by name rather than
    let a caller's ``Model(**blob)`` fail with an unrelated TypeError."""
    if not isinstance(parsed, dict):
        raise ValueError(f"blob {where!r} is not a JSON object")
    return parsed
