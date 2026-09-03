"""optionsDX historical end-of-day chains -- bronze layer
(``docs/DATA_CONTRACTS.md`` #12, ``contracts/optionsdx``).

Like ``ingestion/option_quotes.py`` and unlike every other adapter here, this
one touches **no network**: its source is a set of ``.7z`` archives the human
downloaded by hand from optionsDX into ``data/vendor/optionsdx/`` (gitignored,
licence-limited, absent on CI and on the deployed app -- which is the point).
Nothing in ``research/`` or ``api/`` may require the resulting bronze to exist.

**It never extracts the corpus.** 1.1 GB of archives expand to 8.2 GB against
8.8 GB of free disk, so a bulk extract would fill the machine. Members are read
one month at a time straight out of the archive, sliced to the put wing, and
discarded -- peak footprint is one month of text, ~15 MB.

Same three-function split as the networked adapters, so tests never touch the
vendor directory:

- :func:`parse_optionsdx_month` -- pure, one month's text in, sliced frame out.
- :func:`read_archive_months` -- the 7z read, exercised only by
  ``make ingest-optionsdx``, never by CI.
- :func:`ingest_optionsdx` -- orchestrates read -> parse -> validate/quarantine
  -> commit, **one bronze partition per symbol** rather than one for the whole
  download, because the symbols have wildly different coverage and a single
  partition would make a complete VIX panel and a 40%-complete SPY one
  indistinguishable at read time (``contracts/optionsdx``).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from pandera.errors import SchemaErrors

from tail_lab.contracts.optionsdx import (
    DATASET,
    MAX_DTE_DAYS,
    MONEYNESS_MAX,
    MONEYNESS_MIN,
    OptionsDxQuoteSchema,
    month_coverage,
)
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "DEFAULT_VENDOR_DIR",
    "QUARANTINE_DATASET",
    "IngestResult",
    "ingest_optionsdx",
    "parse_optionsdx_month",
    "read_archive_months",
]

_LOGGER = logging.getLogger(__name__)

QUARANTINE_DATASET = f"{DATASET}__quarantine"

#: Where the human's download lives. Gitignored; absent on CI and on the
#: deployed app, which is what makes this an optional input rather than a
#: dependency.
DEFAULT_VENDOR_DIR = Path("data/vendor/optionsdx")

#: The vendor's columns, as they appear in the header (square brackets and
#: whitespace stripped).
_COLUMNS = (
    "QUOTE_DATE",
    "UNDERLYING_LAST",
    "EXPIRE_DATE",
    "DTE",
    "STRIKE",
    "P_BID",
    "P_ASK",
    "P_VOLUME",
    "P_IV",
    "P_DELTA",
    "P_VEGA",
    "P_THETA",
)

_OUT_COLUMNS = [
    "underlying",
    "quote_date",
    "expiration",
    "strike",
    "bid",
    "ask",
    "volume",
    "spot",
    "iv",
    "delta",
    "vega",
    "theta",
]


@dataclass(frozen=True)
class IngestResult:
    """What one symbol's ingest committed, for the ``§f`` audit trail."""

    symbol: str
    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    months_present: int
    months_missing: int
    first_quote: str | None
    last_quote: str | None
    archives_read: int = 0
    unparsable_rows: int = 0
    duplicate_rows: int = 0
    missing_months: tuple[str, ...] = field(default_factory=tuple)


