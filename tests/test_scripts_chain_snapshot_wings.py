"""The sweep collects both wings, and a call-side failure never hides or
costs the puts (``docs/adr/0031``).

The exit-code contract, per put outcome (calls have no "skipped"):

* puts unsettled (425 / ``SessionInProgress``) -> calls not attempted, exit 0;
* puts committed or no-op -> calls attempted, exit 0 only if they land too;
* puts hard failure -> calls STILL attempted (each session is unrecoverable),
  exit 1.

Every transport failure counts, not only the ones ``_post`` used to catch:
urllib wraps ``h.request()`` in ``URLError`` but not ``h.getresponse()``, so a
dropped connection or a non-JSON 200 escaped as a raw traceback, which would
have skipped the call POST entirely.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import http.client
import importlib.util
import io
import urllib.error
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

_SPEC = importlib.util.spec_from_file_location(
    "chain_snapshot_wings", Path(__file__).resolve().parents[1] / "scripts" / "chain_snapshot.py"
)
assert _SPEC is not None and _SPEC.loader is not None
chain_snapshot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chain_snapshot)

PUTS = chain_snapshot.PUT_PATH
CALLS = chain_snapshot.CALL_PATH
ROWS: list[dict[str, object]] = [{"underlying": "SPY"}] * 20_000


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chain_snapshot, "latest_market_session", lambda: None)
    monkeypatch.setattr(chain_snapshot.time, "sleep", lambda _s: None)
    monkeypatch.setattr("tail_lab.ingestion.option_chain.fetch_chains", lambda _s, *_a: {})


def test_the_two_routes_are_distinct() -> None:
    assert (PUTS, CALLS) == ("/api/ingest/option-chain", "/api/ingest/option-chain-calls")


# ---- --post ------------------------------------------------------------------


def _answer(committed: bool = True, dataset: str = "option_chain_snapshot") -> dict[str, object]:
    return {
        "dataset": dataset,
        "ingest_date": "2026-10-26",
        "rows": 20_000,
        "symbols": 24,
        "quote_date": "2026-10-26",
        "bronze_path": f"s3://lake/bronze/{dataset}",
        "committed": committed,
    }


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "no", {}, io.BytesIO(b"detail"))  # type: ignore[arg-type]


Behaviour = Callable[[], object]


def _server(
    monkeypatch: pytest.MonkeyPatch,
    put: Behaviour,
    call: Behaviour,
    *,
    puts: list[dict[str, object]] = ROWS,
    calls: list[dict[str, object]] = ROWS,
) -> dict[str, list[Mapping[str, object]]]:
    """Stub the sweep and ``_post_once`` per route; return the bodies each
    route received (one entry per ATTEMPT)."""
    sent: dict[str, list[Mapping[str, object]]] = {PUTS: [], CALLS: []}

    def post_once(
        _url: str, path: str, _token: str, payload: Mapping[str, object], _timeout: int
    ) -> object:
        sent[path].append(payload)
        return (put if path == PUTS else call)()

    monkeypatch.setattr(chain_snapshot, "_post_once", post_once)
    monkeypatch.setattr(chain_snapshot, "sweep_to_records", lambda _s: (puts, calls))
    return sent


def _ok(committed: bool = True) -> Behaviour:
    return lambda: _answer(committed)


def _raise(exc: Exception) -> Behaviour:
    def behaviour() -> object:
        raise exc

    return behaviour


def _main() -> int:
    rc: int = chain_snapshot.main(["--post", "http://x", "--token", "t", "--symbols", "spy,qqq"])
    return rc


def test_puts_committed_and_calls_refused_is_red_and_says_the_puts_landed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sent = _server(monkeypatch, _ok(), _raise(_http_error(409)))
    assert _main() == 1
    assert (len(sent[PUTS]), len(sent[CALLS])) == (1, 1)
    captured = capsys.readouterr()
    assert "committed 20000 rows" in captured.out
    assert "calls only; puts landed" in captured.err


def test_puts_refused_still_sends_the_calls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each wing's session is unrecoverable on its own: a put refusal must not
    cost the calls theirs."""
    sent = _server(monkeypatch, _raise(_http_error(409)), _ok())
    assert _main() == 1
    assert (len(sent[PUTS]), len(sent[CALLS])) == (1, 1)
    assert "calls: committed" in capsys.readouterr().out


def test_a_put_no_op_still_lets_the_catch_up_fill_the_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 05:00 UTC catch-up re-POSTs puts the evening run already landed
    (no-op) and can still fill a call session the evening run lost."""
    sent = _server(monkeypatch, _ok(committed=False), _ok())
    assert _main() == 0
    assert (len(sent[PUTS]), len(sent[CALLS])) == (1, 1)


def test_unsettled_puts_send_no_calls_and_stay_green(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Calls come from the same payloads, so they are equally unsettled; the
    call route's own witness would turn a benign skip into a red 409."""
    sent = _server(monkeypatch, _raise(_http_error(425)), _ok())
    assert _main() == 0
    assert (len(sent[PUTS]), len(sent[CALLS])) == (1, 0)
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "calls: not attempted -- the session is still trading" in out


