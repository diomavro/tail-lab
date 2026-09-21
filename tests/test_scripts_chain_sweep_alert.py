"""The failure alert must never hand a human the wrong remedy.

`scripts/chain_sweep_alert.sh` is the only thing that tells anyone a
scheduled job died, and the two jobs it serves have OPPOSITE recovery
properties. The chain sweep is unrecoverable, so its remedy is "re-run now".
The daily refresh is fully recoverable, and running the chain sweep because
of a refresh failure -- after the 13:30 UTC open -- claims the live session's
partition with mid-session quotes and makes that evening's real sweep a
silent no-op. One missed night becomes two lost sessions.

That is not hypothetical: the refresh's `OnFailure=` was wired at the chain
handler, so a refresh failure wrote "this trading day is permanently blank --
run make ingest-option-chain". A later fix suppressed the command but left
the panic paragraph, which is the half a reader acts on.

These tests exist because both were one-line defects in a shell script that
nothing in the repo exercised.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chain_sweep_alert.sh"
#: The command that destroys a session if run at the wrong hour.
FORBIDDEN = "ingest-option-chain"
#: The sentence that makes a reader run it from memory.
PANIC = "unrecoverable if missed"


def _run(context: str, repo: Path) -> str:
    subprocess.run(
        [str(SCRIPT), context] if context else [str(SCRIPT)],
        env={"PATH": "/usr/bin:/bin", "HOME": str(repo), "TAIL_LAB_REPO": str(repo)},
        check=False,
        capture_output=True,
        timeout=60,
    )
    markers = list(repo.glob(".*FAILED"))
    assert markers, f"context={context!r} wrote no marker at all"
    return markers[0].read_text()


@pytest.mark.parametrize("context", ["chain"])
def test_only_the_chain_context_prints_the_chain_remedy(context: str, tmp_path: Path) -> None:
    body = _run(context, tmp_path)
    assert FORBIDDEN in body
    assert PANIC in body


@pytest.mark.parametrize("context", ["refresh", "", "refesh", "Chain", "garbage"])
def test_no_other_context_mentions_the_chain_remedy_or_its_panic(
    context: str, tmp_path: Path
) -> None:
    """Every non-chain context, including a TYPO'd one. A typo is the realistic
    way this fires -- `tail-lab-alert@refesh.service` is one keystroke away and
    systemd will happily start it."""
    body = _run(context, tmp_path)
    assert FORBIDDEN not in body, f"context={context!r} handed the reader the chain remedy"
    assert PANIC not in body, f"context={context!r} printed the chain's panic paragraph"


def test_an_unknown_context_reports_the_value_it_was_given(tmp_path: Path) -> None:
    """The offending value is the ONLY diagnostic that branch carries. An
    earlier version re-exec'd with "unknown" prepended, shifting the real
    value out of $1 and losing it."""
    assert "refesh" in _run("refesh", tmp_path)


def test_the_refresh_context_says_nothing_is_lost(tmp_path: Path) -> None:
    body = _run("refresh", tmp_path)
    assert "serve history on demand" in body
    assert "Do not run the chain sweep" in body
