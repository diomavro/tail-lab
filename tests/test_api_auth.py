from __future__ import annotations

import pytest
from fastapi import HTTPException

from tail_lab.api.auth import require_bearer_token
from tail_lab.config import Settings

TOKEN = "s3cret-token"


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tail_lab.api.auth.get_settings", lambda: Settings(feedback_token=TOKEN))


def test_a_correct_bearer_is_accepted() -> None:
    require_bearer_token(f"Bearer {TOKEN}")


def test_the_scheme_is_matched_case_insensitively_but_the_token_is_not() -> None:
    """RFC 7235 makes the scheme case-insensitive; the secret is not."""
    require_bearer_token(f"bearer {TOKEN}")
    require_bearer_token(f"BEARER {TOKEN}")
    with pytest.raises(HTTPException) as exc:
        require_bearer_token(f"Bearer {TOKEN.upper()}")
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer ", "Basic " + TOKEN, TOKEN, f"Bearer {TOKEN}x", "Bearer wrong"],
)
def test_anything_that_is_not_the_token_is_401(header: str | None) -> None:
    with pytest.raises(HTTPException) as exc:
        require_bearer_token(header)
    assert exc.value.status_code == 401


def test_an_unconfigured_deploy_returns_404_not_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deliberate, and the reason the check comes before any header parsing:
    a deploy with no token configured does not advertise that the route exists
    at all. A 401 would confirm it does."""
    monkeypatch.setattr("tail_lab.api.auth.get_settings", lambda: Settings(feedback_token=None))
    with pytest.raises(HTTPException) as exc:
        require_bearer_token(f"Bearer {TOKEN}")
    assert exc.value.status_code == 404


def test_the_token_is_read_fresh_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings are not captured at import, so rotating the token takes effect
    without a restart — and a stale cached token would be a live auth bug that
    no other test here would catch."""
    require_bearer_token(f"Bearer {TOKEN}")
    monkeypatch.setattr(
        "tail_lab.api.auth.get_settings", lambda: Settings(feedback_token="rotated")
    )
    with pytest.raises(HTTPException):
        require_bearer_token(f"Bearer {TOKEN}")
    require_bearer_token("Bearer rotated")


def test_all_three_gated_route_modules_share_this_one_implementation() -> None:
    """The finding this module exists to close: three byte-equivalent copies of
    an auth gate, each exercised only by its own module's tests, so a policy
    change could silently miss one. If a module ever grows its own copy again,
    this fails."""
    from tail_lab.api import feedback_routes, ingest_routes, putlab_memory_routes

    for module in (feedback_routes, ingest_routes, putlab_memory_routes):
        assert module._require_token is require_bearer_token
