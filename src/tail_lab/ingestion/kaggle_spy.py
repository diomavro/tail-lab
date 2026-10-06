"""Kaggle SPY 2014-2025 chain -> per-year parquet under ``data/vendor/``
(``docs/DATA_CONTRACTS.md`` #13, ``contracts/kaggle_spy``).

**An offline cross-check, never a served dataset.** Unlike every other adapter
here this one does NOT write to the lake: the lake is what the API reads (and
on Fly it is Tigris), so keeping this corpus out of it is what makes "never
served" structural rather than a promise. Output is one parquet per calendar
year under the gitignored ``data/vendor/kaggle_spy/parquet/``. Like
``ingestion/optionsdx.py`` it touches no network: the source is a file the
human downloaded (``HUMAN_TODO.md``), absent on CI and on the deployed app.

**It never holds a year's JSON in memory, let alone the corpus.** Each year is
one ~0.35-1.06 GB line of JSON (8.7 GB in all, against ~15 GB free), so
``json.load`` on it would need several times that in Python objects. The
reader pulls fixed-size text chunks and parses the complete ``{...}`` records
in each -- the vendor's records are flat objects of strings, so a record never
contains a brace -- keeps the put side, and lets the rest go. Peak memory is
one year of PUTS as a frame (~1M rows), not the file.

Same split as the optionsDX adapter, so tests never touch the vendor dir:

- :func:`iter_record_batches` -- pure, a text stream in, record batches out.
- :func:`open_year_source` -- finds the year's file (plain ``.json`` or a
  member of a ``.zip``, streamed, never extracted). Used only by the make
  target.
- :func:`ingest_kaggle_spy_year` -- read -> slice -> dedupe -> validate /
  quarantine -> atomic parquet write -> structured run record.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import logging
import os
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO

import numpy as np
import pandas as pd
from pandera.errors import SchemaErrors

from tail_lab.contracts.kaggle_spy import (
    AV_COLUMNS,
    DATASET,
    GREEK_COLUMNS,
    IV_MAX,
    KaggleSpyPutSchema,
    year_file_name,
)
from tail_lab.observability import log_event

__all__ = [
    "DEFAULT_VENDOR_DIR",
    "IngestResult",
    "RecordBatch",
    "ingest_kaggle_spy_year",
    "iter_record_batches",
    "open_year_source",
    "parquet_path",
    "read_year",
    "slice_records",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

#: Where the human's download lives and where the parquet goes. Gitignored
#: (``data/``) and excluded from the image (``.dockerignore``).
DEFAULT_VENDOR_DIR = Path("data/vendor/kaggle_spy")

#: Characters read per chunk. ~35k records; small enough to be irrelevant to
#: peak memory, large enough that the join-and-parse below is one C call.
DEFAULT_CHUNK_CHARS = 16 * 1024 * 1024

#: One vendor record. Flat, string-valued, so it never contains a brace.
_RECORD = re.compile(r"\{[^{}]*\}")
#: The key every vendor record carries exactly once. Counting it -- not ``{``,
#: and not ``{"contractID"`` -- is what accounts for a record that lost either
#: brace, and keeps the 2014-2018/2025 files' outer ``{date: [...]}`` wrapper
#: from reading as a broken record.
_RECORD_KEY = '"contractID"'


_OUT_COLUMNS = [
    "contract_id",
    "quote_date",
    "expiration",
    "strike",
    "last",
    "mark",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "volume",
    "open_interest",
    *GREEK_COLUMNS,
]

_KEY = ["quote_date", "contract_id"]


@dataclass
class _Tally:
    """Row losses, accumulated across batches -- every loss is a number."""

    records_read: int = 0
    not_spy_put_rows: int = 0
    unparsable_rows: int = 0
    no_ask_rows: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class IngestResult:
    """What one year's ingest wrote, for the ``§f`` audit trail."""

    year: int
    source: str
    parquet_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    records_read: int
    not_spy_put_rows: int
    unparsable_rows: int
    no_ask_rows: int
    duplicate_rows: int
    #: Rows kept with no usable greek block (blank, zero-filled or impossible).
    voided_greek_rows: int
    trading_days: int
    first_quote: str
    last_quote: str
    #: Median over days of (distinct IV values / puts with an IV). ~1.0 means
    #: per-contract inversion; well below means the vendor smoothed it.
    iv_distinct_ratio: float | None


