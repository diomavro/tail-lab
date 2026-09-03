#!/usr/bin/env python3
"""Write, or verify, the manifest of the optionsDX vendor corpus.

The corpus is a hand-download: 112 archives, licence-limited, acquired over
several rounds. Its filenames carry a random vendor token
(``spy_eod_2012-gghtsj.7z``), so two people cannot compare corpora by eye and
"do I already have this?" is not answerable from a directory listing. Working
out what was missing took six rounds of downloading and re-checking, and most
of the files fetched in those rounds turned out to be ones already held.

This makes that a checklist. `--write` records what each archive covers and
its checksum; `--verify` reports drift -- a missing archive, a corrupted one, a
month that has quietly vanished. Neither reads the option data itself, only the
archive index, so both are fast.

The manifest is committed; the 1.7 GB it describes is not (`data/` is
gitignored). If the corpus is ever lost, re-acquisition becomes a list of
symbol/unit pairs rather than a rediscovery.

    python scripts/optionsdx_manifest.py --write
    python scripts/optionsdx_manifest.py --verify
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import re
import sys
from pathlib import Path

VENDOR_DIR = Path("data/vendor/optionsdx")
MANIFEST = Path("docs/OPTIONSDX_MANIFEST.tsv")
_MONTH = re.compile(r"_eod_(\d{4})(\d{2})\.txt$")
_TOKEN = re.compile(r"-[a-z0-9]+\.7z$")


def _unit(name: str) -> str:
    """The vendor's download unit, with its random token stripped."""
    _sym, rest = name.split("_eod_", 1)
    return _TOKEN.sub("", rest).replace(".7z", "")


def scan(vendor_dir: Path) -> list[dict[str, str]]:
    import py7zr

    rows: list[dict[str, str]] = []
    for path in sorted(vendor_dir.glob("*_eod_*.7z")):
        with py7zr.SevenZipFile(path, "r") as handle:
            names = handle.getnames()
        months = sorted(f"{m.group(1)}{m.group(2)}" for n in names if (m := _MONTH.search(n)))
        _MONTHS_BY_FILE[path.name] = months
        digest = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
        rows.append(
            {
                "symbol": path.name.split("_eod_")[0],
                "unit": _unit(path.name),
                "months": str(len(months)),
                "first": months[0] if months else "",
                "last": months[-1] if months else "",
                "bytes": str(path.stat().st_size),
                "md5": digest,
                "filename": path.name,
            }
        )
    return rows


_FIELDS = ("symbol", "unit", "months", "first", "last", "bytes", "md5", "filename")

#: Filled by `scan`, so the summary can count DISTINCT months without a second
#: pass over the archives.
_MONTHS_BY_FILE: dict[str, list[str]] = {}


def _months_of(filename: str) -> list[str]:
    return _MONTHS_BY_FILE.get(filename, [])


def _gaps(months: list[str]) -> list[str]:
    """Months absent between the first and last held, as YYYYMM."""
    if not months:
        return []
    lo, hi = months[0], months[-1]
    span = [
        f"{y}{m:02d}"
        for y in range(int(lo[:4]), int(hi[:4]) + 1)
        for m in range(1, 13)
        if lo <= f"{y}{m:02d}" <= hi
    ]
    held = set(months)
    return [m for m in span if m not in held]


def write(rows: list[dict[str, str]]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(_FIELDS)]
    lines += [
        "\t".join(r[f] for f in _FIELDS)
        for r in sorted(rows, key=lambda r: (r["symbol"], r["unit"]))
    ]
    MANIFEST.write_text("\n".join(lines) + "\n")
    # DISTINCT months, not the sum of per-archive counts: the corpus mixes
    # year files with quarter files (`tsla_eod_2022q2_3`), so archives overlap
    # and summing double-counts. The first cut of this reported tsla as 97
    # months when it holds 96 -- a coverage number that is wrong upward is
    # exactly the kind this dataset cannot afford.
    covered: dict[str, set[str]] = collections.defaultdict(set)
    for r in rows:
        for path in _months_of(r["filename"]):
            covered[r["symbol"]].add(path)
    print(f"wrote {MANIFEST} — {len(rows)} archives")
    for sym in sorted(covered):
        months = sorted(covered[sym])
        span = f"{months[0]}..{months[-1]}"
        gaps = _gaps(months)
        note = "complete" if not gaps else f"{len(gaps)} MISSING"
        print(f"  {sym:6}{len(months):>5} months  {span}  {note}")


def verify(rows: list[dict[str, str]]) -> int:
    if not MANIFEST.exists():
        print(f"{MANIFEST} does not exist — run --write first", file=sys.stderr)
        return 2
    recorded = {}
    for line in MANIFEST.read_text().splitlines()[1:]:
        fields = dict(zip(_FIELDS, line.split("\t"), strict=True))
        recorded[fields["filename"]] = fields
    present = {r["filename"]: r for r in rows}

    missing = sorted(set(recorded) - set(present))
    extra = sorted(set(present) - set(recorded))
    changed = [
        n for n in sorted(set(recorded) & set(present)) if recorded[n]["md5"] != present[n]["md5"]
    ]
    for n in missing:
        print(f"MISSING  {recorded[n]['symbol']} {recorded[n]['unit']}  ({n})")
    for n in extra:
        print(f"EXTRA    {present[n]['symbol']} {present[n]['unit']}  ({n})")
    for n in changed:
        print(f"CHANGED  {n} — md5 differs from the manifest")
    if not (missing or extra or changed):
        print(f"corpus matches the manifest: {len(present)} archives, all checksums good")
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--vendor-dir", type=Path, default=VENDOR_DIR)
    args = parser.parse_args(argv)

    if not args.vendor_dir.is_dir():
        print(f"{args.vendor_dir} does not exist — nothing to describe", file=sys.stderr)
        return 2
    rows = scan(args.vendor_dir)
    if args.write:
        write(rows)
        return 0
    return verify(rows)


if __name__ == "__main__":
    sys.exit(main())
