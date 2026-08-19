"""Gold mart: the sensitivity leaderboard (``docs/END_STATE.md`` §1.1) —
turns already-computed per-asset sensitivity scores into the ranked table
the cockpit's leaderboard panel reads: most-sensitive first, with an
explicit rank column.

Pure by design, like the other ``transforms/`` modules: a DataFrame of
scores in, a ranked DataFrame out — no lake or network access. Computing the
score itself (e.g. downside beta) is a research-layer concern
(``research/metrics/downside_beta.py``); ``transforms/`` sits *below*
``research`` in the machine-checked dependency direction (``ARCHITECTURE.md``,
``pyproject.toml``'s import-linter contract) and must never import it, so
the orchestration that computes scores and then calls this mart lives in
``research/leaderboard.py`` — mirroring the ``research/vix_stretch.py``
split between pure transform and orchestration.
"""

from __future__ import annotations

import pandas as pd

#: Columns ``build_gold`` requires on its input.
REQUIRED_COLUMNS = ("symbol", "score")

#: Column order of the ranked gold table ``build_gold`` returns.
GOLD_COLUMNS = ("rank", "symbol", "score", "metric")


def build_gold(scores: pd.DataFrame, *, metric: str) -> pd.DataFrame:
    """Rank ``scores`` (columns ``symbol``, ``score``) most-sensitive first.

    Adds a 1-based ``rank`` column (higher score = better rank) and a
    ``metric`` column naming which sensitivity metric produced the scores,
    so a leaderboard that later blends multiple metrics can tell rows apart.
    Ties keep the input's relative order (stable sort) rather than an
    arbitrary tiebreak.

    Raises ``ValueError`` if ``scores`` is missing a required column or has
    no rows — an empty leaderboard is a caller bug (the orchestrator should
    have raised ``LookupError`` before reaching here), not a valid gold
    table.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in scores.columns]
    if missing:
        raise ValueError(f"scores is missing required column(s): {missing}")
    if scores.empty:
        raise ValueError("scores must have at least one row to rank")

    out = scores.sort_values("score", ascending=False, kind="stable").reset_index(drop=True)
    out["rank"] = out.index + 1
    out["metric"] = metric
    return out[list(GOLD_COLUMNS)]
