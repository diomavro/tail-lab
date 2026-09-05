"""The chain sweep must not abandon a session on a transient fault.

This dataset is the one that cannot be repaired: every other source here
serves history on demand, but nobody sells a retroactive option chain, so an
attempt given up on costs that session permanently (`docs/adr/0020`).

On 2026-09-04 the scheduled sweep fetched all 24 chains -- 20,113 quotes --
and the app answered HTTP 500. The identical body, re-posted by hand, was
accepted with no change to either side. One immediate retry would have saved
it; instead a human had to notice a red run.
"""

from __future__ import annotations

import importlib.util
import urllib.error
from pathlib import Path
from typing import Any

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "chain_snapshot", Path(__file__).resolve().parents[1] / "scripts" / "chain_snapshot.py"
)
assert _SPEC and _SPEC.loader
chain_snapshot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chain_snapshot)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "boom", {}, None)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backoff is real in production and pointless in a test."""
    monkeypatch.setattr(chain_snapshot.time, "sleep", lambda _s: None)


def test_a_transient_500_is_retried_and_the_session_is_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact 2026-09-04 failure: one 500, then success."""
    calls: list[int] = []

    def fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(500)
        return {"rows": 20113}

    monkeypatch.setattr(chain_snapshot, "_post_once", fake)
    assert chain_snapshot._post("http://x", "t", {"rows": []}, 30) == {"rows": 20113}
    assert len(calls) == 2


def test_a_422_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4xx means the app understood the body and refused it. Re-sending the
    SAME body cannot change that answer, and each attempt burns part of the
    window in which Cboe still serves this session."""
    calls: list[int] = []

    def fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append(1)
        raise _http_error(422)

    monkeypatch.setattr(chain_snapshot, "_post_once", fake)
    with pytest.raises(urllib.error.HTTPError):
        chain_snapshot._post("http://x", "t", {"rows": []}, 30)
    assert len(calls) == 1, "a rejected body was re-sent"


def test_429_is_retried_even_though_it_is_a_4xx() -> None:
    """The one 4xx that explicitly means 'later'."""
    assert chain_snapshot._retryable(_http_error(429)) is True
    assert chain_snapshot._retryable(_http_error(401)) is False
    assert chain_snapshot._retryable(_http_error(503)) is True
    assert chain_snapshot._retryable(urllib.error.URLError("dns")) is True


def test_it_gives_up_rather_than_retrying_forever(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bounded: the catch-up cron is the next line of defence, and a job that
    never exits would block the concurrency group it shares with that cron."""
    calls: list[int] = []

    def fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append(1)
        raise _http_error(500)

    monkeypatch.setattr(chain_snapshot, "_post_once", fake)
    with pytest.raises(urllib.error.HTTPError):
        chain_snapshot._post("http://x", "t", {"rows": []}, 30)
    assert len(calls) == chain_snapshot.POST_ATTEMPTS
