"""The single bearer-token gate for every token-protected route.

There were three of these — one each in ``feedback_routes``,
``putlab_memory_routes`` and ``ingest_routes`` — byte-for-byte equivalent
apart from a local variable name. Nothing was broken by that, and that is
rather the point: an authentication policy copied three times is one that
changes in three places, and the failure mode is silent. Tightening the
comparison, adding a second token, moving 404 to 403, or logging a refusal
would each have had to find all three, and missing one leaves a route on the
old policy with no test able to notice, because each copy is exercised only by
its own module's tests.

Found by a retrospective adversarial review on 2026-09-02, over the ~40 agent
PRs that merged before the reviewer worked (`docs/adr/0023`, `0024`).

The policy itself, unchanged from all three originals:

- **Token unset -> 404.** An unconfigured deploy does not advertise that the
  route exists at all. This is deliberate and is why the check comes before
  any parsing of the header.
- **Missing or wrong bearer -> 401.**
- **Constant-time comparison**, so a wrong token cannot be narrowed by timing.
- **The token is never logged**, and settings are read fresh per request rather
  than captured at import, so rotating it does not need a restart.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException

from tail_lab.config import get_settings

__all__ = ["require_bearer_token"]

_BEARER = "bearer "


def require_bearer_token(authorization: str | None) -> None:
    """Raise unless ``authorization`` carries the configured feedback token.

    Called directly rather than as a FastAPI ``Depends`` — which is why tests
    patch ``tail_lab.api.auth.get_settings`` rather than overriding a
    dependency.
    """
    configured = get_settings().feedback_token
    if not configured:
        raise HTTPException(status_code=404, detail="not found")
    presented = ""
    if authorization and authorization.lower().startswith(_BEARER):
        presented = authorization[len(_BEARER) :].strip()
    if not presented or not secrets.compare_digest(presented, configured):
        raise HTTPException(status_code=401, detail="unauthorized")
