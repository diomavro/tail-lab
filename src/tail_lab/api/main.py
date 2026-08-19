"""The FastAPI app: one health check, one gold-metric endpoint.

The one dashboard tile (frontend/) fetches ``GET /api/vix/stretch``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from tail_lab.api.feedback_routes import router as feedback_router
from tail_lab.api.leaderboard_routes import router as leaderboard_router
from tail_lab.api.putlab_memory_routes import router as putlab_memory_router
from tail_lab.api.putlab_routes import router as putlab_router
from tail_lab.api.schemas import HealthResponse, VixStretchResponse
from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.config import get_settings
from tail_lab.lake.store import LakeStore
from tail_lab.observability import configure_logging
from tail_lab.research.vix_stretch import compute_vix_stretch

# Composition root: configure structured logging once for the whole app
# (docs/STANDARDS.md §f) before anything starts emitting.
configure_logging()

app = FastAPI(title="tail-lab API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # POST for the feedback window's public write endpoint
    # (api/feedback_routes.py); everything else the dashboard calls is GET.
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(feedback_router)
app.include_router(leaderboard_router)
app.include_router(putlab_memory_router)
app.include_router(putlab_router)


@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Defense-in-depth response headers on every response (API + SPA)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@lru_cache(maxsize=1)
def get_lake_store() -> LakeStore:
    return _get_configured_lake_store()


@app.get("/api/health")
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/api/vix/stretch")
def vix_stretch(
    as_of: dt.date | None = Query(default=None, description="Simulation date; defaults to today."),
    store: LakeStore = Depends(get_lake_store),
) -> VixStretchResponse:
    # UTC, not local: the as-of clock must agree with the ingest clock
    # (ingestion stamps snapshots in UTC) or point-in-time reads mismatch.
    resolved_as_of = as_of or dt.datetime.now(dt.UTC).date()
    try:
        result = compute_vix_stretch(store, as_of=resolved_as_of)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return VixStretchResponse(**result.model_dump())


# Serve the built React SPA directly (single-container Fly deploy, matching
# the sibling tip_app pattern) when frontend/dist exists. Absent in tests
# and local `make api` runs against an unbuilt frontend — routes above are
# registered first so /api/* always takes precedence.
_dist_dir = get_settings().static_dir or (Path(__file__).resolve().parents[3] / "frontend" / "dist")
if _dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=_dist_dir, html=True), name="spa")