def test_unsettled_calls_after_settled_puts_are_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calls have no SKIPPED: a 425 after the puts landed means the wings'
    voter sets disagreed about the session, which a human must see."""
    _server(monkeypatch, _ok(), _raise(_http_error(425)))
    assert _main() == 1


def test_an_undeployed_call_route_is_named_as_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Between merge and deploy the SPA's StaticFiles mount answers a POST to
    the unknown path with 405: red, with the puts landed, and a hint."""
    _server(monkeypatch, _ok(), _raise(_http_error(405)))
    assert _main() == 1
    err = capsys.readouterr().err
    assert "call route not deployed yet (check deploy.yml)" in err
    assert "calls only; puts landed" in err


@pytest.mark.parametrize(
    ("put", "call"),
    [
        (_raise(_http_error(404)), _ok()),  # a put 404
        (_raise(_http_error(404)), _raise(_http_error(404))),  # unset app token: both 404
        (_ok(), _raise(_http_error(404))),  # a call 404 is never the SPA mount
        (_raise(_http_error(405)), _ok()),  # the put route is always deployed
    ],
)
def test_only_a_call_405_is_blamed_on_the_deploy(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    put: Behaviour,
    call: Behaviour,
) -> None:
    """``require_bearer_token`` answers 404 on BOTH routes when the app's token
    is unset (api/auth.py); only the SPA mount's 405 on the CALL route means
    "not deployed"."""
    _server(monkeypatch, put, call)
    assert _main() == 1
    assert "not deployed yet" not in capsys.readouterr().err


def test_puts_failed_and_calls_landed_never_says_the_puts_landed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The alert line is what tells a lost put session from a call-only loss
    (ADR 0031): it must never claim the puts landed when they did not."""
    _server(monkeypatch, _raise(_http_error(409)), _ok())
    assert _main() == 1
    err = capsys.readouterr().err
    assert "puts failed; calls landed" in err
    assert "puts landed" not in err.replace("puts failed; calls landed", "")


def test_neither_wing_landing_never_says_the_puts_landed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _server(monkeypatch, _raise(_http_error(409)), _raise(_http_error(409)))
    assert _main() == 1
    err = capsys.readouterr().err
    assert "neither wing landed" in err
    assert "puts landed" not in err
    assert "calls landed" not in err


def test_a_call_wing_under_the_floor_is_red_and_never_posted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sent = _server(monkeypatch, _ok(), _ok(), calls=[{"underlying": "SPY"}] * 3)
    assert _main() == 1
    assert (len(sent[PUTS]), len(sent[CALLS])) == (1, 0)
    err = capsys.readouterr().err
    assert "calls: only 3 rows" in err
    assert "calls only; puts landed" in err


def test_a_put_wing_under_the_floor_still_posts_the_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _server(monkeypatch, _ok(), _ok(), puts=[{"underlying": "SPY"}] * 3)
    assert _main() == 1
    assert (len(sent[PUTS]), len(sent[CALLS])) == (0, 1)


def test_a_dropped_put_connection_still_sends_the_calls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``RemoteDisconnected`` on every attempt: retried, then a FAILED put --
    and the call route still gets exactly one POST."""
    sent = _server(monkeypatch, _raise(http.client.RemoteDisconnected("gone")), _ok())
    assert _main() == 1
    assert (len(sent[PUTS]), len(sent[CALLS])) == (chain_snapshot.POST_ATTEMPTS, 1)
    assert "puts:" in capsys.readouterr().err


def test_a_dropped_call_connection_is_red_says_the_puts_landed_and_never_escapes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _server(monkeypatch, _ok(), _raise(http.client.RemoteDisconnected("gone")))
    assert _main() == 1  # returned, not raised
    assert "calls only; puts landed" in capsys.readouterr().err


def test_one_dropped_connection_is_retried_into_a_landing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    def flaky() -> object:
        attempts.append(1)
        if len(attempts) == 1:
            raise http.client.RemoteDisconnected("gone")
        return _answer()

    sent = _server(monkeypatch, flaky, _ok())
    assert _main() == 0
    assert (len(sent[PUTS]), len(sent[CALLS])) == (2, 1)


