"""The per-name page names screens with a map in ``signalWording.ts`` that
duplicates ``walk_forward._LABELS`` (the labels the API puts on the screens).
The duplicate is kept -- the name record carries screen ids, not labels -- so
this test parses the TypeScript map and fails the moment the two drift."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

from tail_lab.research.backtest import beyond_vol, signal_test, walk_forward

ROOT = Path(__file__).resolve().parents[1]
WORDING = ROOT / "frontend/src/components/putlab/signalWording.ts"
CLIENT = ROOT / "frontend/src/api/client.ts"
PREREGISTERED = ROOT / "frontend/src/components/putlab/preregistered.ts"
REAL_RESULT = ROOT / "docs/research/0035/REAL_RESULT.json"


def _frontend_screen_names() -> dict[str, str]:
    src = WORDING.read_text()
    block = re.search(r"const SCREEN_NAME: Record<string, string> = \{(.*?)\n\}", src, re.S)
    assert block, "SCREEN_NAME map not found in signalWording.ts"
    entries = re.findall(r"^\s*([a-z_0-9]+):\s*'([^']*)',?\s*$", block.group(1), re.M)
    assert entries, "parsed no entries: the parity check would pass vacuously"
    return dict(entries)


def test_the_frontend_screen_names_equal_the_backends_labels() -> None:
    assert _frontend_screen_names() == walk_forward._LABELS


def test_the_parser_sees_every_screen() -> None:
    assert set(_frontend_screen_names()) == set(walk_forward.SCREENS)


# ---- The beyond-vol lists the page switches on (ADR 0035) ----


def _ts_union(name: str) -> set[str]:
    """The string members of ``export type <name> = 'a' | 'b'`` in client.ts."""
    m = re.search(rf"export type {name} =([^\n]*(?:\n\s*\|[^\n]*)*)", CLIENT.read_text())
    assert m, f"type {name} not found in client.ts"
    members = re.findall(r"'([a-z_0-9]+)'", m.group(1))
    assert members, f"{name} parsed no members: the parity check would pass vacuously"
    return set(members)


def test_the_frontend_beyond_vol_status_and_demotion_lists_equal_the_backends() -> None:
    assert _ts_union("BeyondVolStatus") == set(get_args(beyond_vol.Status))
    assert _ts_union("BeyondVolDemotion") == set(get_args(beyond_vol.Demotion))
    assert _ts_union("BeyondVolMode") == set(get_args(signal_test.BeyondVolMode))


def test_the_wording_handles_exactly_the_backends_statuses_and_demotions() -> None:
    """The type unions are compile-checked against the switch, but the wording
    file is parsed too, so a case the page forgot (or invented) fails here."""
    src = WORDING.read_text()
    switch = re.search(r"switch \(bv\.status\) \{(.*?)\n  \}", src, re.S)
    assert switch, "switch (bv.status) not found in signalWording.ts"
    assert set(re.findall(r"case '([a-z_]+)':", switch.group(1))) == set(
        get_args(beyond_vol.Status)
    )
    assert "default:" in switch.group(1), "the status switch needs a safe default"
    table = re.search(r"const DEMOTION_TEXT[^{]*\{(.*?)\n\}", src, re.S)
    assert table, "DEMOTION_TEXT not found in signalWording.ts"
    assert set(re.findall(r"^\s*([a-z_]+):", table.group(1), re.M)) == set(
        get_args(beyond_vol.Demotion)
    )


def test_the_quoted_eligibility_rule_is_the_backends() -> None:
    src = WORDING.read_text()
    consts = dict(re.findall(r"^const (N_MIN|MIN_PERIODS|MIN_EPISODES) = (\d+)$", src, re.M))
    assert {k: int(v) for k, v in consts.items()} == {
        "N_MIN": beyond_vol.N_MIN,
        "MIN_PERIODS": walk_forward.MIN_PERIODS,
        "MIN_EPISODES": walk_forward.MIN_EPISODES,
    }


def _decimals(literal: str) -> int:
    return len(literal.split(".")[1]) if "." in literal else 0


def test_the_quoted_preregistered_result_is_the_committed_one() -> None:
    """The monitoring line quotes the pre-registered figures from one frontend
    constant. It must be the rounded committed result, so the page can never
    quote numbers that no file in the repo backs."""
    ts = PREREGISTERED.read_text()
    fields = dict(re.findall(r"^\s*([a-z0-9_]+): ('?[-\w.]+'?),?\s*$", ts, re.M))
    assert set(fields) == {
        "as_of",
        "status",
        "gamma_bar",
        "ci90_low",
        "ci90_high",
        "p_value",
        "n_periods",
    }, fields
    real = json.loads(REAL_RESULT.read_text())
    assert fields["as_of"] == f"'{signal_test.FREEZE_AS_OF.isoformat()}'"
    assert fields["status"] == f"'{real['status']}'"
    assert int(fields["n_periods"]) == real["n_periods"]
    expected = {
        "gamma_bar": real["gamma_bar"],
        "ci90_low": real["ci90"]["low"],
        "ci90_high": real["ci90"]["high"],
        "p_value": real["p_value"],
    }
    for key, true_value in expected.items():
        literal = fields[key]
        assert float(literal) == round(true_value, _decimals(literal)), (key, literal, true_value)
