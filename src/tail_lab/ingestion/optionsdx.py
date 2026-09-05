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
import gc
import io
import logging
import re
import tempfile
import warnings
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError, ParserWarning
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
    #: Rows kept whose greek block is absent: either the vendor left it blank
    #: or the physical check voided it as impossible. Counted together because
    #: the stored value is the same (NA) and the count comes off `iv.isna()`,
    #: which cannot tell them apart -- so read this as "rows with no usable
    #: greeks", not as "rows we rejected".
    voided_greek_rows: int = 0
    missing_months: tuple[str, ...] = field(default_factory=tuple)


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    """Strip the vendor's ``[BRACKETS]`` and padding from column names."""
    frame.columns = [str(c).strip().strip("[]").upper() for c in frame.columns]
    return frame


_SKIPPED_LINE = re.compile(r"^Skipping line ", re.M)


def _read_csv_counting_bad_rows(source: Path | io.StringIO) -> tuple[pd.DataFrame, int]:
    """Read the vendor CSV, skipping rows the tokeniser rejects, and count them.

    ``on_bad_lines`` defaults to raising, which meant one stray comma in one
    member of one archive aborted that whole symbol's ingest -- and the
    surrounding code claims the opposite, that an unreadable row is counted
    rather than silently lost. Neither "abort the symbol" nor "drop it quietly"
    is right for a 6.6M-row corpus: the row is skipped so the other 74,182 rows
    of that month survive, and counted so a vendor format change still shows up
    as a number instead of as absence.

    The count comes from the reader's own warnings rather than a line tally,
    because pandas reports exactly the lines it could not tokenise and a
    hand-rolled tally has to re-derive blank-line and header handling to agree
    with it. Several skipped lines arrive batched in ONE warning, so the
    occurrences are counted, not the warnings.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ParserWarning)
        frame = pd.read_csv(source, skipinitialspace=True, on_bad_lines="warn")
    malformed = sum(
        len(_SKIPPED_LINE.findall(str(w.message)))
        for w in caught
        if issubclass(w.category, ParserWarning)
    )
    return frame, malformed


def _slice_frame(symbol: str, raw: pd.DataFrame, malformed: int) -> tuple[pd.DataFrame, int]:
    """Slice a whole month's frame to the put wing, vectorised.

    Pure, and the single place the filters live so the streaming path and the
    test path cannot drift. ``malformed`` is the count of rows the CSV reader
    could not tokenise at all, folded into the returned unparsable total so
    that number means "rows this month lost", whatever the reason.
    """
    raw = _normalise(raw)
    missing = [name for name in _COLUMNS if name not in raw.columns]
    if missing:
        raise ValueError(
            f"optionsDX file for {symbol} is missing column(s) {missing}; "
            f"got {list(raw.columns)}. The vendor's format changed -- check it "
            "before trusting any partition."
        )

    out = pd.DataFrame(
        {
            "underlying": symbol.upper(),
            "quote_date": pd.to_datetime(raw["QUOTE_DATE"], errors="coerce"),
            "expiration": pd.to_datetime(raw["EXPIRE_DATE"], errors="coerce"),
            "strike": pd.to_numeric(raw["STRIKE"], errors="coerce"),
            "bid": pd.to_numeric(raw["P_BID"], errors="coerce"),
            "ask": pd.to_numeric(raw["P_ASK"], errors="coerce"),
            "volume": pd.to_numeric(raw["P_VOLUME"], errors="coerce"),
            "spot": pd.to_numeric(raw["UNDERLYING_LAST"], errors="coerce"),
            "iv": pd.to_numeric(raw["P_IV"], errors="coerce"),
            "delta": pd.to_numeric(raw["P_DELTA"], errors="coerce"),
            "vega": pd.to_numeric(raw["P_VEGA"], errors="coerce"),
            "theta": pd.to_numeric(raw["P_THETA"], errors="coerce"),
        }
    )
    dte = pd.to_numeric(raw["DTE"], errors="coerce")
    del raw

    # A row whose numbers cannot be read at all is COUNTED, so a vendor format
    # change surfaces as a number rather than as quietly missing data.
    unreadable = out["spot"].isna() | out["strike"].isna() | dte.isna()
    unparsable = int(unreadable.sum()) + malformed

    keep = (
        ~unreadable
        & (out["spot"] > 0)
        & dte.between(0, MAX_DTE_DAYS)
        & (out["strike"] / out["spot"]).between(MONEYNESS_MIN, MONEYNESS_MAX)
        # No ask is not a quote. A zero BID is a real market state and is kept.
        & out["ask"].notna()
        & (out["ask"] > 0)
    )
    out = out.loc[keep].reset_index(drop=True)

    # A blank IV means the vendor's solver did not converge, and it does not
    # blank the REST of the block -- it fills it with garbage. Observed on
    # deep-OOM VIX puts (strike 18 against spot 27.70): IV blank, delta pinned
    # to exactly -1.0 when the true value is near zero, gamma and theta 0.0,
    # vega -41.4. A -1.0 delta PASSES the schema, so taking the block at face
    # value puts nonsense in the lake silently. The block is taken together or
    # not at all -- the same rule `ingestion/option_chain.py` applies to Cboe's
    # zero-fill, reached independently from a second vendor. The QUOTE on such
    # a row is still trusted and kept: the solver failed, the market did not.
    #
    # And "blank" is not the only way the solver signals failure. SPX also
    # emits IVs of -0.00047 and vegas of -313.40 -- a volatility cannot be
    # negative and a long put's vega cannot be either, so those rows are the
    # same garbage wearing a number instead of a blank. The test is therefore
    # PHYSICAL, not merely "is it present": a greek block is trusted only if
    # every part of it is possible. Anything else voids the block.
    unsolved = (
        out["iv"].isna() | (out["iv"] <= 0) | (out["vega"] < 0) | ~out["delta"].between(-1.0, 0.0)
    )
    out.loc[unsolved, ["iv", "delta", "vega", "theta"]] = pd.NA

    out["bid"] = out["bid"].fillna(0.0)
    out["volume"] = out["volume"].fillna(0).astype("int64")
    # One repeated value across millions of rows: ~56 bytes each as object
    # strings, a few as a category.
    out["underlying"] = out["underlying"].astype("category")
    return out, unparsable


def _slice_source(symbol: str, source: Path | io.StringIO) -> tuple[pd.DataFrame, int]:
    """Read one month from a path or a string, and slice it to the put wing.

    Both entry points go through here so the shipped path and the tested path
    cannot diverge -- which they had. The empty-input guard used to sit on
    ``parse_optionsdx_month`` (tests only); the ingest reads files, so an empty
    ``.7z`` member raised ``EmptyDataError`` out of ``ingest_optionsdx``'s
    member loop and killed the WHOLE symbol. The same all-or-nothing failure
    the malformed-row handling exists to prevent, reintroduced one refactor
    later by moving the reader and leaving the guard behind.

    An empty member is a vendor artefact, not a corpus fault: it contributes no
    rows and no months, and `month_coverage` reports the resulting gap.
    """
    try:
        raw, malformed = _read_csv_counting_bad_rows(source)
    except EmptyDataError:
        return pd.DataFrame(columns=_OUT_COLUMNS), 0
    return _slice_frame(symbol, raw, malformed)


def parse_optionsdx_month(symbol: str, text: str) -> tuple[pd.DataFrame, int]:
    """Slice one month's file text to the put wing. Pure: no clock, no disk.

    The in-memory entry point, used by tests. The ingest path reads the file
    directly (:func:`_read_month_file`) because materialising a 66 MB member as
    a Python string cost 258 MB of RSS on its own.
    """
    return _slice_source(symbol, io.StringIO(text))


def _read_month_file(symbol: str, path: Path) -> tuple[pd.DataFrame, int]:
    """Slice one month straight off disk, never materialising it as a string.

    The first version read the file into a `str` and hand-split every line.
    That builds 33 Python string objects per row where only 12 are wanted, and
    Python does not return the churn to the OS -- so RSS climbed monotonically
    across 168 months and SPX was OOM-killed twice at ~3.9 GB on a 7.4 GB
    machine, having ingested the five smaller symbols without complaint.
    pandas' C reader does the same work without the per-field Python objects.
    """
    return _slice_source(symbol, path)


def read_archive_months(archive: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(member_name, path)`` for each month inside one ``.7z``.

    One member at a time: the corpus does not fit on disk uncompressed, so it
    is never fully materialised. Imported lazily so the module stays importable
    (and the pure parser stays testable) without ``py7zr`` installed.

    **The yielded paths are valid only during iteration.** They live in a
    temporary directory that is destroyed when this generator closes, so
    ``list(read_archive_months(a))`` returns paths that no longer exist. Consume
    it in the ``for`` loop that drives the ingest, and read each file before
    asking for the next.
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
            yield member.name, member


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


def _concat_and_free(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Merge month-frames into one, consuming the caller's list.

    **This does not cap peak memory, and an earlier version of this docstring
    claimed it did.** ``pd.concat`` must hold every input plus a full copy of
    the result while it runs, so the peak is ~2x the data whether the merge
    happens in one call or in batches -- an earlier batched implementation here
    was measured against a single concat over 168 frames / 1129 MB and came out
    1 MB apart (2324 MB vs 2323 MB peak RSS). Batching moved the allocations
    around without ever reducing how much was live at once.

    What this DOES do is release the caller's reference as the frames are
    absorbed, which is worth a little on the smaller symbols and nothing like
    enough for SPX. SPX (168 months, ~7.6M rows, ~681 MB of frames) is still
    OOM-killed at ~3.9 GB and remains un-ingested. The fix is a chunked write
    -- appending each month to the Delta table instead of materialising the
    whole symbol first -- which is a change to the lake layer, not to this
    function, and is tracked in ``AGENT_TODO.md``. Do not reach for a cleverer
    concat here; the shape of the problem is that the symbol is held whole.
    """
    if not frames:
        return pd.DataFrame(columns=_OUT_COLUMNS)
    if len(frames) == 1:
        return frames.pop()
    out = pd.concat(frames, ignore_index=True)
    frames.clear()
    return out


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the optionsDX contract."""
    # lazy=True collects EVERY failure case into one error object. On SPX --
    # 7.65M rows -- a systematic column fault produced millions of them and the
    # process was OOM-killed at 3.8 GB building the error, not the data: peak
    # through concat and dedup was only 2.2 GB. Validation on a corpus this size
    # has to assume failures are rare.
    #
    # The physical greek check makes that assumption safer for the four greek
    # columns, but it does NOT guarantee it: the schema also enforces a unique
    # key over (underlying, quote_date, expiration, strike), and a duplicate
    # sweep violates that without any greek being wrong. So if the kill happens
    # it is still silent (exit 137, no traceback, mid-`validate`) -- which is
    # why the row count goes to the log BEFORE validation is attempted, where
    # it survives the process dying.
    _LOGGER.info("optionsdx.validate.start rows=%d", len(df))
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
            for _name, member in read_archive_months(archive):
                frame, bad = _read_month_file(symbol, member)
                unparsable += bad
                if not frame.empty:
                    frames.append(frame)
    else:
        for _name, text in months:
            frame, bad = parse_optionsdx_month(symbol, text)
            unparsable += bad
            if not frame.empty:
                frames.append(frame)

    combined = _concat_and_free(frames)
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

    # Take coverage off `combined` NOW and release it before the write.
    # `combined` and `valid` are near-identical copies -- 681 MB each on SPX --
    # and the Delta write adds an Arrow conversion on top of both. Holding all
    # three is what pushed SPX to 3.8 GB and got it OOM-killed twice on a 7.4 GB
    # machine, while the five smaller symbols never came close: the failure
    # scales with the largest symbol, so it only appears on the last one.
    #
    # Coverage is measured on what was PARSED, not on what survived validation:
    # a month rejected wholesale would otherwise fall outside the reported span
    # and read as "never downloaded" rather than "downloaded and unusable" --
    # opposite problems, and the first is invisible. That is exactly how a
    # re-downloaded archive (the " (2)" suffix `_REDOWNLOAD_SUFFIX` now strips)
    # silently cost VIX its 2010 while the run reported "0 missing".
    parsed_dates = [d.date() for d in combined["quote_date"].dropna()] if not combined.empty else []
    # Rows whose greek block the physical check voided (see `_slice_frame`).
    # Counted because voiding is the one loss mode that leaves NO trace
    # elsewhere: before the check those rows went to quarantine and showed up
    # in `quarantined_rows`, and now they pass validation with four NA columns
    # and would otherwise vanish from the audit trail entirely. `iv` is NA
    # exactly when the block was voided or the vendor left it blank -- the same
    # condition -- so the count comes off the frame without threading another
    # return value through the parser.
    voided = int(combined["iv"].isna().sum()) if not combined.empty else 0
    del combined
    gc.collect()

    bronze_path = store.write_bronze(f"{DATASET}_{symbol.lower()}", ingest_date, valid)
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(
            f"{QUARANTINE_DATASET}_{symbol.lower()}", ingest_date, quarantined
        )

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
        voided_greek_rows=voided,
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
        voided_greek_rows=result.voided_greek_rows or None,
        duplicate_rows=result.duplicate_rows or None,
        months_present=result.months_present,
        months_missing=result.months_missing or None,
        first_quote=result.first_quote,
        last_quote=result.last_quote,
        bronze_path=result.bronze_path,
    )
