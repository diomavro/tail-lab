"""Every table header and stat label on the Put Lab explains itself.

``docs/adr/0033`` (section 5.4): a ``<th>`` is written ``<Th concept=...>``
(header plus a ConceptInfo), a stat label ``<StatK concept=...>``. A header
that needs no explanation says so with ``data-noexplain="index|name|row-label"``.
This is a static scan of ``frontend/src/components/putlab/**/*.tsx``; the browser
twin (``frontend/e2e/explained.spec.ts``) checks the rendered result.

Every file under that tree is scanned; there is no allow-list of files still to
convert (step C of the sweep emptied it and the mechanism was deleted).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PUTLAB = Path(__file__).resolve().parents[1] / "frontend/src/components/putlab"
ALLOWED_NOEXPLAIN = {"index", "name", "row-label"}

#: The two components that are allowed to spell the raw elements.
WRAPPERS = {"Th.tsx", "StatK.tsx"}

_TAG_START = re.compile(r"<th(?=[\s>/])")


def _tsx_files() -> list[Path]:
    return sorted(PUTLAB.rglob("*.tsx"))


def _rel(path: Path, root: Path = PUTLAB) -> str:
    return path.relative_to(root).as_posix()


def _tag_end(src: str, start: int) -> int:
    """Index just past the ``>`` closing the tag that opens at ``start``.

    Skips ``{...}`` attribute values (which may contain ``>``, e.g. an arrow
    function) by brace depth, and quoted strings outside braces."""
    depth = 0
    quote = ""
    i = start
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'" and depth == 0:
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == ">" and depth == 0:
            return i + 1
        i += 1
    raise AssertionError("unterminated tag")


def raw_th_tags(src: str) -> list[str]:
    """Every raw ``<th ...>`` opening tag in ``src`` (multi-line tags included)."""
    return [src[m.start() : _tag_end(src, m.start())] for m in _TAG_START.finditer(src)]


def offences(path: Path, root: Path = PUTLAB) -> list[str]:
    """What is wrong with one file, as readable lines."""
    src = path.read_text()
    name = _rel(path, root)
    found: list[str] = []
    if name not in WRAPPERS:
        for tag in raw_th_tags(src):
            m = re.search(r"""data-noexplain=(?:"([^"]*)"|'([^']*)')""", tag)
            value = (m.group(1) or m.group(2)) if m else None
            if value not in ALLOWED_NOEXPLAIN:
                one_line = " ".join(tag.split())
                found.append(f"{name}: raw <th> without an enum data-noexplain: {one_line}")
        for token in ("pl-stat-k", "pl-readout-k"):
            if token in src:
                found.append(f"{name}: '{token}' belongs in StatK.tsx only; use <StatK>")
    return found


def test_the_scanner_reads_the_tree() -> None:
    files = _tsx_files()
    assert files, "scanned no files: the rule would pass vacuously"
    names = {_rel(p) for p in files}
    assert names >= WRAPPERS
    # The files the sweep converted are really in the tree it scans.
    assert {"views/PitchView.tsx", "Ledger.tsx", "views/SurfaceView.tsx"} <= names


def test_every_file_explains_every_header_and_label() -> None:
    problems = [line for p in _tsx_files() for line in offences(p)]
    assert problems == []


def test_the_wrappers_carry_the_concept_button() -> None:
    assert "ConceptInfo" in (PUTLAB / "Th.tsx").read_text()
    stat_k = (PUTLAB / "StatK.tsx").read_text()
    assert "pl-stat-k" in stat_k and "pl-readout-k" in stat_k and "ConceptInfo" in stat_k


@pytest.mark.parametrize(
    ("src", "bad"),
    [
        ("<th>Hit</th>", 1),
        ("<th\n  className='num'\n>Hit</th>", 1),
        ('<th data-noexplain="index">#</th>', 0),
        ('<th data-noexplain="nonsense">x</th>', 1),
        ("<th onClick={() => a > b}>x</th>", 1),
        ('<Th concept="hit_rate">Hit</Th>', 0),
        ("<thead><tr></tr></thead>", 0),
    ],
)
def test_the_scanner_catches_what_it_should(src: str, bad: int) -> None:
    tags = raw_th_tags(src)
    flagged = [
        t
        for t in tags
        if not (m := re.search(r'data-noexplain="([^"]*)"', t))
        or m.group(1) not in ALLOWED_NOEXPLAIN
    ]
    assert len(flagged) == bad


def test_a_regression_in_a_converted_file_is_caught(tmp_path: Path) -> None:
    """Mutation check: add a bare <th> and a raw stat label to a copy of a
    converted file and the offence list must name both."""
    broken = tmp_path / "BakeOff.tsx"
    broken.write_text(
        (PUTLAB / "BakeOff.tsx").read_text()
        + '\n<th>Bare</th>\n<div className="pl-stat-k">k</div>\n'
    )
    problems = offences(broken, tmp_path)
    assert any("raw <th>" in p for p in problems)
    assert any("pl-stat-k" in p for p in problems)
    assert offences(PUTLAB / "BakeOff.tsx") == []
