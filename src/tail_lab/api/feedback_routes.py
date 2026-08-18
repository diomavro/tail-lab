"""HTTP routes for the in-app feedback window (``docs/adr/0014``).

Two-tier lifecycle, mirrored from ``tail_lab.feedback.store``:

- ``POST /api/feedback`` — public, unauthenticated (like the dashboard's
  read routes). Dio writes a note from the dashboard; no login exists in
  this app to gate it behind, and the write is cheap to moderate by hand
  since only Dio ever calls it in practice.
- ``GET /api/feedback`` — token-gated (Bearer), fail-closed. This is what
  the daily agent (``docs/AGENT_MISSION.md``) pulls at the start of every
  run: open ``big_picture`` directives (``standing``, never auto-resolved)
  and open ``issue``s it can act on and then resolve.
- ``POST /api/feedback/{id}/resolve`` — same token gate. The agent resolves
  an ``issue`` it fixed; the UI may also call it later so Dio can retire a
  standing directive himself (not built here — see the ADR).

Token check mirrors quizkit's ``admin_feedback_routes.py``: unset token ->
404 (don't advertise the route exists on an unconfigured deploy), wrong/
missing bearer -> 401, constant-time compare. The token is read fresh from
settings on every request (not cached at import time) and never logged.
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from fastapi import APIRouter, Depends, Header, HTTPException

from tail_lab.api.schemas import FeedbackCreateRequest, FeedbackListResponse
from tail_lab.config import get_feedback_store as _get_configured_feedback_store
from tail_lab.config import get_settings
from tail_lab.feedback.store import FeedbackRecord, FeedbackStore

router = APIRouter()


@lru_cache(maxsize=1)
def get_feedback_store() -> FeedbackStore:
    return _get_configured_feedback_store()


def _require_token(authorization: str | None) -> None:
    configured_token = get_settings().feedback_token
    if not configured_token:
        raise HTTPException(status_code=404, detail="not found")
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[len("Bearer ") :].strip()
    if not presented or not secrets.compare_digest(presented, configured_token):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/api/feedback", status_code=201)
def create_feedback(
    body: FeedbackCreateRequest,
    store: FeedbackStore = Depends(get_feedback_store),
) -> FeedbackRecord:
    return store.add(body.text, body.kind)


@router.get("/api/feedback")
def list_feedback(
    authorization: str | None = Header(default=None),
    store: FeedbackStore = Depends(get_feedback_store),
) -> FeedbackListResponse:
    _require_token(authorization)
    open_records = store.list_open()
    return FeedbackListResponse(
        standing=[r for r in open_records if r.kind == "big_picture"],
        issues=[r for r in open_records if r.kind == "issue"],
    )


@router.post("/api/feedback/{feedback_id}/resolve")
def resolve_feedback(
    feedback_id: str,
    authorization: str | None = Header(default=None),
    store: FeedbackStore = Depends(get_feedback_store),
) -> FeedbackRecord:
    _require_token(authorization)
    try:
        return store.resolve(feedback_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
