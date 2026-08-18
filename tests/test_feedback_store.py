from __future__ import annotations

from pathlib import Path

import pytest

from tail_lab.feedback.store import FeedbackStore
from tail_lab.lake.blob_store import BlobStore


@pytest.fixture
def blobs(tmp_path: Path) -> BlobStore:
    return BlobStore(tmp_path)


@pytest.fixture
def store(blobs: BlobStore) -> FeedbackStore:
    return FeedbackStore(blobs)


def test_add_creates_open_record(store: FeedbackStore) -> None:
    record = store.add("please add FRED credit adapter", "issue")
    assert record.text == "please add FRED credit adapter"
    assert record.kind == "issue"
    assert record.status == "open"
    assert record.resolved_at is None


def test_list_open_returns_both_kinds(store: FeedbackStore) -> None:
    big = store.add("prioritize the sensitivity leaderboard", "big_picture")
    issue = store.add("VIX tile shows stale date", "issue")
    open_ids = {r.id for r in store.list_open()}
    assert open_ids == {big.id, issue.id}


def test_list_open_excludes_resolved(store: FeedbackStore) -> None:
    issue = store.add("bug", "issue")
    store.resolve(issue.id)
    assert store.list_open() == []


def test_resolve_marks_status_and_sets_resolved_at(store: FeedbackStore) -> None:
    issue = store.add("bug", "issue")
    resolved = store.resolve(issue.id)
    assert resolved.status == "resolved"
    assert resolved.resolved_at is not None


def test_resolve_is_idempotent(store: FeedbackStore) -> None:
    issue = store.add("bug", "issue")
    first = store.resolve(issue.id)
    second = store.resolve(issue.id)
    assert second.status == "resolved"
    assert second.resolved_at == first.resolved_at


def test_resolve_unknown_id_raises_lookup_error(store: FeedbackStore) -> None:
    with pytest.raises(LookupError):
        store.resolve("does-not-exist")


def test_resolved_record_is_never_lost_only_status_changes(
    store: FeedbackStore, blobs: BlobStore
) -> None:
    """Resolving rewrites the record in place -- it stays retrievable via
    list_json (history), just filtered out of list_open."""
    issue = store.add("bug", "issue")
    store.resolve(issue.id)
    raw_records = blobs.list_json("ops/feedback")
    assert len(raw_records) == 1
    assert raw_records[0]["id"] == issue.id
    assert raw_records[0]["status"] == "resolved"


def test_big_picture_is_never_auto_resolved_by_add_or_list(store: FeedbackStore) -> None:
    """Nothing but an explicit resolve() call ever changes a big_picture
    record's status -- add() and list_open() must leave it open."""
    big = store.add("always screen the broad universe", "big_picture")
    store.list_open()
    store.add("another issue", "issue")
    reloaded = [r for r in store.list_open() if r.id == big.id]
    assert len(reloaded) == 1
    assert reloaded[0].status == "open"
