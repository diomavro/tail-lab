from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from tail_lab.lake.blob_store import BlobStore, write_quarantine


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


def test_a_blob_that_is_not_an_object_is_a_named_failure(tmp_path: Path) -> None:
    """Every blob is written from a mapping, so a list or scalar is corruption.
    Callers validate the result with ``Model.model_validate``; handing them a
    list would fail far from the cause, so the store names it here."""
    store = BlobStore(tmp_path)
    (tmp_path / "feedback").mkdir()
    (tmp_path / "feedback" / "bad.json").write_text("[1, 2]")

    with pytest.raises(ValueError, match="not a JSON object"):
        store.read_json("feedback/bad.json")
    with pytest.raises(ValueError, match="not a JSON object"):
        store.list_json("feedback")


class _Recorder:
    def __init__(self) -> None:
        self.logged: list[Mapping[str, str]] = []
        self.errors: list[BaseException] = []

    def log(self, line: Mapping[str, str]) -> None:
        self.logged.append(line)

    def on_error(self, exc: BaseException) -> None:
        self.errors.append(exc)


_LINES = [{"capture": "k", "payload": "{}"}, {"capture": "k", "payload": "[]"}]


def test_write_quarantine_writes_and_logs_nothing(tmp_path: Path) -> None:
    rec = _Recorder()
    blobs = BlobStore(tmp_path)
    assert write_quarantine(
        blobs, "ops/q/a.json", {"rows": []}, log_lines=_LINES, log=rec.log, on_error=rec.on_error
    )
    assert blobs.read_json("ops/q/a.json") == {"rows": []}
    assert rec.logged == [] and rec.errors == []


def test_write_quarantine_falls_back_to_one_log_line_per_row(tmp_path: Path) -> None:
    """A lake write error is survived: the cause is reported once, then every
    row's line is logged -- the only copy of those rows now."""
    (tmp_path / "ops").write_text("a file where the directory should be")
    rec = _Recorder()
    written = write_quarantine(
        BlobStore(tmp_path),
        "ops/q/a.json",
        {"rows": []},
        log_lines=_LINES,
        log=rec.log,
        on_error=rec.on_error,
    )
    assert written is False
    assert len(rec.errors) == 1 and isinstance(rec.errors[0], OSError)
    assert rec.logged == _LINES


def test_write_quarantine_lets_any_other_error_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = BlobStore(tmp_path)

    def broken(_key: str, _obj: Mapping[str, object]) -> None:
        raise TypeError("not JSON serialisable")

    monkeypatch.setattr(blobs, "write_json", broken)
    rec = _Recorder()
    with pytest.raises(TypeError):
        write_quarantine(blobs, "k", {}, log_lines=_LINES, log=rec.log, on_error=rec.on_error)
    assert rec.logged == []


def test_write_quarantine_reports_the_cause_before_the_rows(tmp_path: Path) -> None:
    """The cause comes first, once, so whoever reads the log sees WHY before
    the rows it explains."""
    (tmp_path / "ops").write_text("a file where the directory should be")
    calls: list[str] = []
    write_quarantine(
        BlobStore(tmp_path),
        "ops/q/a.json",
        {"rows": []},
        log_lines=_LINES,
        log=lambda line: calls.append(f"log:{line['payload']}"),
        on_error=lambda exc: calls.append(f"error:{type(exc).__name__}"),
    )
    assert len(calls) == 3
    assert calls[0].startswith("error:")
    assert calls[1:] == ["log:{}", "log:[]"]
