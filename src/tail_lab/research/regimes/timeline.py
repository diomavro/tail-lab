"""Point-in-time market-regime timeline from the VIX complex.

``label_vix_series`` is pure (VIX closes in, regime labels out);
``compute_regime_timeline`` reads the bronze VIX snapshot known on or before
``as_of`` and labels every date in it. The memory layer (phase 2) maps each
backtest cycle's entry date onto this timeline to key its verdict by regime.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.regime import RegimeLabel, classify_vix_series
from tail_lab.lake.store import LakeStore

VIX_DATASET = "vix"


def label_vix_series(vix_close: pd.Series) -> pd.Series:
    """Regime label for each VIX close, preserving the input index.

    Uses the hysteresis classifier (`contracts/regime.classify_vix_series`),
    not the bare level thresholds: verdicts are keyed by regime, so boundary
    chatter is not cosmetic here (`docs/PRIOR_ART.md` §8). Still causal, so
    the point-in-time invariant is untouched.
    """
    labels = classify_vix_series(vix_close.to_numpy().tolist())
    return pd.Series(labels, index=vix_close.index, name="regime")


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


def compute_regime_timeline(store: LakeStore, *, as_of: dt.date) -> pd.Series:
    """Date-indexed regime labels from the VIX snapshot known as of ``as_of``.

    Reads only the bronze VIX known on or before ``as_of``
    (:meth:`LakeStore.read_bronze_as_of` enforces no-look-ahead), sorted and
    de-duplicated by date. Raises ``LookupError`` if no VIX snapshot exists as
    of that date.
    """
    return label_vix_series(load_vix_close(store, as_of=as_of))


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
