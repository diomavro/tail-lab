"""Live options-listing cadence, derived from the bronze options-expiry chain
snapshot (``docs/END_STATE.md`` §1.1/§1.2) -- the "wire it" half of the
``options_expiry`` dangling-branch decision (``AGENT_TODO.md``,
``docs/DATA_FLOW.md`` §3.1: the adapter and the pure classifier were both
built and tested, but nothing read them).

This is the orchestration point allowed to depend on both ``lake`` (to read
the bronze chain as-of a date) and ``transforms`` (the pure classifier) --
see the layering rule in ``pyproject.toml``, mirroring ``vix_stretch.py``'s
role for the VIX dataset.

Callers should fall back to ``contracts.options_calendar.cadence_for``'s
static table on ``LookupError`` (no bronze snapshot for the symbol -- true
for every symbol today, since ``make ingest-options-expiry`` has never been
run against prod) or ``ValueError`` (a snapshot exists but has fewer than two
near-term expirations to classify from) -- both mean "no live answer yet",
not a hard failure.
"""

from __future__ import annotations

import datetime as dt

from tail_lab.contracts.options_calendar import Cadence, OptionsCadence, cadence_for
from tail_lab.contracts.options_expiry import dataset_id
from tail_lab.lake.store import LakeStore
from tail_lab.transforms.options_expiry import classify_cadence

#: Label/detail pairs for a live-derived result -- distinct from the static
#: table's "Weeklies"/"Monthlies" text so a cockpit reader can tell the two
#: sources apart without cross-referencing a snapshot id.
_LIVE_LABELS: dict[Cadence, tuple[str, str]] = {
    "weekly": ("Weeklies (live)", "Weekly + monthly expiries, derived from today's chain"),
    "monthly": (
        "Monthlies (live)",
        "Third-Friday monthlies, thin weeklies, derived from today's chain",
    ),
}


def live_cadence_for(store: LakeStore, symbol: str, *, as_of: dt.date) -> OptionsCadence:
    """Derive ``symbol``'s listing cadence from its bronze expiration-date
    chain known as of ``as_of``.

    Raises ``LookupError`` if no ``options_expiry_<symbol>`` bronze snapshot
    exists on or before ``as_of`` (:meth:`LakeStore.read_bronze_as_of`
    enforces no-look-ahead), and ``ValueError`` if the snapshot has fewer
    than two near-term expirations to classify from
    (:func:`transforms.options_expiry.classify_cadence`).
    """
    bronze = store.read_bronze_as_of(dataset_id(symbol), as_of)
    cadence, avg_gap_days = classify_cadence(bronze["expiration_date"], as_of=as_of)
    label, detail = _LIVE_LABELS[cadence]
    return OptionsCadence(
        symbol=symbol.upper(),
        # The bronze chain carries no display name -- reuse the curated
        # catalogue's for it (or its flagged-default symbol echo) rather than
        # inventing a second name -> symbol mapping.
        name=cadence_for(symbol).name,
        cadence=cadence,
        avg_gap_days=avg_gap_days,
        label=label,
        detail=detail,
    )