def _maybe_float(text: str) -> float | None:
    """A blank vendor cell is an ABSENCE, never a zero.

    optionsDX leaves greeks blank on illiquid rows. Coercing those to 0.0 would
    put a real-looking number where there is no observation -- the same error
    ``docs/DATA_VERDICTS.md`` caught in the lambdaclass file and
    ``ingestion/option_chain.py`` guards against in Cboe's zero-fill.
    """
    t = text.strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_optionsdx_month(symbol: str, text: str) -> tuple[pd.DataFrame, int]:
    """Slice one month's file to the put wing. Pure: no clock, no filesystem.

    Returns ``(frame, unparsable_row_count)``. A row whose numbers cannot be
    read is counted rather than dropped silently, so a vendor format change
    shows up as a number instead of as quietly missing data.
    """
    lines = text.splitlines()
    if len(lines) < 2:
        return pd.DataFrame(columns=_OUT_COLUMNS), 0

    header = [c.strip().strip("[]").upper() for c in lines[0].split(",")]
    index = {name: header.index(name) for name in _COLUMNS if name in header}
    missing = [name for name in _COLUMNS if name not in index]
    if missing:
        raise ValueError(
            f"optionsDX file for {symbol} is missing column(s) {missing}; got {header}. "
            "The vendor's format changed -- check it before trusting any partition."
        )

    rows: list[dict[str, Any]] = []
    unparsable = 0
    for line in lines[1:]:
        fields = line.split(",")
        if len(fields) < len(header):
            unparsable += 1
            continue
        spot = _maybe_float(fields[index["UNDERLYING_LAST"]])
        strike = _maybe_float(fields[index["STRIKE"]])
        dte = _maybe_float(fields[index["DTE"]])
        ask = _maybe_float(fields[index["P_ASK"]])
        bid = _maybe_float(fields[index["P_BID"]])
        if spot is None or strike is None or dte is None:
            unparsable += 1
            continue
        if spot <= 0 or not 0 <= dte <= MAX_DTE_DAYS:
            continue
        if not MONEYNESS_MIN <= strike / spot <= MONEYNESS_MAX:
            continue
        # No ask is not a quote. A zero BID is a real market state and is kept.
        if ask is None or ask <= 0:
            continue
        volume = _maybe_float(fields[index["P_VOLUME"]])
        # A blank IV means the vendor's solver did not converge, and it does not
        # blank the REST of the block -- it fills it with garbage. Observed on
        # deep-OOM VIX puts (strike 15 against spot 27.70): IV blank, delta
        # pinned to exactly -1.0 when the true value is near zero, gamma and
        # theta exactly 0.0, and vega carrying nonsense like -31.4.
        #
        # So the block is taken together or not at all. This is the same rule
        # `ingestion/option_chain.py` applies to Cboe's zero-fill, arrived at
        # independently from a different vendor's failure -- which is reason
        # enough to treat it as the house rule for any greek source. Keeping
        # only the rows where IV survives is not a filter on liquidity; the
        # quote itself (bid/ask) is still trusted and kept.
        iv = _maybe_float(fields[index["P_IV"]])
        solved = iv is not None
        rows.append(
            {
                "underlying": symbol.upper(),
                "quote_date": fields[index["QUOTE_DATE"]].strip(),
                "expiration": fields[index["EXPIRE_DATE"]].strip(),
                "strike": strike,
                "bid": 0.0 if bid is None else bid,
                "ask": ask,
                "volume": 0 if volume is None else int(volume),
                "spot": spot,
                "iv": iv,
                "delta": _maybe_float(fields[index["P_DELTA"]]) if solved else None,
                "vega": _maybe_float(fields[index["P_VEGA"]]) if solved else None,
                "theta": _maybe_float(fields[index["P_THETA"]]) if solved else None,
            }
        )

    frame = pd.DataFrame(rows, columns=_OUT_COLUMNS)
    if not frame.empty:
        frame["quote_date"] = pd.to_datetime(frame["quote_date"], errors="coerce")
        frame["expiration"] = pd.to_datetime(frame["expiration"], errors="coerce")
    return frame, unparsable


def read_archive_months(archive: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(member_name, text)`` for each month inside one ``.7z``.

    One member at a time: the corpus does not fit on disk uncompressed, so it
    is never fully materialised. Imported lazily so the module stays importable
    (and the pure parser stays testable) without ``py7zr`` installed.
    """
    import py7zr

    # ONE archive at a time, into a temp directory that deletes itself.
    #
    # Not a stream: py7zr's in-memory `read()` was removed in 1.x, and pinning
    # to the older API is a poor trade for a dependency whose surface has
    # already shifted once. `extract` is the stable path.
    #
    # Not the whole corpus either: 7z here is solid-compressed, so pulling a
    # single member can inflate the entire block, but a whole-corpus extract
    # needs 8.2 GB against ~8.8 GB free. Per-archive is the middle: peak disk is
    # one archive uncompressed (~40 MB for a VIX year, ~200 MB for the largest
    # SPX quarter) and it is reclaimed before the next one starts.
    with tempfile.TemporaryDirectory(prefix="optionsdx-") as tmp:
        root = Path(tmp)
        with py7zr.SevenZipFile(archive, "r") as handle:
            handle.extractall(path=root)
        for member in sorted(root.rglob("*.txt")):
            yield member.name, member.read_text(encoding="utf-8", errors="replace")


#: A browser re-download lands as ``name (1).7z``, ``name (2).7z`` beside the
#: original. Left alone, the glob reads the same months twice, every row
#: collides on the schema's ``(underlying, quote_date, expiration, strike)``
#: key, and pandera quarantines BOTH copies -- so a stray duplicate file does
#: not merely waste time, it silently deletes a year. That is exactly what
#: happened to VIX 2010 on the first real run: coverage said 168 months
#: present, and the stored data started in 2011.
_REDOWNLOAD_SUFFIX = re.compile(r" \(\d+\)(?=\.7z$)")


def _dedupe_archives(paths: Sequence[Path]) -> list[Path]:
    """One archive per canonical name, preferring the copy without a suffix."""
    by_canonical: dict[str, Path] = {}
    for path in paths:
        canonical = _REDOWNLOAD_SUFFIX.sub("", path.name)
        if canonical not in by_canonical or path.name == canonical:
            by_canonical[canonical] = path
    return [by_canonical[k] for k in sorted(by_canonical)]


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the optionsDX contract."""
    try:
        return OptionsDxQuoteSchema.validate(df, lazy=True), df.iloc[0:0]
    except SchemaErrors as err:
        bad = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad)]
        kept = df.loc[~df.index.isin(bad)]
        return OptionsDxQuoteSchema.validate(kept, lazy=True), quarantined