def test_a_reset_after_the_send_is_retried_and_flagged_as_a_drop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A peer RST while reading the answer raises a plain
    ``ConnectionResetError`` (errno 104) from ``getresponse``: not wrapped in
    ``URLError``, and not ``RemoteDisconnected``. It is a drop like the others:
    retried, and a no-op after it is reported as a likely landing."""
    attempts: list[int] = []

    def reset_then_no_op() -> object:
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionResetError(104, "Connection reset by peer")
        return _answer(committed=False)

    sent = _server(monkeypatch, reset_then_no_op, _ok())
    assert _main() == 0
    assert len(sent[PUTS]) == 2
    assert "likely committed it before the connection dropped" in capsys.readouterr().out


def test_a_plain_os_error_is_an_annotated_failure_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Any other ``OSError`` raw from ``getresponse`` (not retryable) must
    still map to FAILED so the call wing is attempted."""
    sent = _server(monkeypatch, _raise(BrokenPipeError(32, "Broken pipe")), _ok())
    assert _main() == 1
    assert (len(sent[PUTS]), len(sent[CALLS])) == (1, 1)
    assert "::error::puts: the POST to /api/ingest/option-chain failed" in capsys.readouterr().err


def test_an_incomplete_read_is_retried_too() -> None:
    assert chain_snapshot._retryable(http.client.IncompleteRead(b"")) is True
    assert chain_snapshot._retryable(http.client.RemoteDisconnected("x")) is True
    assert chain_snapshot._retryable(ConnectionResetError(104, "reset")) is True
    assert chain_snapshot._retryable(ValueError("not json")) is False


def test_a_no_op_after_a_drop_says_the_earlier_attempt_likely_landed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A drop AFTER the send may follow a commit, so the retry answers
    ``committed=False``. "wrote nothing" would then be a false claim."""
    attempts: list[int] = []

    def drop_then_no_op() -> object:
        attempts.append(1)
        if len(attempts) == 1:
            raise http.client.IncompleteRead(b"")
        return _answer(committed=False)

    _server(monkeypatch, drop_then_no_op, _ok())
    assert _main() == 0
    out = capsys.readouterr().out
    assert "an earlier attempt likely committed it before the connection dropped" in out
    assert "wrote nothing" not in out.split("calls:")[0]


def test_a_non_json_answer_is_a_failure_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``json.loads`` on an HTML 200 raises ``ValueError`` inside ``_post``."""
    sent = _server(monkeypatch, _raise(ValueError("Expecting value")), _ok())
    assert _main() == 1
    assert (len(sent[PUTS]), len(sent[CALLS])) == (1, 1)
    assert "::error::puts:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("exc", "annotation"),
    [
        (_http_error(422), "app rejected the sweep: HTTP 422"),
        (urllib.error.URLError("dns"), "could not reach http://x"),
        (TimeoutError(), "did not answer within"),
    ],
)
def test_the_named_failures_keep_their_own_annotations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exc: Exception,
    annotation: str,
) -> None:
    """The broad transport clause sits AFTER these: every one of them is an
    ``OSError``, so a broad clause first would swallow its annotation."""
    _server(monkeypatch, _raise(exc), _ok())
    assert _main() == 1
    assert annotation in capsys.readouterr().err


def test_an_unreadable_answer_keeps_its_annotation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``ValidationError`` is a ``ValueError``: the broad clause must not reach it."""
    assert issubclass(ValidationError, ValueError)
    _server(monkeypatch, lambda: {"rows": 1}, _ok())
    assert _main() == 1
    assert "not a snapshot result" in capsys.readouterr().err


def test_the_token_is_checked_before_any_sweep_or_floor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A short, tokenless sweep used to exit 1 on the floor; the operator's
    actual mistake is the missing token, which is exit 2."""
    swept: list[int] = []
    monkeypatch.setattr(chain_snapshot, "sweep_to_records", lambda _s: swept.append(1) or ([], []))
    assert chain_snapshot.main(["--post", "http://x", "--symbols", "spy"]) == 2
    assert swept == []
    assert "--post needs --token" in capsys.readouterr().err


def test_both_wings_carry_one_witness_read_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """One Nasdaq read per run: re-reading it for the calls could hand the two
    wings different witnesses across a session boundary."""
    reads: list[int] = []

    def witness() -> dt.date:
        reads.append(1)
        return dt.date(2026, 10, 26)

    monkeypatch.setattr(chain_snapshot, "latest_market_session", witness)
    sent = _server(monkeypatch, _ok(), _ok())
    assert _main() == 0
    assert reads == [1]
    (put_body,), (call_body,) = sent[PUTS], sent[CALLS]
    assert put_body["symbols"] == call_body["symbols"] == ["spy", "qqq"]
    assert put_body["market_session"] == call_body["market_session"] == "2026-10-26"


# ---- --local -----------------------------------------------------------------


