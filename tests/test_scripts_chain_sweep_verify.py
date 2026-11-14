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
    CALL_DATASET,
    CHIP_COHORT,
    CHIP_COHORT_SINCE,
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
    {overrides}
    exec(compile(sys.stdin.read(), "chain_sweep_verify", "exec"))
    """
)


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _sandbox(repo: Path, overrides: str = "") -> dict[str, str]:
    bin_dir = repo / "stubs"
    _executable(bin_dir / "getent", "#!/bin/sh\nexit 0\n")
    # Hour 08 UTC keeps the forward check armed whatever time CI runs at.
    _executable(
        bin_dir / "date",
        '#!/bin/sh\ncase "$*" in *%H*) echo 08;; *) exec /bin/date "$@";; esac\n',
    )
    (repo / "prelude.py").write_text(_PRELUDE.replace("{overrides}", overrides))
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


def _run(repo: Path, overrides: str = "") -> tuple[int, str]:
    """Run the real script; ``overrides`` is one prelude line that revives a
    stubbed source (the network stays dead otherwise)."""
    proc = subprocess.run(
        [str(SCRIPT)], env=_sandbox(repo, overrides), check=False, capture_output=True, timeout=120
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


def _partition(
    store: DeltaLakeStore,
    day: dt.date,
    names: list[str],
    dataset: str = "option_chain_snapshot",
) -> None:
    store.write_bronze(
        dataset,
        day,
        pd.DataFrame(
            {
                "underlying": names,
                "quote_date": [pd.Timestamp(day)] * len(names),
                "strike": [1.0] * len(names),
            }
        ),
    )


# Fixed dates around the pin, not today-relative ones: the expected set is a
# function of the session date now. The walk counts partitions, not days, so
# dates in the past are still inspected; the network checks are stubbed dead.
# (The call-wing tests below write partitions up to a week after the pin,
# which are in the past now; expected_symbols' later-session rule is pinned in
# tests/test_contracts_option_chain.py.)
PIN = CHIP_COHORT_SINCE
BEFORE = PIN - dt.timedelta(days=1)


def test_partitions_before_the_chip_cohort_are_not_gaps(tmp_path: Path) -> None:
    """Widening the sweep must not make every earlier partition report 14
    permanent, unfixable gaps (bronze is immutable)."""
    store = DeltaLakeStore(tmp_path / "data")
    _partition(store, BEFORE, list(CORE_SNAPSHOT_SYMBOLS))
    _partition(store, PIN, list(DEFAULT_SNAPSHOT_SYMBOLS))
    code, log = _run(tmp_path)
    assert code == 0, log
    assert f"chip cohort expected from {PIN}" in log
    assert f"OK  {BEFORE}: 24/24" in log
    assert f"OK  {PIN}: 38/38" in log


def test_a_first_cohort_session_missing_two_names_reports_them(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path / "data")
    lost = list(CHIP_COHORT[-2:])
    _partition(store, PIN, [s for s in DEFAULT_SNAPSHOT_SYMBOLS if s not in lost])
    code, log = _run(tmp_path)
    assert code == 1, log
    assert f"{PIN} is missing: {', '.join(sorted(lost))}" in log


def test_a_session_with_no_cohort_at_all_after_the_pin_is_a_gap(tmp_path: Path) -> None:
    """The case the old derived start passed as "not yet collected": every
    cohort name gone from a partition the cohort was due in."""
    store = DeltaLakeStore(tmp_path / "data")
    _partition(store, PIN, list(CORE_SNAPSHOT_SYMBOLS))
    code, log = _run(tmp_path)
    assert code == 1, log
    assert f"{PIN} is missing: {', '.join(sorted(CHIP_COHORT))}" in log


# ---- the call wing (docs/adr/0031) -----------------------------------------
# A session can land its puts and lose its calls; the verifier read the puts
# alone, so that loss passed as "every checked session is complete".
CALLS = CALL_DATASET
AFTER = PIN + dt.timedelta(days=1)


def _nasdaq_trading_days(days: list[dt.date]) -> str:
    """A prelude override reviving check C: Nasdaq answers with ``days``."""
    return (
        "import datetime; ohlcv.fetch_nasdaq_raw = lambda *a, **k: None; "
        "ohlcv.nasdaq_trading_days = lambda raw: "
        f"{{datetime.date.fromisoformat(d) for d in {[d.isoformat() for d in days]!r}}}"
    )


def test_a_call_wing_missing_names_fails_while_the_puts_pass(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path / "data")
    lost = sorted(DEFAULT_SNAPSHOT_SYMBOLS)[:2]
    _partition(store, PIN, list(DEFAULT_SNAPSHOT_SYMBOLS))
    _partition(store, PIN, [s for s in DEFAULT_SNAPSHOT_SYMBOLS if s not in lost], CALLS)
    code, log = _run(tmp_path)
    assert code == 1, log
    assert f"OK  {PIN}: 38/38" in log
    assert f"GAP calls: {PIN}: 36/38" in log
    assert f"FAIL: calls: {PIN} is missing: {', '.join(lost)}" in log
    assert f"FAIL: {PIN} is missing" not in log  # the puts are not blamed


def test_a_call_gap_is_silenced_only_by_a_calls_acknowledgement(tmp_path: Path) -> None:
    """An unlabelled line acknowledges the PUT gap; the same symbol's call gap
    needs its own `calls:` line, or a put acknowledgement would hide it."""
    store = DeltaLakeStore(tmp_path / "data")
    lost = sorted(DEFAULT_SNAPSHOT_SYMBOLS)[0]
    full = list(DEFAULT_SNAPSHOT_SYMBOLS)
    _partition(store, PIN, full)
    _partition(store, PIN, [s for s in full if s != lost], CALLS)
    _partition(store, AFTER, full)  # newer sessions, so PIN is acknowledgeable
    _partition(store, AFTER, full, CALLS)
    known = tmp_path / ".chain-known-gaps"
    known.write_text(f"{PIN}\t{lost}\n")
    code, log = _run(tmp_path)
    assert code == 1, log
    assert f"FAIL: calls: {PIN} is missing: {lost}" in log
    known.write_text(f"{PIN}\tcalls:{lost.upper()}\n")
    code, log = _run(tmp_path)
    assert code == 0, log
    assert f"ack calls: {PIN}: 37/38  (1 acknowledged)" in log


def test_a_whole_call_session_lost_is_a_gap_and_acks_with_calls_star(tmp_path: Path) -> None:
    """Check C per wing: the puts have the session, the calls have none."""
    store = DeltaLakeStore(tmp_path / "data")
    full = list(DEFAULT_SNAPSHOT_SYMBOLS)
    middle = PIN + dt.timedelta(days=3)  # a Monday between two Fridays' partitions
    late = PIN + dt.timedelta(days=7)
    for day in (PIN, middle, late):
        _partition(store, day, full)
    for day in (PIN, late):
        _partition(store, day, full, CALLS)
    days = [PIN, middle, late, late + dt.timedelta(days=1)]
    revive = _nasdaq_trading_days(days)
    code, log = _run(tmp_path, revive)
    assert code == 1, log
    assert f"GAP calls: {middle}: no partition at all" in log
    assert f"GAP {middle}" not in log  # the puts have it
    assert f"'{middle}<TAB>calls:*'" in log
    (tmp_path / ".chain-known-gaps").write_text(f"{middle}\t*\n")
    code, log = _run(tmp_path, revive)
    assert code == 1, log  # a put acknowledgement does not cover the calls
    (tmp_path / ".chain-known-gaps").write_text(f"{middle}\tcalls:*\n")
    code, log = _run(tmp_path, revive)
    assert code == 0, log


#: Revives the forward check: Cboe "serves" AFTER.
CBOE_SERVES_AFTER = (
    "import pandas; chain.fetch_chain_raw = lambda symbol: None; "
    "chain.parse_cboe_chain = lambda raw: "
    f"(pandas.DataFrame({{'quote_date': [pandas.Timestamp('{AFTER}')]}}), None)"
)


def test_the_forward_check_fails_when_cboes_session_has_no_calls(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path / "data")
    full = list(DEFAULT_SNAPSHOT_SYMBOLS)
    _partition(store, PIN, full)
    _partition(store, AFTER, full)
    _partition(store, PIN, full, CALLS)
    code, log = _run(tmp_path, CBOE_SERVES_AFTER)
    assert code == 1, log
    assert f"\nsymbols at {AFTER}: 38/38" in log
    assert (
        f"FAIL: calls: newest captured session is {PIN} but Cboe is serving {AFTER}"
        f" -- {AFTER} was NOT captured"
    ) in log


def test_the_forward_check_reports_both_wings_when_both_are_stale(tmp_path: Path) -> None:
    """A put failure must not stop the calls being judged, and a stale wing is
    not also compared, symbol by symbol, against its older partition."""
    store = DeltaLakeStore(tmp_path / "data")
    full = list(DEFAULT_SNAPSHOT_SYMBOLS)
    _partition(store, PIN, full)
    _partition(store, PIN, full, CALLS)
    code, log = _run(tmp_path, CBOE_SERVES_AFTER)
    assert code == 1, log
    stale = f"newest captured session is {PIN} but Cboe is serving {AFTER}"
    assert f"FAIL: {stale}" in log
    assert f"FAIL: calls: {stale}" in log
    assert f"no {AFTER} quotes for" not in log


def test_the_newest_call_session_cannot_be_acknowledged(tmp_path: Path) -> None:
    """Per wing: the calls' newest session may still be re-capturable even when
    the puts have a newer one, so a `calls:` line must not silence it."""
    store = DeltaLakeStore(tmp_path / "data")
    full = list(DEFAULT_SNAPSHOT_SYMBOLS)
    lost = sorted(full)[0]
    _partition(store, PIN, full)
    _partition(store, AFTER, full)
    _partition(store, PIN, [s for s in full if s != lost], CALLS)
    (tmp_path / ".chain-known-gaps").write_text(f"{PIN}\tcalls:{lost}\n")
    code, log = _run(tmp_path)
    assert code == 1, log
    assert f"FAIL: calls: {PIN} is missing: {lost}" in log


def test_a_stray_whole_session_label_acknowledges_nothing(tmp_path: Path) -> None:
    """`:*` or `put:*` is a typo, not some wing's acknowledgement, and must not
    crash the switch either (degrade, don't die)."""
    store = DeltaLakeStore(tmp_path / "data")
    full = list(DEFAULT_SNAPSHOT_SYMBOLS)
    middle = PIN + dt.timedelta(days=3)
    late = PIN + dt.timedelta(days=7)
    for day in (PIN, late):
        _partition(store, day, full)
        _partition(store, day, full, CALLS)
    days = [PIN, middle, late, late + dt.timedelta(days=1)]
    revive = _nasdaq_trading_days(days)
    (tmp_path / ".chain-known-gaps").write_text(f"{middle}\t:*\n{middle}\tput:*\n")
    code, log = _run(tmp_path, revive)
    assert code == 1, log
    assert "Traceback" not in log
    assert f"GAP {middle}: no partition at all" in log
    assert f"GAP calls: {middle}: no partition at all" in log


def test_the_forward_check_names_calls_missing_from_cboes_session(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path / "data")
    full = list(DEFAULT_SNAPSHOT_SYMBOLS)
    lost = sorted(full)[0]
    _partition(store, AFTER, full)
    _partition(store, AFTER, [s for s in full if s != lost], CALLS)
    code, log = _run(tmp_path, CBOE_SERVES_AFTER)
    assert code == 1, log
    assert f"\nsymbols at {AFTER}: 38/38" in log
    assert f"calls: symbols at {AFTER}: 37/38" in log
    assert f"FAIL: calls: no {AFTER} quotes for: {lost}" in log


def test_the_forward_check_judges_the_calls_when_the_puts_have_no_partition(
    tmp_path: Path,
) -> None:
    store = DeltaLakeStore(tmp_path / "data")
    _partition(store, PIN, list(DEFAULT_SNAPSHOT_SYMBOLS), CALLS)
    code, log = _run(tmp_path, CBOE_SERVES_AFTER)
    assert code == 1, log
    assert f"FAIL: bronze has NO partition at or before {AFTER}" in log
    assert f"FAIL: calls: newest captured session is {PIN} but Cboe is serving {AFTER}" in log
