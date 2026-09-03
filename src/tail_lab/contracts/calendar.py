"""Expiration-calendar arithmetic, shared by every layer that needs a date.

A LEAF module: it imports nothing else from ``tail_lab`` (enforced by the
import-linter layers contract in ``pyproject.toml``). That is the entire reason
it exists here rather than anywhere more obvious.

``third_friday`` was implemented twice — once in
``research/backtest/index_replication.py`` for the Cboe programs' roll dates,
and again as ``_third_friday`` in ``ingestion/vix_futures.py`` for VX futures
settlement. Two spellings of one rule, and the same rule: the US equity-index
option expiration.

The obvious de-duplication is unavailable. ``ingestion`` may not import
``research`` — imports point downward — so the ingestion adapter could not have
reused the existing function even had its author noticed it. Duplication was
the *only* thing the layering allowed, which is exactly when a shared leaf is
the answer rather than a convenience: put the rule where both sides may legally
reach it.

Found by the adversarial design review on PR #73.
"""

from __future__ import annotations

import datetime as dt

__all__ = ["third_friday"]


def third_friday(year: int, month: int) -> dt.date:
    """The third Friday of ``year``-``month``.

    The standard US equity-index option expiration: the roll date of the Cboe
    programs this platform replicates, and the settlement date of the VX
    futures it ingests.

    Scanning days 1-28 is deliberate and sufficient: the third Friday can never
    fall later than the 21st, so no month-length special-casing is needed and
    February is not a special case.

    **No holiday adjustment.** When the third Friday is an exchange holiday the
    real settlement moves, and this returns the nominal date. Callers that care
    are expected to fail loudly on the resulting miss rather than silently
    accept a neighbouring day — see the note in ``ingestion/vix_futures.py``.
    """
    fridays = [
        d
        for d in (dt.date(year, month, day) for day in range(1, 29))
        if d.weekday() == 4  # Monday is 0
    ]
    return fridays[2]
