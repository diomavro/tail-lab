from __future__ import annotations

import pytest

from tail_lab.research.backtest.growth_summary import (
    annualize_total_return,
    summarise_growth,
)


def test_annualizes_a_total_return() -> None:
    assert annualize_total_return(0.21, 2.0) == pytest.approx(0.1, abs=1e-9)


def test_a_total_loss_annualizes_to_minus_one() -> None:
    assert annualize_total_return(-1.0, 3.0) == -1.0


def test_zero_years_returns_the_total_unchanged() -> None:
    assert annualize_total_return(0.5, 0.0) == 0.5


def test_summary_line_reads_as_expected() -> None:
    assert summarise_growth(0.21, 2.0) == "10.0%/yr over 2y (total 21.0%)"
