from __future__ import annotations

from pathlib import Path

import pytest

from tail_lab.lake.blob_store import BlobStore


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    store.write_json("ops/feedback/abc.json", {"id": "abc", "status": "open"})
    assert store.read_json("ops/feedback/abc.json") == {"id": "abc", "status": "open"}


def test_read_missing_key_raises_lookup_error(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    with pytest.raises(LookupError):
        store.read_json("ops/feedback/missing.json")


def test_list_json_returns_empty_for_unwritten_prefix(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    assert store.list_json("ops/feedback") == []


def test_list_json_returns_every_blob_under_prefix(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    store.write_json("ops/feedback/one.json", {"id": "one"})
    store.write_json("ops/feedback/two.json", {"id": "two"})
    records = store.list_json("ops/feedback")
    assert {r["id"] for r in records} == {"one", "two"}


def test_write_overwrites_existing_key(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    store.write_json("ops/feedback/abc.json", {"id": "abc", "status": "open"})
    store.write_json("ops/feedback/abc.json", {"id": "abc", "status": "resolved"})
    assert store.read_json("ops/feedback/abc.json")["status"] == "resolved"
