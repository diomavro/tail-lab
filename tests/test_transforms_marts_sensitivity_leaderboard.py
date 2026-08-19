"""Tests for the pure gold-mart ranking function
(``transforms/marts/sensitivity_leaderboard.py``)."""

from __future__ import annotations

import pandas as pd
import pytest

from tail_lab.transforms.marts.sensitivity_leaderboard import build_gold


def test_build_gold_ranks_most_sensitive_first() -> None:
    scores = pd.DataFrame({"symbol": ["AAA", "BBB", "CCC"], "score": [0.5, 1.8, 1.2]})
    gold = build_gold(scores, metric="downside_beta")
    assert list(gold.columns) == ["rank", "symbol", "score", "metric"]
    assert gold["symbol"].tolist() == ["BBB", "CCC", "AAA"]
    assert gold["rank"].tolist() == [1, 2, 3]
    assert (gold["metric"] == "downside_beta").all()


def test_build_gold_ties_keep_input_order() -> None:
    scores = pd.DataFrame({"symbol": ["AAA", "BBB", "CCC"], "score": [1.0, 2.0, 1.0]})
    gold = build_gold(scores, metric="downside_beta")
    # BBB (2.0) ranks first; the two 1.0-score ties keep AAA-before-CCC.
    assert gold["symbol"].tolist() == ["BBB", "AAA", "CCC"]


def test_build_gold_rejects_missing_columns() -> None:
    with pytest.raises(ValueError, match="missing required column"):
        build_gold(pd.DataFrame({"symbol": ["AAA"]}), metric="downside_beta")


def test_build_gold_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="at least one row"):
        build_gold(pd.DataFrame({"symbol": [], "score": []}), metric="downside_beta")
