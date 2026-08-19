"""Market-regime timeline (``docs/END_STATE.md`` §1.3).

Orchestration that turns the point-in-time VIX snapshot into a per-date
regime label. Pure classification lives in ``contracts.regime``; reading the
lake as-of a simulation date lives here (same split as ``research/vix_stretch``).
"""