def _parse_records(texts: list[str]) -> tuple[list[dict[str, object]], int]:
    """Parse matched ``{...}`` spans: one ``json.loads`` for the batch, falling
    back to one per record only when the batch holds a bad one.

    Losses are counted in RECORDS, not spans: a span holding two
    ``"contractID"`` keys is two fused records (a lost ``}, {``). If it still
    parses, JSON keeps one of them and the other is a loss; if it fails, both
    are.
    """
    if not texts:
        return [], 0
    try:
        parsed: object = json.loads("[" + ",".join(texts) + "]")
    except json.JSONDecodeError:
        good: list[dict[str, object]] = []
        lost = 0
        for text in texts:
            keys = text.count(_RECORD_KEY)
            try:
                one: object = json.loads(text)
            except json.JSONDecodeError:
                lost += max(1, keys)
                continue
            if isinstance(one, dict):
                good.append(one)
                lost += max(0, keys - 1)
            else:
                lost += max(1, keys)
        return good, lost
    if not isinstance(parsed, list):  # unreachable: the text was built as "[...]"
        raise TypeError(f"expected a JSON array, got {type(parsed).__name__}")
    records = [r for r in parsed if isinstance(r, dict)]
    fused = sum(max(0, t.count(_RECORD_KEY) - 1) for t in texts)
    return records, len(parsed) - len(records) + fused


@dataclass(frozen=True)
class RecordBatch:
    """One chunk's parsed records and the records it lost.

    ``truncated`` is set only on the final batch, when the file does not end
    the way every complete year file does (``]]`` or ``]}``): a cut-off
    download, which must not replace a year even if what it holds parses.
    """

    records: list[dict[str, object]]
    unparsable: int
    truncated: bool = False


#: How every complete year file ends: an array of day arrays, or an object
#: whose last value is a day array.
_COMPLETE_ENDINGS = ("]]", "]}")


def iter_record_batches(
    stream: TextIO, *, chunk_chars: int = DEFAULT_CHUNK_CHARS
) -> Iterator[RecordBatch]:
    """Yield a :class:`RecordBatch` per chunk of the vendor's JSON text, and
    always a final one carrying the end-of-file verdict.

    The outer structure is skipped rather than parsed, so it is irrelevant --
    and it does differ by year: 2019-2024 are an array of day arrays
    (``[[{..}, ..], [{..}]]``), 2014-2018 and 2025 an object keyed by date
    (``{"2014-01-01": [], "2014-01-02": [{..}, ..]}``, holidays as empty
    lists). Nothing larger than a chunk is ever held.

    A record split across a chunk boundary is carried to the next chunk. Every
    ``"contractID"`` key not inside a complete ``{...}`` is a record that was
    read and lost -- a missing opening or closing brace mid-stream, or records
    still open at end of file (a truncated download) -- and is counted as
    unparsable rather than vanishing.
    """
    carry = ""
    ending = ""
    while True:
        chunk = stream.read(chunk_chars)
        if not chunk:
            break
        ending = (ending + chunk)[-64:]
        text = carry + chunk
        cut = text.rfind("}") + 1
        texts = _RECORD.findall(text, 0, cut)
        orphans = text.count(_RECORD_KEY, 0, cut) - sum(t.count(_RECORD_KEY) for t in texts)
        carry = text[cut:]
        if texts or orphans:
            records, bad = _parse_records(texts)
            yield RecordBatch(records, bad + orphans)
    tail = carry.count(_RECORD_KEY) or int("{" in carry)
    yield RecordBatch([], tail, truncated=not ending.rstrip().endswith(_COMPLETE_ENDINGS))


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce")


def _count(frame: pd.DataFrame, column: str) -> pd.Series:
    """A size / volume / open-interest field. Blank, garbage or fractional is
    ABSENT (``<NA>``), never a fabricated 0 -- the same rule as the bid."""
    n = _num(frame, column)
    # NaN and +-inf fail the magnitude test too, so it is the only guard needed
    # before the cast: a value int64 cannot hold would otherwise crash the year.
    representable = (n.abs() < 2**63) & (n == n.round())
    return n.where(representable).astype("Int64")


