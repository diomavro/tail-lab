"""The verifier must degrade on a dead network and still let the lake decide.

`scripts/chain_sweep_verify.sh` is the dead-man's switch for the one dataset
nobody sells retroactively. On 2026-09-22, its first scheduled run, a DNS
failure moments after resume raised out of the unguarded Cboe fetch: the
backward lake check never ran, the script exited 1, and the alert told the
operator a healthy day was permanently blank. The fix (note it, carry on, let
check B decide) was only ever verified by hand fault injection.

The harness sandboxes `TAIL_LAB_REPO`, stubs the DNS probe and the clock's hour
(so the forward check is armed), and fronts `.venv/bin/python` with a wrapper
that makes every Cboe and Nasdaq fetch raise, then runs the real script body
against a real local lake.
"""

from __future__ import annotations

import datetime as dt
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pandas as pd

from tail_lab.contracts.option_chain import (
    CHIP_COHORT,
    CORE_SNAPSHOT_SYMBOLS,
    DEFAULT_SNAPSHOT_SYMBOLS,
)
from tail_lab.lake.store import DeltaLakeStore

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chain_sweep_verify.sh"

_PRELUDE = textwrap.dedent(
    """
    import sys

    import requests

    import tail_lab.ingestion.ohlcv as ohlcv
    import tail_lab.ingestion.option_chain as chain


    def _dead(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Temporary failure in name resolution")


    chain.fetch_chain_raw = _dead
    ohlcv.fetch_nasdaq_raw = _dead
    exec(compile(sys.stdin.read(), "chain_sweep_verify", "exec"))
    """
)


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _sandbox(repo: Path) -> dict[str, str]:
    bin_dir = repo / "stubs"
    _executable(bin_dir / "getent", "#!/bin/sh\nexit 0\n")
    # Hour 08 UTC keeps the forward check armed whatever time CI runs at.
    _executable(
        bin_dir / "date",
        '#!/bin/sh\ncase "$*" in *%H*) echo 08;; *) exec /bin/date "$@";; esac\n',
    )
    (repo / "prelude.py").write_text(_PRELUDE)
    _executable(
        repo / ".venv" / "bin" / "python",
        f'#!/bin/sh\nexec {sys.executable} "{repo}/prelude.py"\n',
    )
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(repo),
        "TAIL_LAB_REPO": str(repo),
        "TAIL_LAB_LAKE_BACKEND": "local",
        "TAIL_LAB_LAKE_ROOT": str(repo / "data"),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }


def _run(repo: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [str(SCRIPT)], env=_sandbox(repo), check=False, capture_output=True, timeout=120
    )
    log = (repo / ".chain-sweep-verify.log").read_text()
    return proc.returncode, log


def test_an_unreachable_cboe_and_nasdaq_on_an_empty_lake_exits_zero(tmp_path: Path) -> None:
    """Not being able to look is not evidence of a gap."""
    code, log = _run(tmp_path)
    assert code == 0, log
    assert "could not reach Cboe for the forward check (ConnectionError)" in log
    assert "trading days unavailable (ConnectionError)" in log
    assert "OK: every checked session is complete" in log


def test_an_unreachable_cboe_still_lets_the_lake_check_find_a_gap(tmp_path: Path) -> None:
    """The regression itself: a dead forward fetch must not skip check B."""
    today = dt.date.today()
    store = DeltaLakeStore(tmp_path / "data")
    only_spy = pd.DataFrame(
        {"underlying": ["spy"], "quote_date": [pd.Timestamp(today)], "strike": [1.0]}
    )
    store.write_bronze("option_chain_snapshot", today, only_spy)
    code, log = _run(tmp_path)
    assert code == 1, log
    assert "could not reach Cboe" in log
    assert f"GAP {today}" in log
    assert "FAIL:" in log


def _partition(store: DeltaLakeStore, day: dt.date, names: list[str]) -> None:
    store.write_bronze(
        "option_chain_snapshot",
        day,
        pd.DataFrame(
            {
                "underlying": names,
                "quote_date": [pd.Timestamp(day)] * len(names),
                "strike": [1.0] * len(names),
            }
        ),
    )


def test_partitions_before_the_chip_cohort_are_not_gaps(tmp_path: Path) -> None:
    """Widening the sweep must not make every earlier partition report 14
    permanent, unfixable gaps (bronze is immutable)."""
    store = DeltaLakeStore(tmp_path / "data")
    today = dt.date.today()
    before, after = today - dt.timedelta(days=2), today - dt.timedelta(days=1)
    _partition(store, before, list(CORE_SNAPSHOT_SYMBOLS))
    _partition(store, after, list(DEFAULT_SNAPSHOT_SYMBOLS))
    code, log = _run(tmp_path)
    assert code == 0, log
    assert f"chip cohort expected from {after}" in log
    assert f"OK  {before}: 24/24" in log
    assert f"OK  {after}: 38/38" in log


def test_a_first_cohort_session_missing_two_names_reports_them(tmp_path: Path) -> None:
    """12 of 14 on day one starts the cohort -- it is not skipped as
    "not yet collected", which would hide the two losses forever."""
    store = DeltaLakeStore(tmp_path / "data")
    today = dt.date.today() - dt.timedelta(days=1)
    lost = list(CHIP_COHORT[-2:])
    _partition(store, today, [s for s in DEFAULT_SNAPSHOT_SYMBOLS if s not in lost])
    code, log = _run(tmp_path)
    assert code == 1, log
    assert f"{today} is missing: {', '.join(sorted(lost))}" in log


def test_a_cohort_name_dropping_out_later_is_a_gap(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path / "data")
    today = dt.date.today()
    first, later = today - dt.timedelta(days=2), today - dt.timedelta(days=1)
    _partition(store, first, list(DEFAULT_SNAPSHOT_SYMBOLS))
    _partition(store, later, [s for s in DEFAULT_SNAPSHOT_SYMBOLS if s != "anet"])
    code, log = _run(tmp_path)
    assert code == 1, log
    assert f"{later} is missing: anet" in log


def test_no_cohort_yet_is_a_note_not_a_failure(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path / "data")
    _partition(store, dt.date.today() - dt.timedelta(days=1), list(CORE_SNAPSHOT_SYMBOLS))
    code, log = _run(tmp_path)
    assert code == 0, log
    assert "chip cohort not yet collected" in log
