from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tail_lab.api.feedback_routes import get_feedback_store
from tail_lab.api.main import app
from tail_lab.config import Settings
from tail_lab.feedback.store import FeedbackStore
from tail_lab.lake.blob_store import BlobStore


@pytest.fixture
def feedback_store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(BlobStore(tmp_path))


@pytest.fixture
def client(feedback_store: FeedbackStore) -> Iterator[TestClient]:
    get_feedback_store.cache_clear()
    app.dependency_overrides[get_feedback_store] = lambda: feedback_store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_feedback_store, None)


def _set_token(monkeypatch: pytest.MonkeyPatch, token: str | None) -> None:
    """``_require_token`` reads ``get_settings()`` directly (not a FastAPI
    ``Depends``, so ``app.dependency_overrides`` can't reach it) -- patch the
    name as imported into ``feedback_routes`` instead."""
    monkeypatch.setattr(
        "tail_lab.api.feedback_routes.get_settings",
        lambda: Settings(feedback_token=token),
    )


# ---- POST /api/feedback (public, no token needed) --------------------------


def test_create_feedback_issue_returns_201(client: TestClient) -> None:
    resp = client.post("/api/feedback", json={"text": "VIX tile shows stale date", "kind": "issue"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["text"] == "VIX tile shows stale date"
    assert body["kind"] == "issue"
    assert body["status"] == "open"
    assert body["resolved_at"] is None


def test_create_feedback_big_picture_returns_201(client: TestClient) -> None:
    resp = client.post(
        "/api/feedback",
        json={"text": "prioritize the sensitivity leaderboard", "kind": "big_picture"},
    )
    assert resp.status_code == 201
    assert resp.json()["kind"] == "big_picture"


def test_create_feedback_rejects_empty_text(client: TestClient) -> None:
    resp = client.post("/api/feedback", json={"text": "", "kind": "issue"})
    assert resp.status_code == 422


def test_create_feedback_rejects_oversized_text(client: TestClient) -> None:
    resp = client.post("/api/feedback", json={"text": "x" * 4001, "kind": "issue"})
    assert resp.status_code == 422


def test_create_feedback_rejects_bad_kind(client: TestClient) -> None:
    resp = client.post("/api/feedback", json={"text": "hello", "kind": "not_a_kind"})
    assert resp.status_code == 422


def test_create_feedback_persists_to_store(
    client: TestClient, feedback_store: FeedbackStore
) -> None:
    client.post("/api/feedback", json={"text": "a real note", "kind": "issue"})
    assert len(feedback_store.list_open()) == 1


# ---- GET /api/feedback (token-gated, fail-closed) ---------------------------


def test_list_feedback_404_when_token_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, None)
    resp = client.get("/api/feedback")
    assert resp.status_code == 404


def test_list_feedback_401_when_token_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, "secret-token")
    resp = client.get("/api/feedback")
    assert resp.status_code == 401


def test_list_feedback_401_when_token_wrong(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, "secret-token")
    resp = client.get("/api/feedback", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_list_feedback_200_when_token_correct_splits_standing_and_issues(
    client: TestClient, feedback_store: FeedbackStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    feedback_store.add("always screen the broad universe", "big_picture")
    feedback_store.add("VIX tile shows stale date", "issue")
    _set_token(monkeypatch, "secret-token")
    resp = client.get("/api/feedback", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["standing"]) == 1
    assert len(body["issues"]) == 1
    assert body["standing"][0]["kind"] == "big_picture"
    assert body["issues"][0]["kind"] == "issue"


# ---- POST /api/feedback/{id}/resolve (token-gated) --------------------------


def test_resolve_feedback_404_when_token_unset(
    client: TestClient, feedback_store: FeedbackStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = feedback_store.add("bug", "issue")
    _set_token(monkeypatch, None)
    resp = client.post(f"/api/feedback/{record.id}/resolve")
    assert resp.status_code == 404


def test_resolve_feedback_401_when_token_wrong(
    client: TestClient, feedback_store: FeedbackStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = feedback_store.add("bug", "issue")
    _set_token(monkeypatch, "secret-token")
    resp = client.post(
        f"/api/feedback/{record.id}/resolve", headers={"Authorization": "Bearer nope"}
    )
    assert resp.status_code == 401


def test_resolve_feedback_404_when_id_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_token(monkeypatch, "secret-token")
    resp = client.post(
        "/api/feedback/does-not-exist/resolve",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert resp.status_code == 404


def test_resolve_feedback_marks_resolved_and_drops_from_open_list(
    client: TestClient, feedback_store: FeedbackStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = feedback_store.add("bug", "issue")
    _set_token(monkeypatch, "secret-token")
    resp = client.post(
        f"/api/feedback/{record.id}/resolve",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    assert feedback_store.list_open() == []


def test_resolve_feedback_idempotent(
    client: TestClient, feedback_store: FeedbackStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = feedback_store.add("bug", "issue")
    _set_token(monkeypatch, "secret-token")
    headers = {"Authorization": "Bearer secret-token"}
    first = client.post(f"/api/feedback/{record.id}/resolve", headers=headers)
    second = client.post(f"/api/feedback/{record.id}/resolve", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["resolved_at"] == first.json()["resolved_at"]
