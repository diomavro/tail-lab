"""Point-in-time market-regime timeline from the volatility complex, widened
by credit spreads when they are available (`docs/END_STATE.md` §1.3 --
"volatility complex + credit spreads + rates").

``label_vix_series``/``label_credit_series`` are pure (a level series in,
regime labels out); ``compute_regime_timeline`` reads the bronze snapshots
known on or before ``as_of`` and labels every VIX date, escalated by credit
stress where HY OAS history exists, the union of the credit partitions
plus the archive (`combine_regime_labels`). The memory
layer (phase 2) maps each backtest cycle's entry date onto this timeline to
key its verdict by regime.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.credit import (
    ARCHIVE_DATASET,
    IGNORED_BRONZE_PARTITIONS,
    resolve_credit_history,
)
from tail_lab.contracts.credit import DATASET as CREDIT_DATASET
from tail_lab.contracts.regime import (
    RegimeLabel,
    classify_credit_series,
    classify_vix_series,
    combine_regime_labels,
)
from tail_lab.lake.store import BronzeSnapshotMissing, LakeStore

_LOGGER = logging.getLogger(__name__)

VIX_DATASET = "vix"

#: The FRED series id read out of the shared ``credit`` bronze dataset for
#: the widened regime classifier -- HY OAS only (`docs/DATA_CONTRACTS.md`
#: #4); IG OAS lands in the same dataset but is not used here.
HY_OAS_SERIES_ID = "BAMLH0A0HYM2"

#: How long after its ``obs_date`` an HY OAS print can first be acted on. ICE
#: computes day *t*'s spread after the close and FRED posts it the next
#: morning, yet every ``BAMLH0A0HYM2`` row in the lake carries
#: ``vintage_date == obs_date`` (measured 2026-10-30) -- so the vintage column cannot be trusted to
#: say when a print was known. One calendar day moves day *t*'s print onto the
#: first VIX session after *t* (Friday's print onto Monday), the earliest
#: close at which anyone had it.
CREDIT_PUBLICATION_LAG = pd.Timedelta(days=1)


def label_vix_series(vix_close: pd.Series) -> pd.Series:
    """Regime label for each VIX close, preserving the input index.

    Uses the hysteresis classifier (`contracts/regime.classify_vix_series`),
    not the bare level thresholds: verdicts are keyed by regime, so boundary
    chatter is not cosmetic here (`docs/PRIOR_ART.md` §8). Still causal, so
    the point-in-time invariant is untouched.
    """
    labels = classify_vix_series(vix_close.to_numpy().tolist())
    return pd.Series(labels, index=vix_close.index, name="regime")


def label_credit_series(hy_oas: pd.Series) -> pd.Series:
    """Regime label for each HY OAS print, preserving the input index. Same
    shape as :func:`label_vix_series`, over the credit hysteresis classifier."""
    labels = classify_credit_series(hy_oas.to_numpy().tolist())
    return pd.Series(labels, index=hy_oas.index, name="credit_regime")


def load_vix_close(store: LakeStore, *, as_of: dt.date) -> pd.Series:
    """Date-indexed VIX close series known as of ``as_of``.

    Reads only the bronze VIX known on or before ``as_of``
    (:meth:`LakeStore.read_bronze_as_of` enforces no-look-ahead), sorted and
    de-duplicated by date. Raises ``LookupError`` if no VIX snapshot exists as
    of that date. The raw series behind :func:`compute_regime_timeline`'s
    labels, for callers (e.g. the vol-beta fragility metric) that need the
    level itself rather than the classified regime.
    """
    try:
        bronze = store.read_bronze_as_of(VIX_DATASET, as_of)
    except LookupError as exc:
        raise LookupError(f"no VIX known as of {as_of.isoformat()}") from exc
    if bronze.empty:
        raise LookupError(f"no VIX known as of {as_of.isoformat()}")

    ordered = bronze.sort_values("date").drop_duplicates(subset="date", keep="last")
    return pd.Series(
        ordered["close"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(ordered["date"]),
        name="close",
    )


class NoCreditHistory(LookupError):
    """Neither ``credit`` nor ``credit_archive`` holds any HY OAS as of the
    date. The one case in which the regime falls back to VIX alone."""


class CreditHistoryError(RuntimeError):
    """HY history exists but its archive/live seam is broken: the first live
    obs after the archive's end is missing or more than
    :data:`SEAM_MAX_GAP` late. Deliberately NOT a ``LookupError`` -- that would
    become a 404 or a silent VIX-only fallback, hiding a data fault; it
    surfaces as a 500 plus the error log."""


#: The widest gap tolerated between the archive's end and the first live obs.
#: Above the largest observed gap between consecutive HY obs (4 days).
SEAM_MAX_GAP = pd.Timedelta(days=7)

#: A print counts for this many business days after its publication date.
CREDIT_MAX_STALE_BUSINESS_DAYS = 10

_HISTORY_COLUMNS = ["obs_date", "value", "vintage_date"]


def _read_hy_history(
    store: LakeStore,
    dataset: str,
    as_of: dt.date,
    ignored: frozenset[tuple[str, dt.date]],
) -> pd.DataFrame:
    """Every eligible HY row of ``dataset``; an empty frame when the dataset
    has no eligible partition. Only that absence is swallowed -- any other
    store error (``KeyError`` included) propagates."""
    try:
        return store.read_bronze_history_as_of(
            dataset,
            as_of,
            columns=_HISTORY_COLUMNS,
            equals={"series_id": HY_OAS_SERIES_ID},
            ignored=ignored,
        )
    except BronzeSnapshotMissing:
        return pd.DataFrame(columns=[*_HISTORY_COLUMNS, "ingest_date"])


def _check_seam(series: pd.Series, archive_end: pd.Timestamp) -> None:
    after = series.index[series.index > archive_end]
    if len(after) == 0:
        raise CreditHistoryError(
            f"{HY_OAS_SERIES_ID}: archive ends {archive_end.date()} but no live obs follow it"
        )
    if after.min() - archive_end > SEAM_MAX_GAP:
        raise CreditHistoryError(
            f"{HY_OAS_SERIES_ID}: first live obs {after.min().date()} is more than "
            f"{SEAM_MAX_GAP.days} days after the archive's end {archive_end.date()}"
        )


def load_credit_oas(
    store: LakeStore,
    *,
    as_of: dt.date,
    ignored: frozenset[tuple[str, dt.date]] = IGNORED_BRONZE_PARTITIONS,
) -> pd.Series:
    """Date-indexed HY OAS series known as of ``as_of``, one value per ``obs_date``.

    Reads EVERY ``credit`` partition ingested on or before ``as_of`` (each is
    FRED's sliding window, so the newest alone loses history) plus the one-off
    ``credit_archive`` (`docs/adr/0033`), minus ``ignored`` -- keyed
    ``(dataset, ingest_date)`` and defaulting to
    ``contracts.credit.IGNORED_BRONZE_PARTITIONS``. The archive is
    authoritative for ``obs_date <= archive_end`` (its own maximum), live after
    it; within each, the newest partition wins
    (:func:`contracts.credit.resolve_credit_history`). An ``as_of`` before the
    archive's ingest date sees no archive, and live serves every obs.

    Raises :class:`NoCreditHistory` (a ``LookupError``) when neither dataset
    holds HY, and :class:`CreditHistoryError` when an archive exists but no
    live obs follows its end within :data:`SEAM_MAX_GAP`. Any other store error
    propagates.
    """
    live = _read_hy_history(store, CREDIT_DATASET, as_of, ignored)
    archive = _read_hy_history(store, ARCHIVE_DATASET, as_of, ignored)
    if live.empty and archive.empty:
        raise NoCreditHistory(
            f"no credit history for {HY_OAS_SERIES_ID} known as of {as_of.isoformat()}"
        )
    series = resolve_credit_history(live, archive)
    if not archive.empty:
        _check_seam(series, pd.Timestamp(archive["obs_date"].max()))
    return series


def _align_credit(
    credit_labels: pd.Series, vix_index: pd.DatetimeIndex
) -> tuple[pd.Series, pd.Series]:
    """Each VIX date's credit label, and its staleness in calendar days.

    A print counts from its publication date ``p = obs_date +``
    :data:`CREDIT_PUBLICATION_LAG`: the latest print with ``p <= d`` applies
    only while ``np.busday_count(p, d)`` is within
    :data:`CREDIT_MAX_STALE_BUSINESS_DAYS`. A date with no print yet, or only a
    stale one, gets ``"calm"`` (no opinion: VIX decides). The staleness series
    is NaN where no print was ever available and positive where a print went
    stale, so callers can tell a source hole from the start of history.
    """
    published = (credit_labels.index + CREDIT_PUBLICATION_LAG).to_numpy().astype("datetime64[D]")
    days = vix_index.to_numpy().astype("datetime64[D]")
    pos = np.searchsorted(published, days, side="right") - 1
    has_print = pos >= 0
    safe = np.where(has_print, pos, 0)
    busy = np.busday_count(published[safe], days)
    live = has_print & (busy <= CREDIT_MAX_STALE_BUSINESS_DAYS)
    labels = np.where(live, credit_labels.to_numpy()[safe], "calm")
    obs = credit_labels.index.to_numpy().astype("datetime64[D]")
    stale_days = np.where(
        has_print & ~live, (days - obs[safe]).astype("timedelta64[D]").astype(float), np.nan
    )
    return (
        pd.Series(labels, index=vix_index),
        pd.Series(stale_days, index=vix_index),
    )


def compute_regime_timeline(store: LakeStore, *, as_of: dt.date) -> pd.Series:
    """Date-indexed regime labels from the VIX snapshot known as of
    ``as_of``, escalated by credit-spread stress wherever HY OAS history (the union of
    the credit partitions plus the archive) is available (`combine_regime_labels`: credit can only make a day
    look more severe, never less).

    Reads only bronze known on or before ``as_of`` (no look-ahead), and a
    credit print only counts from its publication date
    (:data:`CREDIT_PUBLICATION_LAG`) for
    :data:`CREDIT_MAX_STALE_BUSINESS_DAYS` business days; after that credit has
    no opinion, VIX decides, and one ``event=regime.credit_stale`` warning is
    logged per call. Falls back to the VIX-only label when
    :class:`NoCreditHistory` (neither ``credit`` nor ``credit_archive`` holds
    HY; same graceful-fallback precedent `research/cadence.py` follows for the
    options-expiry dataset). Only that exception is caught: a broken
    archive/live seam raises :class:`CreditHistoryError`, and a store ``KeyError``
    propagates. Raises ``LookupError`` if no VIX snapshot exists as of that
    date -- VIX is the mandatory half, credit is an optional escalation.
    """
    vix_labels = label_vix_series(load_vix_close(store, as_of=as_of))
    try:
        credit = load_credit_oas(store, as_of=as_of)
    except NoCreditHistory:
        return vix_labels

    aligned, stale_days = _align_credit(
        label_credit_series(credit), pd.DatetimeIndex(vix_labels.index)
    )
    if stale_days.notna().any():
        _LOGGER.warning(
            "event=regime.credit_stale staleness_days=%d dates=%d",
            int(stale_days.max()),
            int(stale_days.notna().sum()),
        )

    # combine_regime_labels is variadic but only two labels are passed today
    # (VIX, credit); docs/END_STATE.md §1.3 names rates as the third planned
    # input to this same widened regime view, which is why it isn't `(v, c)`
    # positional params instead.
    combined = [combine_regime_labels(v, c) for v, c in zip(vix_labels, aligned, strict=True)]
    return pd.Series(combined, index=vix_labels.index, name="regime")


def regime_on_or_before(timeline: pd.Series, when: dt.date) -> RegimeLabel:
    """The regime in force on ``when`` — the label of the latest timeline date
    that is on or before it (markets are closed on weekends/holidays, so an
    arbitrary calendar date needs the last *trading* day's regime).

    Raises ``LookupError`` if ``when`` predates the whole timeline.
    """
    ts = pd.Timestamp(when)
    eligible = timeline.loc[timeline.index <= ts]
    if eligible.empty:
        raise LookupError(f"{when.isoformat()} predates the regime timeline")
    label: RegimeLabel = eligible.iloc[-1]
    return label


class RegimeSegment(BaseModel):
    """A contiguous run of one regime on the timeline."""

    regime: RegimeLabel
    start: dt.date
    end: dt.date
    n_days: int


class RegimeTimelineView(BaseModel):
    """The regime history for the cockpit's regime panel."""

    as_of: dt.date
    current: RegimeLabel
    segments: list[RegimeSegment]
    day_counts: dict[str, int]


def regime_segments(timeline: pd.Series) -> list[RegimeSegment]:
    """Run-length-encode a date-indexed regime series into contiguous
    segments (a compact form for drawing the panel's bands)."""
    segments: list[RegimeSegment] = []
    dates = [d.date() if isinstance(d, pd.Timestamp) else d for d in timeline.index]
    labels = list(timeline.to_numpy())
    if not labels:
        return segments
    start_i = 0
    for i in range(1, len(labels) + 1):
        if i < len(labels) and labels[i] == labels[start_i]:
            continue
        segments.append(
            RegimeSegment(
                regime=labels[start_i],
                start=dates[start_i],
                end=dates[i - 1],
                n_days=i - start_i,
            )
        )
        start_i = i
    return segments


def compute_regime_view(store: LakeStore, *, as_of: dt.date) -> RegimeTimelineView:
    """The regime panel view as of ``as_of`` — the current regime, the
    run-length-encoded history, and per-regime day counts."""
    timeline = compute_regime_timeline(store, as_of=as_of)
    segments = regime_segments(timeline)
    counts: dict[str, int] = {}
    for seg in segments:
        counts[seg.regime] = counts.get(seg.regime, 0) + seg.n_days
    return RegimeTimelineView(
        as_of=as_of,
        current=segments[-1].regime,
        segments=segments,
        day_counts=counts,
    )