@dataclasses.dataclass
class _Result:
    """Enough of ``IngestResult`` for ``_run_local``; kept honest by
    ``test_the_local_stub_invents_no_fields`` below."""

    valid_rows: int = 19_525
    symbols_ok: tuple[str, ...] = ("SPY", "QQQ")
    symbols_failed: tuple[str, ...] = ()
    symbols_off_session: tuple[str, ...] = ()
    committed: bool = True
    quote_date: dt.date = dt.date(2026, 10, 26)
    bronze_path: str = "s3://lake/bronze/x"


def test_the_local_stub_invents_no_fields() -> None:
    from tail_lab.ingestion.option_chain import IngestResult

    real = {f.name for f in dataclasses.fields(IngestResult)}
    assert {f.name for f in dataclasses.fields(_Result)} <= real


def _local(
    monkeypatch: pytest.MonkeyPatch, put: Callable[[], _Result], call: Callable[[], _Result]
) -> list[dict[str, object]]:
    """Stub the lake and the ingest per right; return each ingest's kwargs."""
    seen: list[dict[str, object]] = []

    def ingest(_store: object, _symbols: object, **kw: object) -> _Result:
        seen.append(kw)
        return (put if kw["right"] == "P" else call)()

    monkeypatch.setattr("tail_lab.config.get_lake_store", lambda: object())
    monkeypatch.setattr("tail_lab.ingestion.option_chain.ingest_option_chain", ingest)
    return seen


def _local_raise(exc: Exception) -> Callable[[], _Result]:
    def behaviour() -> _Result:
        raise exc

    return behaviour


def _run_local() -> int:
    rc: int = chain_snapshot.main(["--local", "--symbols", "spy,qqq"])
    return rc


def test_local_fetches_once_and_hands_the_same_payloads_to_both_wings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[list[str]] = []
    payloads = {"spy": {"data": {}}}

    def fetch_chains(symbols: list[str], *_a: object) -> dict[str, object]:
        fetched.append(list(symbols))
        return payloads

    monkeypatch.setattr("tail_lab.ingestion.option_chain.fetch_chains", fetch_chains)
    seen = _local(monkeypatch, _Result, _Result)
    assert _run_local() == 0
    assert fetched == [["spy", "qqq"]]
    assert [kw["right"] for kw in seen] == ["P", "C"]
    assert all(kw["payloads"] is payloads for kw in seen)
    assert seen[0]["now"] is seen[1]["now"]


def test_local_unsettled_puts_skip_the_calls_and_say_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tail_lab.contracts.option_chain import SessionInProgress

    seen = _local(monkeypatch, _local_raise(SessionInProgress("still trading")), _Result)
    assert _run_local() == 0
    assert [kw["right"] for kw in seen] == ["P"]
    assert "calls: not attempted -- the session is still trading" in capsys.readouterr().out


def test_local_refused_puts_still_land_the_calls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tail_lab.contracts.option_chain import IncompleteSweepError

    seen = _local(monkeypatch, _local_raise(IncompleteSweepError("15 of 24")), _Result)
    assert _run_local() == 1
    assert [kw["right"] for kw in seen] == ["P", "C"]
    captured = capsys.readouterr()
    assert "calls: swept" in captured.out
    assert "puts failed; calls landed" in captured.err
    assert "puts landed" not in captured.err.replace("puts failed; calls landed", "")


def test_local_neither_wing_landing_never_says_the_puts_landed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tail_lab.contracts.option_chain import IncompleteSweepError

    refused = _local_raise(IncompleteSweepError("15 of 24"))
    _local(monkeypatch, refused, refused)
    assert _run_local() == 1
    err = capsys.readouterr().err
    assert "neither wing landed" in err
    assert "puts landed" not in err


def test_local_a_put_lake_write_failure_still_lands_the_calls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``LAKE_WRITE_ERRORS`` used to escape ``_run_local`` as a traceback,
    which would now also skip the call ingest."""
    seen = _local(monkeypatch, _local_raise(OSError("disk full")), _Result)
    assert _run_local() == 1
    assert [kw["right"] for kw in seen] == ["P", "C"]
    assert "::error::puts:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure",
    ["refused", "lake", "floor", "unsettled"],
)
def test_local_a_call_failure_is_red_and_says_the_puts_landed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    from tail_lab.contracts.option_chain import IncompleteSweepError, SessionInProgress

    call: Callable[[], _Result] = {
        "refused": _local_raise(IncompleteSweepError("15 of 24")),
        "lake": _local_raise(OSError("disk full")),
        "floor": lambda: _Result(valid_rows=3),
        "unsettled": _local_raise(SessionInProgress("still trading")),
    }[failure]
    _local(monkeypatch, _Result, call)
    assert _run_local() == 1
    assert "calls only; puts landed" in capsys.readouterr().err