def _void_impossible_greeks(out: pd.DataFrame) -> None:
    """The house rule (Cboe zero-fill, optionsDX blank ``P_IV``): a greek block
    that is absent IN ANY PART, zero-filled or physically impossible is voided
    WHOLE.

    An IV of exactly 0 is the vendor's "could not compute", not a measurement;
    any infinite greek, an IV above ``IV_MAX``, a put's delta outside [-1, 0],
    or a negative gamma or vega, is garbage wearing a number. So is the
    "pinned" block -- delta exactly -1 with gamma and vega exactly 0 beside a
    positive IV -- the signature optionsDX's failed solver leaves too: an IV
    that produces no gamma and no vega is not one the block was computed
    from (21,260 such 2023 puts, IVs 0.02-6.84; 18 more with delta -1.00000 but a
    non-zero gamma or vega are kept as rounding). Voiding here rather
    than leaving it to the schema matters: the schema would quarantine the
    whole ROW, quote included. The quote is kept: the solver failed, the
    market did not.
    """
    block = out[list(GREEK_COLUMNS)].replace([np.inf, -np.inf], np.nan)
    unsolved = (
        block.isna().any(axis=1)
        | ~((out["iv"] > 0) & (out["iv"] <= IV_MAX))
        | ~out["delta"].between(-1.0, 0.0)
        | (out["gamma"] < 0)
        | (out["vega"] < 0)
        | ((out["delta"] == -1.0) & (out["gamma"] == 0) & (out["vega"] == 0))
    )
    out.loc[unsolved, list(GREEK_COLUMNS)] = pd.NA


def slice_records(records: list[dict[str, object]], tally: _Tally) -> pd.DataFrame:
    """Vendor records -> SPY put rows in contract column names. Pure.

    Raises on a changed key set: a renamed column parsed as something else is
    the one failure that would put plausible nonsense in the parquet.
    """
    tally.records_read += len(records)
    if not records:
        return pd.DataFrame(columns=_OUT_COLUMNS)
    raw = pd.DataFrame.from_records(records)
    if set(raw.columns) != set(AV_COLUMNS):
        raise ValueError(
            f"Kaggle SPY record keys changed: missing {sorted(set(AV_COLUMNS) - set(raw.columns))}, "
            f"unexpected {sorted(set(raw.columns) - set(AV_COLUMNS))}. Check the vendor format "
            "before trusting any parquet."
        )
    is_put = (raw["type"] == "put") & (raw["symbol"] == "SPY")
    tally.not_spy_put_rows += int((~is_put).sum())
    raw = raw.loc[is_put]

    out = pd.DataFrame(
        {
            "contract_id": raw["contractID"].astype(str),
            "quote_date": pd.to_datetime(raw["date"], format="%Y-%m-%d", errors="coerce"),
            "expiration": pd.to_datetime(raw["expiration"], format="%Y-%m-%d", errors="coerce"),
            "strike": _num(raw, "strike"),
            "last": _num(raw, "last"),
            "mark": _num(raw, "mark"),
            "bid": _num(raw, "bid"),
            "ask": _num(raw, "ask"),
            "bid_size": _count(raw, "bid_size"),
            "ask_size": _count(raw, "ask_size"),
            "volume": _count(raw, "volume"),
            "open_interest": _count(raw, "open_interest"),
            "iv": _num(raw, "implied_volatility"),
            "delta": _num(raw, "delta"),
            "gamma": _num(raw, "gamma"),
            "theta": _num(raw, "theta"),
            "vega": _num(raw, "vega"),
            "rho": _num(raw, "rho"),
        }
    )
    # A blank BID is unreadable, not a zero bid: "$0.00 bid" is a real market
    # state, and inventing one would show up as a vendor disagreement.
    unreadable = (
        out["quote_date"].isna()
        | out["expiration"].isna()
        | out["strike"].isna()
        | out["bid"].isna()
    )
    tally.unparsable_rows += int(unreadable.sum())
    out = out.loc[~unreadable]
    # No ask is not a quote (the optionsDX rule); a zero BID is a real state.
    no_ask = out["ask"].isna() | (out["ask"] <= 0)
    tally.no_ask_rows += int(no_ask.sum())
    out = out.loc[~no_ask].reset_index(drop=True)
    _void_impossible_greeks(out)
    return out


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the contract."""
    try:
        return KaggleSpyPutSchema.validate(df, lazy=True), df.iloc[0:0]
    except SchemaErrors as err:
        bad = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad)]
        kept = df.loc[~df.index.isin(bad)]
        return KaggleSpyPutSchema.validate(kept, lazy=True), quarantined


def parquet_path(year: int, out_dir: Path | None = None) -> Path:
    """Where ``year``'s validated puts live."""
    return (out_dir or DEFAULT_VENDOR_DIR / "parquet") / f"{DATASET}_puts_{year}.parquet"


