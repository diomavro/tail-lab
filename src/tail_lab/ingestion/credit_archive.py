"""One-off HY OAS history ingest from a pinned Wayback capture
(`docs/adr/0034`, `docs/DATA_CONTRACTS.md` #4).

FRED cut ``BAMLH0A0HYM2`` to a sliding 3-year window in April 2026, so the
live ``credit`` dataset cannot reach 2008, 2011, 2016 or 2020. The last
capture of the full history is a Wayback snapshot of 2025-11-04. This module
fetches exactly that capture, checks its decoded sha256, validates it, checks
it against the live series, and writes it ONCE as bronze ``credit_archive``.

It accepts only the pinned ``(ARCHIVE_URL, ARCHIVE_SHA256)`` pair: the URL is
not a parameter, and the hash parameter exists only as a private test seam.
Ingesting any other snapshot means changing the constants -- a reviewed code
change. The data is ICE's: it is never committed and never served (only the
regime labels derived from it are).

Human-run once after merge (``make ingest-credit-archive``; licence confirmed by Dio 2026-10-31 (internal use only; values never served publicly)); not
in CI, not scheduled.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.credit import (
    ARCHIVE_DATASET,
    ARCHIVE_SHA256,
    ARCHIVE_URL,
    ARCHIVE_VINTAGE,
    DATASET,
    HY_SERIES_ID,
    IGNORED_BRONZE_PARTITIONS,
    CreditSchema,
    resolve_credit_history,
)
from tail_lab.lake.store import BronzeSnapshotMissing, LakeStore
from tail_lab.observability import log_event

__all__ = [
    "ArchiveIngestResult",
    "CreditArchiveRefused",
    "RefusalReason",
    "ingest_credit_archive",
]

_LOGGER = logging.getLogger(__name__)

RefusalReason = Literal[
    "same_day_partition",
    "hash_mismatch",
    "invalid_rows",
    "no_live_hy",
    "thin_overlap",
    "overlap_mismatch",
]

#: The gate compares the archive with live HY over the obs they share.
MIN_OVERLAP_OBS = 250
MAX_OVERLAP_DIFF = 0.005

#: What the CSV uses for "no print": an empty cell, or FRED's "." sentinel.
_BLANKS = frozenset({"", "."})

_FETCH_TIMEOUT_S = 60.0


class CreditArchiveRefused(RuntimeError):
    """The archive ingest refused to write; ``reason`` says which check."""

    def __init__(self, reason: RefusalReason, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason: RefusalReason = reason


@dataclass(frozen=True)
class ArchiveIngestResult:
    """Outcome of one run: what was written, or that it was already done."""

    skipped: bool
    rows: int
    dropped_blank: int
    bronze_path: str | None


def _parse_csv(content: bytes) -> tuple[pd.DataFrame, int]:
    """The CSV as ``(frame, blanks_dropped)``. Only an empty cell or ``.`` is a
    blank; any other non-numeric text survives as NaN so validation refuses."""
    raw = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
    if raw.shape[1] != 2:
        raise CreditArchiveRefused("invalid_rows", f"expected 2 columns, got {raw.shape[1]}")
    dates, values = raw.iloc[:, 0], raw.iloc[:, 1].str.strip()
    present = ~values.isin(_BLANKS)
    frame = pd.DataFrame(
        {
            "series_id": HY_SERIES_ID,
            "obs_date": pd.to_datetime(dates[present], errors="coerce"),
            "value": pd.to_numeric(values[present], errors="coerce"),
            "vintage_date": pd.Timestamp(ARCHIVE_VINTAGE),
        }
    ).reset_index(drop=True)
    return frame, int((~present).sum())


def _validate(frame: pd.DataFrame) -> pd.DataFrame:
    try:
        valid = CreditSchema.validate(frame, lazy=True)
    except SchemaErrors as err:
        raise CreditArchiveRefused(
            "invalid_rows", f"{len(err.failure_cases)} failure(s); nothing written"
        ) from err
    # A capture cannot hold an observation dated on or after its own capture day
    # (that day's print is posted the next morning).
    late = int((valid["obs_date"] >= pd.Timestamp(ARCHIVE_VINTAGE)).sum())
    if late:
        raise CreditArchiveRefused(
            "invalid_rows", f"{late} obs dated on or after the capture date {ARCHIVE_VINTAGE}"
        )
    return valid


def _live_hy(
    store: LakeStore, today: dt.date, ignored: frozenset[tuple[str, dt.date]]
) -> pd.Series:
    try:
        live = store.read_bronze_history_as_of(
            DATASET,
            today,
            columns=["obs_date", "value", "vintage_date"],
            equals={"series_id": HY_SERIES_ID},
            ignored=ignored,
        )
    except BronzeSnapshotMissing as exc:
        raise CreditArchiveRefused("no_live_hy", "no live credit partition") from exc
    if live.empty:
        raise CreditArchiveRefused("no_live_hy", "live credit has no HY rows")
    return resolve_credit_history(live, live.iloc[0:0])


def _overlap_gate(archive: pd.DataFrame, live: pd.Series) -> tuple[int, float]:
    """Refuse unless archive and live agree on enough shared obs."""
    mine = archive.set_index("obs_date")["value"]
    shared = mine.index.intersection(live.index)
    if len(shared) < MIN_OVERLAP_OBS:
        raise CreditArchiveRefused(
            "thin_overlap", f"{len(shared)} shared obs, need {MIN_OVERLAP_OBS}"
        )
    worst = float((mine.loc[shared] - live.loc[shared]).abs().max())
    if worst > MAX_OVERLAP_DIFF:
        raise CreditArchiveRefused(
            "overlap_mismatch", f"max |diff| {worst:.4f} over {len(shared)} shared obs"
        )
    return len(shared), worst


def ingest_credit_archive(
    store: LakeStore,
    *,
    get: Callable[..., requests.Response] = requests.get,
    today: dt.date | None = None,
    ignored: frozenset[tuple[str, dt.date]] = IGNORED_BRONZE_PARTITIONS,
    _expected_sha256: str = ARCHIVE_SHA256,
) -> ArchiveIngestResult:
    """Fetch, verify and write the pinned archive, once. See the module docstring.

    Steps, in order: refuse a same-day partition; skip if a non-ignored
    archive partition exists; fetch (``raise_for_status`` before hashing);
    hash; drop blanks; validate (any bad row refuses everything); gate on
    agreement with live HY; write. Raises :class:`CreditArchiveRefused`.
    """
    day = today or dt.datetime.now(dt.UTC).date()
    # A same-day write would be a silent no-op (bronze is immutable), so it is
    # refused -- ignored or not, and before the "already done" skip.
    if store.bronze_partition_exists(ARCHIVE_DATASET, day):
        raise CreditArchiveRefused(
            "same_day_partition", f"{ARCHIVE_DATASET} already has a partition for {day}"
        )
    existing = [
        d for d in store.bronze_ingest_dates(ARCHIVE_DATASET) if (ARCHIVE_DATASET, d) not in ignored
    ]
    if existing:
        log_event(_LOGGER, "ingest.credit_archive.skip", existing=existing[-1].isoformat())
        return ArchiveIngestResult(skipped=True, rows=0, dropped_blank=0, bronze_path=None)

    response = get(ARCHIVE_URL, timeout=_FETCH_TIMEOUT_S)
    response.raise_for_status()  # Wayback's 429 is an HTML page, not a hash problem
    digest = hashlib.sha256(response.content).hexdigest()
    if digest != _expected_sha256:
        raise CreditArchiveRefused("hash_mismatch", f"got {digest}")

    frame, dropped = _parse_csv(response.content)
    valid = _validate(frame)
    overlap, worst = _overlap_gate(valid, _live_hy(store, day, ignored))
    bronze_path = store.write_bronze(ARCHIVE_DATASET, day, valid)
    log_event(
        _LOGGER,
        "ingest.credit_archive",
        dataset=ARCHIVE_DATASET,
        ingest_date=day.isoformat(),
        rows=len(valid),
        dropped_blank=dropped,
        overlap_obs=overlap,
        overlap_max_diff=worst,
        first_obs_date=str(valid["obs_date"].min().date()),
        last_obs_date=str(valid["obs_date"].max().date()),
        sha256=digest,
        bronze_path=bronze_path,
    )
    return ArchiveIngestResult(
        skipped=False, rows=len(valid), dropped_blank=dropped, bronze_path=bronze_path
    )
