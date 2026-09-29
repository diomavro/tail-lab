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