def ingest_optionsdx(
    store: LakeStore,
    symbol: str,
    *,
    vendor_dir: Path | None = None,
    ingest_date: dt.date | None = None,
    months: Sequence[tuple[str, str]] | None = None,
) -> IngestResult:
    """Read every archive for ``symbol``, slice, and commit ONE bronze partition.

    ``months`` lets tests drive the whole orchestration off in-memory text
    without a vendor directory or ``py7zr``.
    """
    vendor_dir = vendor_dir or DEFAULT_VENDOR_DIR
    ingest_date = ingest_date or dt.date.today()

    frames: list[pd.DataFrame] = []
    unparsable = 0
    archives = 0
    if months is None:
        found = _dedupe_archives(sorted(vendor_dir.glob(f"{symbol.lower()}_eod_*.7z")))
        if not found:
            raise FileNotFoundError(
                f"no optionsDX archives for {symbol!r} in {vendor_dir} — this adapter "
                "reads a hand-downloaded, licence-limited corpus (HUMAN_TODO.md)."
            )
        for archive in found:
            archives += 1
            for _name, text in read_archive_months(archive):
                frame, bad = parse_optionsdx_month(symbol, text)
                unparsable += bad
                if not frame.empty:
                    frames.append(frame)
    else:
        for _name, text in months:
            frame, bad = parse_optionsdx_month(symbol, text)
            unparsable += bad
            if not frame.empty:
                frames.append(frame)

    combined = (
        pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=_OUT_COLUMNS)
    )
    # Filename dedup handles re-downloads; this handles OVERLAPPING archives,
    # which is a different problem with the same symptom -- the corpus mixes
    # year files with quarter files (`tsla_eod_2022q2_3`), so two archives can
    # legitimately cover the same month. Dropping the collision here keeps the
    # duplicate out of quarantine, where it would look like bad data instead of
    # repeated data.
    before = len(combined)
    if not combined.empty:
        combined = combined.drop_duplicates(
            subset=["underlying", "quote_date", "expiration", "strike"], keep="first"
        ).reset_index(drop=True)
    duplicate_rows = before - len(combined)

    valid, quarantined = validate_and_quarantine(combined)

    bronze_path = store.write_bronze(f"{DATASET}_{symbol.lower()}", ingest_date, valid)
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(
            f"{QUARANTINE_DATASET}_{symbol.lower()}", ingest_date, quarantined
        )

    # Coverage is computed from everything PARSED, not from what survived
    # validation. A month rejected wholesale would otherwise fall outside the
    # reported span entirely and read as "never downloaded" rather than
    # "downloaded and unusable" -- opposite problems, and the first one is
    # invisible. Nearly cost exactly that: the first VIX run lost all of 2010 to
    # a greek-block bug and cheerfully reported `0 MISSING`.
    parsed_dates = [d.date() for d in combined["quote_date"].dropna()] if not combined.empty else []
    present, absent = month_coverage(parsed_dates)
    result = IngestResult(
        symbol=symbol.upper(),
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        months_present=len(present),
        months_missing=len(absent),
        first_quote=present[0] if present else None,
        last_quote=present[-1] if present else None,
        archives_read=archives,
        unparsable_rows=unparsable,
        duplicate_rows=duplicate_rows,
        missing_months=tuple(absent),
    )
    _log_run(result, ingest_date)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date) -> None:
    log_event(
        _LOGGER,
        "ingestion.optionsdx.run",
        dataset=f"{DATASET}_{result.symbol.lower()}",
        symbol=result.symbol,
        ingest_date=ingest_date,
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        archives_read=result.archives_read or None,
        unparsable_rows=result.unparsable_rows or None,
        duplicate_rows=result.duplicate_rows or None,
        months_present=result.months_present,
        months_missing=result.months_missing or None,
        first_quote=result.first_quote,
        last_quote=result.last_quote,
        bronze_path=result.bronze_path,
    )
