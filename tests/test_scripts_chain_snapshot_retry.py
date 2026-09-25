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

import datetime as dt
import importlib.util
import io
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


@pytest.fixture(autouse=True)
def _no_market_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main`` now asks Nasdaq for the latest session before every sweep;
    tests never touch the network, so the witness is stubbed out here."""
    monkeypatch.setattr(chain_snapshot, "latest_market_session", lambda: None)


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


@pytest.mark.parametrize(
    ("code", "expected_calls"),
    [
        # A 4xx means the app understood the body and refused it. Re-sending the
        # SAME bytes cannot change that answer, and each attempt burns part of
        # the window in which Cboe still serves this session.
        (422, 1),
        (401, 1),
        # A 5xx means it failed to handle a request it might handle next time.
        (500, chain_snapshot.POST_ATTEMPTS),
    ],
)
def test_only_faults_a_retry_could_fix_are_retried(
    monkeypatch: pytest.MonkeyPatch, code: int, expected_calls: int
) -> None:
    """Asserted as a CONTRAST, deliberately.

    Testing "a 422 is sent once" on its own passes just as happily when there
    is no retry loop at all, so it cannot detect the regression it is named
    for. Pinning 1-vs-4 against the same call counter is what makes the loop's
    presence observable.
    """
    calls: list[int] = []

    def fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append(1)
        raise _http_error(code)

    monkeypatch.setattr(chain_snapshot, "_post_once", fake)
    with pytest.raises(urllib.error.HTTPError):
        chain_snapshot._post("http://x", "t", {"rows": []}, 30)
    assert len(calls) == expected_calls


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


def test_a_wedged_app_reports_a_failure_rather_than_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`TimeoutError` is a sibling of `URLError` under `OSError`, not a
    subclass, so it escaped both of main()'s handlers and surfaced as a raw
    traceback. The exit code was still non-zero -- no session was lost -- but
    the whole premise of this workflow is that a red run is legible at a
    glance, and a traceback in the Actions log is not."""

    def fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise TimeoutError

    monkeypatch.setattr(chain_snapshot, "_post_once", fake)
    monkeypatch.setattr(
        chain_snapshot, "sweep_to_records", lambda _s: [{"underlying": "SPY"}] * 20_000
    )

    rc = chain_snapshot.main(["--post", "http://x", "--token", "t", "--symbols", "spy"])

    assert rc == 1
    assert "::error::" in capsys.readouterr().err


def test_the_post_carries_the_floor_and_the_witness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``symbols`` the app cannot apply the symbol floor to chains
    that never reached it; without ``market_session`` it cannot see a frozen
    feed. Both have to cross the wire or the server-side checks are inert."""
    sent: list[dict[str, Any]] = []

    def fake(_url: str, _token: str, payload: dict[str, Any], _timeout: int) -> dict[str, Any]:
        sent.append(payload)
        return {
            "rows": 1,
            "symbols": 1,
            "quote_date": "2026-09-22",
            "bronze_path": "p",
            "committed": True,
        }

    monkeypatch.setattr(chain_snapshot, "_post_once", fake)
    monkeypatch.setattr(chain_snapshot, "latest_market_session", lambda: dt.date(2026, 9, 23))
    monkeypatch.setattr(
        chain_snapshot, "sweep_to_records", lambda _s: [{"underlying": "SPY"}] * 20_000
    )

    assert chain_snapshot.main(["--post", "http://x", "--token", "t", "--symbols", "spy,qqq"]) == 0
    assert sent[0]["symbols"] == ["spy", "qqq"]
    assert sent[0]["market_session"] == "2026-09-23"


def test_a_no_op_is_reported_as_one(capsys: pytest.CaptureFixture[str]) -> None:
    rc = chain_snapshot._report_post(
        {
            "rows": 20078,
            "symbols": 24,
            "quote_date": "2026-09-22",
            "bronze_path": "p",
            "committed": False,
        }
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "NO-OP" in out
    assert "committed 20078" not in out


def test_a_too_early_answer_is_a_quiet_skip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """425 means the session is still trading and nothing is lost."""

    def fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise urllib.error.HTTPError("http://x", 425, "Too Early", {}, io.BytesIO(b"still trading"))  # type: ignore[arg-type]

    monkeypatch.setattr(chain_snapshot, "_post_once", fake)
    monkeypatch.setattr(
        chain_snapshot, "sweep_to_records", lambda _s: [{"underlying": "SPY"}] * 20_000
    )

    assert chain_snapshot.main(["--post", "http://x", "--token", "t", "--symbols", "spy"]) == 0
    assert "SKIPPED" in capsys.readouterr().out


def test_an_unreadable_witness_is_announced_not_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When Nasdaq cannot be read the frozen-feed check is off for the run,
    and this warning is the ONLY trace of that (adversarial review: removing
    it broke nothing)."""
    monkeypatch.setattr(chain_snapshot, "latest_market_session", lambda: None)

    assert chain_snapshot._market_session() is None
    assert "frozen Cboe feed would go undetected" in capsys.readouterr().out
