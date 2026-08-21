"""Bronze -> derived listing-cadence classification for the options-expiry
dataset (``docs/END_STATE.md`` §1.1/§1.2, ``AGENT_TODO.md``'s "live
options-expiry cadence adapter" item).

Pure by design, mirroring ``transforms/vix.py``: takes the raw expiration
dates a chain lists (``ingestion/options_expiry.py``'s bronze rows) and
returns a cadence classification. Nothing here touches the filesystem, the
network, or a ``LakeStore`` -- orchestration (reading bronze as-of, calling
this, and eventually replacing ``contracts/options_calendar.py``'s
hand-maintained table) is a follow-up increment (``AGENT_TODO.md``), left to
``research`` once it exists.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from tail_lab.contracts.options_calendar import Cadence

#: Only expirations within this many days of ``as_of`` count toward the
#: cadence classification -- a chain's far-dated LEAPS expiries are sparse
#: by nature (quarterly/annual) and would drag a genuinely weekly chain's
#: average gap toward "monthly" if included; the question that matters for
#: an OOM-put screen over ``docs/END_STATE.md`` §1.2's short-tenor
#: candidates is how densely the *near* chain is listed.
NEAR_TERM_DAYS = 90

#: A near-term average gap at or below this many days is classified
#: "weekly" (multiple overlapping weekly + monthly expiries in the front
#: months); above it, "monthly" (only monthly/quarterly listings). Chosen
#: as the midpoint between a genuinely weekly chain's near-term average gap
#: (observed ~3-7 days on real Yahoo data for SPY/QQQ) and a monthly-only
#: chain's (a calendar month, ~30 days) -- see the pinned test cases.
WEEKLY_THRESHOLD_DAYS = 10.0


def classify_cadence(
    expiration_dates: pd.Series,
    *,
    as_of: dt.date,
    near_term_days: int = NEAR_TERM_DAYS,
    weekly_threshold_days: float = WEEKLY_THRESHOLD_DAYS,
) -> tuple[Cadence, float]:
    """Classify a chain's listing cadence from its raw expiration dates.

    ``expiration_dates`` is the bronze/silver ``expiration_date`` column
    (pandas Timestamps) for one symbol. Restricts to the expirations within
    ``near_term_days`` of ``as_of`` (inclusive, non-negative offsets only --
    an already-lapsed date in a stale snapshot is not "near-term"), computes
    the average calendar-day gap between consecutive listed dates in that
    window, and classifies "weekly" when that average is at or below
    ``weekly_threshold_days``, else "monthly".

    Returns ``(cadence, avg_gap_days)`` -- the same two fields
    ``contracts/options_calendar.py``'s hand-maintained ``OptionsCadence``
    table carries today, so a future increment can swap the static table for
    this live-derived pair without changing the shape callers read.

    Raises ``ValueError`` if fewer than 2 near-term expirations are
    available -- a gap needs at least two dates to compute.
    """
    as_of_ts = pd.Timestamp(as_of)
    horizon_ts = as_of_ts + pd.Timedelta(days=near_term_days)
    dates = pd.Series(expiration_dates)
    near = dates[(dates >= as_of_ts) & (dates <= horizon_ts)].sort_values().drop_duplicates()

    if len(near) < 2:
        raise ValueError(
            f"only {len(near)} expirations within {near_term_days} days of "
            f"{as_of.isoformat()}; need at least 2 to compute a listing cadence"
        )

    avg_gap_days = float(near.diff().dropna().dt.days.mean())
    cadence: Cadence = "weekly" if avg_gap_days <= weekly_threshold_days else "monthly"
    return cadence, avg_gap_days
