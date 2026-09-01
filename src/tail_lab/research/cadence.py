"""Live-derived options-listing cadence, falling back to the static table
(``docs/END_STATE.md`` §1.1/§1.2 -- the "options_expiry is a dangling
branch" ``AGENT_TODO.md`` item).

``contracts/options_calendar.py``'s table is hand-maintained config: real,
genuinely weekly/monthly today, but a snapshot of a slow-moving fact rather
than a measurement. ``ingestion/options_expiry.py`` + ``transforms/
options_expiry.classify_cadence`` derive the same two fields (cadence,
avg_gap_days) from what a chain actually lists. This module is the
orchestrator that was missing: read the bronze snapshot as-of a date, mirroring
``research/data_quality.py``'s point-in-time read shape, and classify it --
falling back to the static table whenever there isn't yet a usable snapshot,
so a symbol with no ``options_expiry_*`` partition (every symbol, until
``make ingest-options-expiry`` runs against prod) reads exactly as it did
before this module existed.
"""

from __future__ import annotations

import datetime as dt

from tail_lab.contracts.options_calendar import Cadence, OptionsCadence, cadence_for
from tail_lab.contracts.options_expiry import dataset_id
from tail_lab.lake.store import LakeStore
from tail_lab.transforms.options_expiry import classify_cadence

#: Label/detail shown once a symbol's cadence comes from the listed chain
#: rather than the hand-maintained table -- mirrors
#: ``contracts/options_calendar._cadence``'s pair, tagged live so the two
#: provenances are never visually confused.
_LIVE_LABELS: dict[Cadence, tuple[str, str]] = {
    "weekly": ("Weeklies (live)", "Weekly + monthly expiries, derived from the listed chain"),
    "monthly": (
        "Monthlies (live)",
        "Third-Friday monthlies, thin weeklies, derived from the listed chain",
    ),
}


def resolve_cadence(store: LakeStore, symbol: str, *, as_of: dt.date) -> OptionsCadence:
    """The listing cadence for ``symbol`` as of ``as_of``.

    Live-derived from the forward-collected expiry snapshot when one exists
    and carries enough near-term expirations to classify; otherwise the
    hand-maintained fallback (``contracts.options_calendar.cadence_for``).
    Never raises -- an unsnapshotted symbol is exactly as answerable as it
    was before this function existed.
    """
    static = cadence_for(symbol)
    try:
        bronze = store.read_bronze_as_of(dataset_id(symbol), as_of)
    except LookupError:
        return static
    if bronze.empty:
        return static
    try:
        cadence, avg_gap_days = classify_cadence(bronze["expiration_date"], as_of=as_of)
    except ValueError:
        return static

    label, detail = _LIVE_LABELS[cadence]
    return static.model_copy(
        update={"cadence": cadence, "avg_gap_days": avg_gap_days, "label": label, "detail": detail}
    )
