"""``--local`` must fetch each chain once, not twice.

``main`` used to call ``sweep_to_records`` unconditionally and THEN, for
``--local``, call ``ingest_option_chain`` -- which does its own fetch. Every
chain was pulled twice, 48 requests where 24 were needed, back to back.

Invisible on a GitHub runner, immediate on a workstation. Measured 2026-09-19
driving the local path by hand, Cboe returned **429 on 9 of 24 symbols**: the
first sweep logged 24/24 and the second 15/24, and the run still exited 0
having committed 15 chains. Because bronze is immutable that short partition
is the one that stands for the session, and the nine missing chains are as
unrecoverable as the whole day would have been -- the reasoning already
written against ``_FETCH_ATTEMPTS`` in the adapter, and the reason
``MIN_PLAUSIBLE_ROWS`` does not save you here: 15 of 24 clears any
wholesale-failure floor.

After the fix the same command swept 24/24 with no 429 at all.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
from pathlib import Path
from typing import Any

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "chain_snapshot_local", Path(__file__).resolve().parents[1] / "scripts" / "chain_snapshot.py"
)
assert _SPEC is not None and _SPEC.loader is not None
chain_snapshot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chain_snapshot)


@pytest.fixture(autouse=True)
def _no_market_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main`` now asks Nasdaq for the latest session before every sweep;
    tests never touch the network, so the witness is stubbed out here."""
    monkeypatch.setattr(chain_snapshot, "latest_market_session", lambda: None)


class _Result:
    """Enough of ``IngestResult`` for the local path to report on.

    Kept in step with the real dataclass by
    ``test_the_stub_still_matches_the_real_ingest_result`` below — a stub that
    silently lags its subject turns an interface change into a green test.
    """

    valid_rows = 19_525
    symbols_ok = ("spy", "qqq")
    symbols_failed: tuple[str, ...] = ()
    symbols_off_session: tuple[str, ...] = ()
    committed = True
    quote_date = dt.date(2026, 9, 18)
    bronze_path = "s3://tail-lab-lake/bronze/option_chain_snapshot/ingest_date=2026-09-18"


def test_the_stub_still_matches_the_real_ingest_result() -> None:
    """``_Result`` is a hand-rolled double, so it can silently fall behind the
    dataclass it stands in for — and then this file goes green while the real
    caller raises ``AttributeError`` in production. That is not hypothetical:
    adding ``committed`` and ``symbols_off_session`` to ``IngestResult`` broke the
    local path here exactly that way.

    Scope, honestly: ``consumed`` is a hand-maintained literal, so this does
    NOT detect ``_run_local`` growing a new field read — the caller tests
    catch that, by raising ``AttributeError``. What this uniquely catches is
    the opposite drift: a stub that invents a field ``IngestResult`` does not
    have, which would make the caller tests pass against an interface that
    does not exist.
    """
    from tail_lab.ingestion.option_chain import IngestResult

    real = {f.name for f in dataclasses.fields(IngestResult)}
    stubbed = {n for n in vars(_Result) if not n.startswith("_")}
    assert stubbed <= real, f"stub invents fields IngestResult lacks: {sorted(stubbed - real)}"
    # The fields `_run_local` reads. If it grows another, add it here AND to
    # the stub, rather than discovering it from a traceback.
    consumed = {
        "valid_rows",
        "symbols_ok",
        "symbols_failed",
        "symbols_off_session",
        "committed",
        "quote_date",
        "bronze_path",
    }
    assert consumed <= real
    assert consumed <= stubbed


def test_local_does_not_also_run_the_post_paths_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression itself: one fetch per chain, not two."""
    swept = 0
    ingested = 0

    def fake_sweep(symbols: Any, **_kw: Any) -> list[dict[str, Any]]:
        nonlocal swept
        swept += 1
        return [{"underlying": "SPY"}] * 20_000

    def fake_ingest(_store: Any, _symbols: Any, **_kw: Any) -> _Result:
        nonlocal ingested
        ingested += 1
        return _Result()

    monkeypatch.setattr(chain_snapshot, "sweep_to_records", fake_sweep)
    monkeypatch.setattr("tail_lab.config.get_lake_store", lambda: object())
    monkeypatch.setattr("tail_lab.ingestion.option_chain.ingest_option_chain", fake_ingest)

    assert chain_snapshot.main(["--local", "--symbols", "spy,qqq"]) == 0
    assert ingested == 1
    assert swept == 0, "--local must not run the --post path's sweep as well"


def test_local_fails_on_an_implausibly_short_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The row floor has to move with the code: it used to guard the pre-sweep
    that ``--local`` no longer runs, so it now guards the ingest result."""

    class _Short(_Result):
        valid_rows = 3

    monkeypatch.setattr("tail_lab.config.get_lake_store", lambda: object())
    monkeypatch.setattr(
        "tail_lab.ingestion.option_chain.ingest_option_chain", lambda _s, _y, **_kw: _Short()
    )
    assert chain_snapshot.main(["--local", "--symbols", "spy"]) == 1


def test_local_exits_non_zero_when_the_sweep_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``IncompleteSweepError`` must reach the shell as a non-zero exit.

    That exit code is the ONLY thing that makes
    ``OnFailure=tail-lab-alert@chain.service`` fire, which is in turn the only
    thing that tells anyone the session was refused. Refusing to write is the
    correct outcome, but a refusal nobody hears is indistinguishable from a
    successful capture — and the chain cannot be back-filled after the next US
    open.

    Mutation-checked: flipping ``chain_snapshot.py``'s refusal branch from
    ``return 1`` to ``return 0`` left the whole file green before this test
    existed.
    """
    from tail_lab.ingestion.option_chain import IncompleteSweepError

    def refuse(_store: Any, _symbols: Any, **_kw: Any) -> _Result:
        raise IncompleteSweepError("only 15 of 24 chains returned quotes")

    monkeypatch.setattr("tail_lab.config.get_lake_store", lambda: object())
    monkeypatch.setattr("tail_lab.ingestion.option_chain.ingest_option_chain", refuse)
    assert chain_snapshot.main(["--local", "--symbols", "spy"]) == 1


def test_local_reports_a_no_op_rather_than_claiming_rows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A no-op write must not print the in-memory row count as though it had
    been committed — the misreport that hid two dead sweeps on 2026-09-19."""

    class _NoOp(_Result):
        committed = False

    monkeypatch.setattr("tail_lab.config.get_lake_store", lambda: object())
    monkeypatch.setattr(
        "tail_lab.ingestion.option_chain.ingest_option_chain", lambda _s, _y, **_kw: _NoOp()
    )
    assert chain_snapshot.main(["--local", "--symbols", "spy"]) == 0
    out = capsys.readouterr().out
    assert "NO-OP" in out
    assert "committed 19525 rows" not in out


def test_local_hands_the_market_witness_to_the_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The systemd stand-in runs THIS path. If it stops forwarding the
    witness, a frozen Cboe feed goes back to no-opping green on the local
    sweep while every other test here still passes."""
    seen: dict[str, Any] = {}

    def fake_ingest(_store: Any, _symbols: Any, **kw: Any) -> _Result:
        seen.update(kw)
        return _Result()

    monkeypatch.setattr("tail_lab.config.get_lake_store", lambda: object())
    monkeypatch.setattr("tail_lab.ingestion.option_chain.ingest_option_chain", fake_ingest)
    monkeypatch.setattr(chain_snapshot, "latest_market_session", lambda: dt.date(2026, 9, 23))

    assert chain_snapshot.main(["--local", "--symbols", "spy"]) == 0
    assert seen.get("market_session") == dt.date(2026, 9, 23)
