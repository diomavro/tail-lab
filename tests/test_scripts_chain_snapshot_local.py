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


class _Result:
    """Enough of ``IngestResult`` for the local path to report on."""

    valid_rows = 19_525
    symbols_ok = ("spy", "qqq")
    symbols_failed: tuple[str, ...] = ()
    bronze_path = "s3://tail-lab-lake/bronze/option_chain_snapshot/ingest_date=2026-09-18"


def test_local_does_not_also_run_the_post_paths_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression itself: one fetch per chain, not two."""
    swept = 0
    ingested = 0

    def fake_sweep(symbols: Any, **_kw: Any) -> list[dict[str, Any]]:
        nonlocal swept
        swept += 1
        return [{"underlying": "SPY"}] * 20_000

    def fake_ingest(_store: Any, _symbols: Any) -> _Result:
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
        "tail_lab.ingestion.option_chain.ingest_option_chain", lambda _s, _y: _Short()
    )
    assert chain_snapshot.main(["--local", "--symbols", "spy"]) == 1
