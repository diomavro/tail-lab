"""The daily refresh must never report success on a run that did nothing.

`scripts/local_daily_refresh.sh` is, while GitHub Actions is blocked on
billing, one of only two things keeping the lake current. Both of its guards
are one token away from turning a dead run into a green one:

* if the OHLCV symbol probe comes back empty the loop body never executes, so
  without a guard the script refreshes no OHLCV at all and still exits 0;
* if the FRED probe CRASHES rather than reporting "no key", a two-state check
  cannot tell that from "unconfigured" and silently skips two sources forever.

Both are the silent-success class this repo spends most of its guards on, and
neither was exercised by anything until these tests existed.

The script is driven through a sandboxed `TAIL_LAB_REPO` holding a stub
Makefile and a stub `.venv/bin/python`, so nothing here touches the network,
the real lake, or systemd.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_daily_refresh.sh"

#: Answers both probes the script makes. `symbols` and `fred` decide what the
#: stub reports; "crash" makes it exit non-zero the way a broken venv would.
_STUB_PYTHON = """#!/usr/bin/env bash
src="$*"
case "$src" in
  *DEFAULT_SNAPSHOT_SYMBOLS*) [ "$STUB_SYMBOLS" = "crash" ] && exit 3; echo "$STUB_SYMBOLS" ;;
  *fred_api_key*)             [ "$STUB_FRED" = "crash" ] && exit 3; echo "$STUB_FRED" ;;
  *) exit 0 ;;
esac
"""

#: Every `make ingest-*` target succeeds, so only the guards can fail a run.
_STUB_MAKEFILE = "ingest-%:\n\t@true\n"


def _sandbox(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    py = repo / ".venv" / "bin" / "python"
    py.write_text(_STUB_PYTHON)
    py.chmod(0o755)
    (repo / "Makefile").write_text(_STUB_MAKEFILE)
    return repo


def _run(repo: Path, *, symbols: str, fred: str) -> tuple[int, str]:
    result = subprocess.run(
        [str(SCRIPT)],
        env={
            **os.environ,
            "TAIL_LAB_REPO": str(repo),
            "STUB_SYMBOLS": symbols,
            "STUB_FRED": fred,
            "REFRESH_OHLCV_SYMBOLS": "",
        },
        check=False,
        capture_output=True,
        timeout=300,
    )
    log = repo / ".daily-refresh.log"
    return result.returncode, (log.read_text() if log.exists() else "")


def test_an_empty_symbol_list_fails_instead_of_reporting_success(tmp_path: Path) -> None:
    """The loop body simply never runs, so without the guard this is a green
    build that refreshed nothing."""
    rc, log = _run(_sandbox(tmp_path), symbols="", fred="NOKEY")
    assert rc == 1
    assert "ohlcv:symbol-list" in log
    assert "all sources refreshed" not in log


def test_a_crashing_symbol_probe_also_fails(tmp_path: Path) -> None:
    rc, log = _run(_sandbox(tmp_path), symbols="crash", fred="NOKEY")
    assert rc == 1
    assert "ohlcv:symbol-list" in log


def test_a_crashing_fred_probe_is_not_mistaken_for_an_absent_key(tmp_path: Path) -> None:
    """A two-state check would read the crash as "no key" and skip rates and
    credit forever without ever failing. The probe is tri-state for this."""
    rc, log = _run(_sandbox(tmp_path), symbols="spy qqq", fred="crash")
    assert rc == 1
    assert "fred-probe" in log


def test_an_absent_key_is_skipped_not_failed(tmp_path: Path) -> None:
    """An unconfigured feed must not make the daily alert cry wolf."""
    rc, log = _run(_sandbox(tmp_path), symbols="spy qqq", fred="NOKEY")
    assert rc == 0
    assert "skipped (unconfigured): rates credit" in log
    assert "all sources refreshed" in log


def test_the_happy_path_exits_zero(tmp_path: Path) -> None:
    rc, log = _run(_sandbox(tmp_path), symbols="spy qqq", fred="KEY")
    assert rc == 0
    assert "all sources refreshed" in log


def test_it_never_invokes_the_option_chain_sweep(tmp_path: Path) -> None:
    """This script runs at a morning hour. An option-chain write here used to
    claim the session's partition and silently no-op that evening's real
    sweep; the settle-time guard now refuses it, but the refresh must still
    never invoke the chain sweep."""
    repo = _sandbox(tmp_path)
    # Make any chain target fail loudly rather than succeed silently.
    (repo / "Makefile").write_text(
        "ingest-option-chain:\n\t@echo CHAIN_INVOKED; false\n" + _STUB_MAKEFILE
    )
    rc, log = _run(repo, symbols="spy qqq", fred="NOKEY")
    assert "CHAIN_INVOKED" not in log
    assert rc == 0
