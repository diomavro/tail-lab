"""The weekly Tiingo refresh: skip when unconfigured, fail loudly otherwise,
and hold its own lock without deadlocking on it.

Why each case matters:

* a missing key must SKIP (exit 0) -- it is a HUMAN_TODO item, and failing
  would make the alert cry wolf every week;
* a probe that crashes must FAIL -- otherwise a broken venv is
  indistinguishable from "unconfigured" and the feed silently stops;
* a failed ingest must FAIL with the re-run remedy, so OnFailure= fires;
* a second run while the lock is held must give up within the wait and never
  reach the ingest (two writers double a partition: write_bronze has no CAS).

Driven through a sandboxed ``TAIL_LAB_REPO`` with a stub ``.venv/bin/python``,
so nothing touches the network, the lake or systemd.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "weekly_tiingo_refresh.sh"

#: Answers the key probe from STUB_KEY ("crash" exits non-zero) and the ingest
#: from STUB_INGEST (its exit code), leaving a marker when the ingest ran.
_STUB_PYTHON = """#!/usr/bin/env bash
src="$*"
case "$src" in
  *tiingo_api_key\\ else*) [ "$STUB_KEY" = "crash" ] && exit 3; echo "$STUB_KEY" ;;
  *ingest_tiingo_eod*) touch "$TAIL_LAB_REPO/ingest-ran"; echo "committed 1 rows"; exit "$STUB_INGEST" ;;
  *) exit 0 ;;