def read_year(year: int, *, out_dir: Path | None = None) -> pd.DataFrame:
    """Read one ingested year. Raises ``FileNotFoundError`` naming the fix."""
    path = parquet_path(year, out_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"no Kaggle SPY parquet for {year} at {path} -- run "
            f"`make ingest-kaggle-spy YEAR={year}` after the download in HUMAN_TODO.md"
        )
    return pd.read_parquet(path)


@contextlib.contextmanager
def open_year_source(vendor_dir: Path, year: int) -> Iterator[tuple[TextIO, str]]:
    """Open ``year``'s JSON as a text stream, plain or inside a zip.

    The kaggle CLI may deliver a single file as ``<name>.json.zip``, and a
    whole-dataset download is one zip of all twelve; either is read as a
    stream straight out of the archive, never extracted -- 8.7 GB does not fit.
    """
    name = year_file_name(year)
    plain = vendor_dir / name
    if plain.exists():
        with plain.open(encoding="utf-8") as handle:
            yield handle, str(plain)
        return
    for archive in sorted(vendor_dir.glob("*.zip")):
        with zipfile.ZipFile(archive) as zf:
            members = [m for m in zf.namelist() if Path(m).name == name]
            if members:
                with zf.open(members[0]) as raw:
                    yield io.TextIOWrapper(raw, encoding="utf-8"), f"{archive}!{members[0]}"
                return
    raise FileNotFoundError(
        f"no {name} (plain or inside a .zip) in {vendor_dir} -- this adapter reads a "
        "hand-downloaded, licence-limited corpus (HUMAN_TODO.md)."
    )


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    """A killed run must not leave a half-written year that reads as whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _iv_distinct_ratio(valid: pd.DataFrame) -> float | None:
    with_iv = valid.loc[valid["iv"].notna(), ["quote_date", "iv"]]
    if with_iv.empty:
        return None
    per_day = with_iv.groupby("quote_date")["iv"].agg(["nunique", "count"])
    return float((per_day["nunique"] / per_day["count"]).median())


def _read_stream(stream: TextIO, chunk_chars: int, tally: _Tally) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for batch in iter_record_batches(stream, chunk_chars=chunk_chars):
        # A record that failed JSON parsing was still READ: counting it on
        # both sides is what makes valid + losses == records_read hold.
        tally.records_read += batch.unparsable
        tally.unparsable_rows += batch.unparsable
        tally.truncated = tally.truncated or batch.truncated
        frame = slice_records(batch.records, tally)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=_OUT_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def _refused_quarantine_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}__refused_quarantine.parquet")


def _refuse(
    year: int,
    label: str,
    path: Path,
    tally: _Tally,
    quarantined: pd.DataFrame,
    duplicate_rows: int,
    reason: str,
) -> NoReturn:
    """Log the run, keep the evidence, write no year, raise.

    Two refusals. A file that does not END like a complete year is a cut-off
    download: half a year parses fine, and letting it replace the whole one,
    green, is the silent failure this exists for. A file with zero valid puts
    (a login page, an empty download) is the other. If rows were quarantined
    (a schema or format drift, not a bad download) they go to a SEPARATE
    ``__refused_quarantine`` file, so the quarantine beside a good year still
    belongs to that year; a refusal with nothing quarantined removes an older
    refusal's file, so whatever is on disk belongs to the latest run.
    """
    refused_path: str | None = None
    target = _refused_quarantine_path(path)
    if quarantined.empty:
        target.unlink(missing_ok=True)
    else:
        _write_atomic(quarantined, target)
        refused_path = str(target)
    log_event(
        _LOGGER,
        "ingestion.kaggle_spy.refused",
        dataset=DATASET,
        reason=reason,
        year=year,
        run_date=dt.date.today(),
        source=label,
        quarantined_rows=len(quarantined),
        records_read=tally.records_read,
        not_spy_put_rows=tally.not_spy_put_rows,
        unparsable_rows=tally.unparsable_rows,
        no_ask_rows=tally.no_ask_rows,
        duplicate_rows=duplicate_rows,
        refused_quarantine_path=refused_path,
    )
    raise ValueError(
        f"Kaggle SPY {year} from {label}: {reason}; {tally.records_read} records read "
        f"({tally.not_spy_put_rows} not SPY puts, {tally.unparsable_rows} unparsable, "
        f"{tally.no_ask_rows} no ask, {duplicate_rows} duplicate, {len(quarantined)} "
        f"quarantined{' -> ' + refused_path if refused_path else ''}) -- {path} was NOT "
        "written. Check the downloaded file, or the schema if rows were quarantined."
    )


def _refusal_reason(
    year: int, combined: pd.DataFrame, valid: pd.DataFrame, tally: _Tally
) -> str | None:
    """Why this file must not become ``year``'s parquet, or ``None``.

    A row dated outside ``year`` means the WRONG FILE (a renamed or
    mis-downloaded year): it can be perfectly well-formed, so nothing else
    here would stop it replacing the real year. A year file holds only its
    own year's sessions, so one such row refuses the whole file.
    """
    off_year = pd.to_datetime(combined["quote_date"]).dt.year != year
    if off_year.any():
        dates = combined.loc[off_year, "quote_date"]
        return (
            f"{int(off_year.sum())} of {len(combined)} puts are dated outside {year} "
            f"({dates.min().date()}..{dates.max().date()}) -- the wrong file"
        )
    if valid.empty:
        return "0 valid puts"
    if tally.truncated:
        return "the file is cut off (it does not end like a complete year)"
    return None


def ingest_kaggle_spy_year(
    year: int,
    *,
    vendor_dir: Path | None = None,
    out_dir: Path | None = None,
    source: TextIO | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> IngestResult:
    """Stream one year's file to ``parquet_path(year)``, and log the run.

    ``source`` lets tests drive the whole path off an in-memory stream.
    Re-running a year overwrites its parquet atomically: the input is the same
    file, so the output is too, and this is not the lake's immutable bronze.
    """
    tally = _Tally()
    if source is None:
        with open_year_source(vendor_dir or DEFAULT_VENDOR_DIR, year) as (stream, label):
            combined = _read_stream(stream, chunk_chars, tally)
    else:
        label = "<stream>"
        combined = _read_stream(source, chunk_chars, tally)

    # Only IDENTICAL repeats are duplicates. Two different rows under one key
    # are a conflict nobody can resolve by picking the first -- that could
    # keep the bad copy and drop the good -- so both go to quarantine via the
    # schema's unique key.
    before = len(combined)
    combined = combined.drop_duplicates(keep="first").reset_index(drop=True)
    valid, quarantined = validate_and_quarantine(combined)

    path = parquet_path(year, out_dir)
    reason = _refusal_reason(year, combined, valid, tally)
    if reason is not None:
        _refuse(year, label, path, tally, quarantined, before - len(combined), reason)
    _write_atomic(valid, path)
    _refused_quarantine_path(path).unlink(missing_ok=True)
    q_path = path.with_name(f"{path.stem}__quarantine.parquet")
    quarantine_path: str | None = None
    if quarantined.empty:
        # A clean re-run must not leave an older input's quarantine beside it.
        q_path.unlink(missing_ok=True)
    else:
        _write_atomic(quarantined, q_path)
        quarantine_path = str(q_path)

    days = sorted(valid["quote_date"].dt.date.unique())
    result = IngestResult(
        year=year,
        source=label,
        parquet_path=str(path),
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        records_read=tally.records_read,
        not_spy_put_rows=tally.not_spy_put_rows,
        unparsable_rows=tally.unparsable_rows,
        no_ask_rows=tally.no_ask_rows,
        duplicate_rows=before - len(combined),
        voided_greek_rows=int(valid["iv"].isna().sum()),
        trading_days=len(days),
        first_quote=days[0].isoformat(),
        last_quote=days[-1].isoformat(),
        iv_distinct_ratio=_iv_distinct_ratio(valid),
    )
    _log_run(result)
    return result


def _log_run(result: IngestResult) -> None:
    log_event(
        _LOGGER,
        "ingestion.kaggle_spy.run",
        dataset=DATASET,
        year=result.year,
        run_date=dt.date.today(),
        source=result.source,
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        records_read=result.records_read,
        not_spy_put_rows=result.not_spy_put_rows,
        unparsable_rows=result.unparsable_rows,
        no_ask_rows=result.no_ask_rows,
        duplicate_rows=result.duplicate_rows,
        voided_greek_rows=result.voided_greek_rows,
        trading_days=result.trading_days,
        first_quote=result.first_quote,
        last_quote=result.last_quote,
        iv_distinct_ratio=result.iv_distinct_ratio,
        parquet_path=result.parquet_path,
        quarantine_path=result.quarantine_path,
    )
