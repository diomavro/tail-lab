"""The per-name page names screens with a map in ``signalWording.ts`` that
duplicates ``walk_forward._LABELS`` (the labels the API puts on the screens).
The duplicate is kept -- the name record carries screen ids, not labels -- so
this test parses the TypeScript map and fails the moment the two drift."""

from __future__ import annotations

import re
from pathlib import Path

from tail_lab.research.backtest import walk_forward

WORDING = Path(__file__).resolve().parents[1] / "frontend/src/components/putlab/signalWording.ts"


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