esac
"""


def _sandbox(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    py = repo / ".venv" / "bin" / "python"
    py.write_text(_STUB_PYTHON)
    py.chmod(0o755)
    return repo


def _run(repo: Path, *, key: str, ingest: int = 0, lock_wait: str = "300") -> tuple[int, str]:
    result = subprocess.run(
        [str(SCRIPT)],
        env={
            **os.environ,
            "TAIL_LAB_REPO": str(repo),
            "TAIL_LAB_LOCK_WAIT": lock_wait,
            "STUB_KEY": key,
            "STUB_INGEST": str(ingest),
        },
        check=False,
        capture_output=True,
        timeout=60,
    )
    log = repo / ".tiingo-refresh.log"
    return result.returncode, (log.read_text() if log.exists() else "")


def test_no_key_skips_without_failing(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    code, log = _run(repo, key="NOKEY")
    assert code == 0
    assert "SKIPPED (no TIINGO_API_KEY" in log
    assert not (repo / "ingest-ran").exists()


def test_a_crashed_probe_fails_rather_than_skipping(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    code, log = _run(repo, key="crash")
    assert code == 1
    assert "config probe itself did not run" in log
    assert not (repo / "ingest-ran").exists()


def test_a_successful_ingest_exits_zero(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    code, log = _run(repo, key="KEY")
    assert code == 0
    assert (repo / "ingest-ran").exists()
    assert "committed 1 rows" in log


def test_a_failed_ingest_fails_with_the_rerun_remedy(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    code, log = _run(repo, key="KEY", ingest=1)
    assert code == 1
    assert "./scripts/weekly_tiingo_refresh.sh" in log
    assert "serves history on demand" in log


def test_a_held_lock_gives_up_without_running_the_ingest(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    with (repo / ".tiingo-refresh.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        code, log = _run(repo, key="KEY", lock_wait="1")
    assert code == 1
    assert "still held after 1s" in log
    assert not (repo / "ingest-ran").exists()


def test_it_never_shares_the_daily_refresh_lock(tmp_path: Path) -> None:
    # Holding the DAILY lock must not block the weekly run: its ~95-minute run
    # would otherwise time out the daily refresh's 300 s wait and fire its alert.
    repo = _sandbox(tmp_path)
    with (repo / ".daily-refresh.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        code, _ = _run(repo, key="KEY", lock_wait="1")
    assert code == 0
    assert (repo / "ingest-ran").exists()


def test_a_briefly_held_lock_is_waited_out_not_refused(tmp_path: Path) -> None:
    # -w, not -n: a human re-run overlapping the timer resolves by waiting.
    import threading
    import time

    repo = _sandbox(tmp_path)
    held = (repo / ".tiingo-refresh.lock").open("w")
    fcntl.flock(held, fcntl.LOCK_EX)
    threading.Thread(target=lambda: (time.sleep(1.0), held.close()), daemon=True).start()
    code, _ = _run(repo, key="KEY", lock_wait="10")
    assert code == 0
    assert (repo / "ingest-ran").exists()


def test_an_orphaned_child_of_the_ingest_does_not_hold_the_lock(tmp_path: Path) -> None:
    # The ingest runs with 9>&-: were the lock fd inherited, a child left
    # running after the script exits would pin the lock and every later run
    # would report "still held" until a human killed it.
    repo = _sandbox(tmp_path)
    py = repo / ".venv" / "bin" / "python"
    py.write_text(
        _STUB_PYTHON.replace(
            '*ingest_tiingo_eod*) touch "$TAIL_LAB_REPO/ingest-ran";',
            '*ingest_tiingo_eod*) (sleep 30 &) ; touch "$TAIL_LAB_REPO/ingest-ran";',
        )
    )
    first, _ = _run(repo, key="KEY")
    second, log = _run(repo, key="KEY", lock_wait="1")
    assert (first, second) == (0, 0), log


def test_make_runs_the_checkout_it_is_invoked_from(tmp_path: Path) -> None:
    # `make ingest-tiingo-eod` must run the script against the checkout it is
    # invoked from (its .venv, .env and log) -- not the main checkout, the
    # script's default. (With a symlinked .venv the editable install still
    # resolves to main's src; that is true of every make target.)
    import shutil

    repo = _sandbox(tmp_path)
    root = Path(__file__).resolve().parents[1]
    shutil.copy(root / "Makefile", repo / "Makefile")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "weekly_tiingo_refresh.sh").symlink_to(SCRIPT)
    result = subprocess.run(
        ["make", "-s", "ingest-tiingo-eod"],
        cwd=repo,
        # A path that cannot exist, never "": the script treats empty as unset,
        # so a Makefile that dropped TAIL_LAB_REPO=$(CURDIR) would otherwise run
        # against the real main checkout (its key, its production lake).
        env={
            **os.environ,
            "STUB_KEY": "KEY",
            "STUB_INGEST": "0",
            "TAIL_LAB_REPO": str(tmp_path / "nowhere"),
        },
        check=False,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert (repo / "ingest-ran").exists()
    assert (repo / ".tiingo-refresh.log").exists()


def test_a_big_log_is_rolled_by_the_run_that_holds_the_lock(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    (repo / ".tiingo-refresh.log").write_text("x" * 2_000_001)
    code, log = _run(repo, key="KEY")
    assert code == 0
    assert (repo / ".tiingo-refresh.log.1").stat().st_size == 2_000_001
    assert len(log) < 10_000


def test_a_run_waiting_on_the_lock_never_rolls_the_log(tmp_path: Path) -> None:
    # The roll happens after the lock: a second run must not rename the log out
    # from under the run in flight.
    repo = _sandbox(tmp_path)
    (repo / ".tiingo-refresh.log").write_text("x" * 2_000_001)
    with (repo / ".tiingo-refresh.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        code, _ = _run(repo, key="KEY", lock_wait="1")
    assert code == 1
    assert not (repo / ".tiingo-refresh.log.1").exists()


def test_a_crashed_probe_leaves_its_traceback_in_the_log(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    py = repo / ".venv" / "bin" / "python"
    py.write_text(
        _STUB_PYTHON.replace(
            '[ "$STUB_KEY" = "crash" ] && exit 3;',
            '[ "$STUB_KEY" = "crash" ] && { echo "AttributeError: no tiingo_api_key" >&2; exit 3; };',
        )
    )
    code, log = _run(repo, key="crash")
    assert code == 1
    assert "AttributeError: no tiingo_api_key" in log
