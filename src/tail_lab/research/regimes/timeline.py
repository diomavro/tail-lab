"""Point-in-time market-regime timeline from the volatility complex, widened
by credit spreads when they are available (`docs/END_STATE.md` §1.3 --
"volatility complex + credit spreads + rates").

``label_vix_series``/``label_credit_series`` are pure (a level series in,
regime labels out); ``compute_regime_timeline`` reads the bronze snapshots
known on or before ``as_of`` and labels every VIX date, escalated by credit
stress where a credit snapshot exists (`combine_regime_labels`). The memory
layer (phase 2) maps each backtest cycle's entry date onto this timeline to
key its verdict by regime.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.credit import DATASET as CREDIT_DATASET
from tail_lab.contracts.regime import (
    RegimeLabel,
    classify_credit_series,
    classify_vix_series,
    combine_regime_labels,
)
from tail_lab.lake.store import LakeStore

VIX_DATASET = "vix"

#: The FRED series id read out of the shared ``credit`` bronze dataset for
#: the widened regime classifier -- HY OAS only (`docs/DATA_CONTRACTS.md`
#: #4); IG OAS lands in the same dataset but is not used here.
HY_OAS_SERIES_ID = "BAMLH0A0HYM2"


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


def load_credit_oas(store: LakeStore, *, as_of: dt.date) -> pd.Series:
    """Date-indexed HY OAS series known as of ``as_of``, one point-in-time
    value per ``obs_date``.

    Reads the bronze credit snapshot known on or before ``as_of``
    (:meth:`LakeStore.read_bronze_as_of` enforces no-look-ahead), then
    resolves FRED's ALFRED vintage history to the value actually known at
    that time: for each ``obs_date``, keeps the row with the latest
    ``vintage_date`` (`docs/DATA_CONTRACTS.md` #4). No further filtering is
    needed -- every ``vintage_date`` in the resolved partition was already
    published on or before that partition's own ``ingest_date``, which is
    itself <= ``as_of``, so "latest vintage in the partition" already means
    "latest vintage known as of the query date". Raises ``LookupError`` if no
    credit snapshot, or no HY OAS row within it, exists as of that date.
    """
    try:
        bronze = store.read_bronze_as_of(CREDIT_DATASET, as_of)
    except LookupError as exc:
        raise LookupError(f"no credit snapshot known as of {as_of.isoformat()}") from exc

    hy = bronze.loc[bronze["series_id"] == HY_OAS_SERIES_ID]
    if hy.empty:
        raise LookupError(f"no {HY_OAS_SERIES_ID} known as of {as_of.isoformat()}")

    latest_vintage = hy.sort_values("vintage_date").drop_duplicates(subset="obs_date", keep="last")
    ordered = latest_vintage.sort_values("obs_date")
    return pd.Series(
        ordered["value"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(ordered["obs_date"]),
        name="hy_oas",
    )


def compute_regime_timeline(store: LakeStore, *, as_of: dt.date) -> pd.Series:
    """Date-indexed regime labels from the VIX snapshot known as of
    ``as_of``, escalated by credit-spread stress wherever a credit snapshot
    is also available (`combine_regime_labels`: credit can only make a day
    look more severe, never less).

    Reads only bronze known on or before ``as_of``
    (:meth:`LakeStore.read_bronze_as_of` enforces no-look-ahead). Falls back
    to the VIX-only label whenever no credit snapshot exists yet -- as of
    this writing, no environment has ever run `make ingest-credit`, so this
    is the live behaviour everywhere today, and becomes credit-aware with no
    further code change the moment a credit partition exists (same
    graceful-fallback precedent `research/cadence.py` follows for the
    options-expiry dataset). Raises ``LookupError`` if no VIX snapshot exists
    as of that date -- VIX is the mandatory half, credit is an optional
    escalation.
    """
    vix_labels = label_vix_series(load_vix_close(store, as_of=as_of))
    try:
        credit = load_credit_oas(store, as_of=as_of)
    except LookupError:
        return vix_labels

    credit_labels = label_credit_series(credit)
    # ffill: a VIX date's credit opinion is whatever HY OAS last printed on or
    # before it -- still causal, since only values dated <= that VIX date
    # (already <= as_of, per load_credit_oas) are ever used. A VIX date
    # earlier than the first known credit print has no opinion yet; treat
    # that as "calm" (the least severe) so combination stays total.
    aligned = credit_labels.reindex(vix_labels.index, method="ffill").fillna("calm")

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
