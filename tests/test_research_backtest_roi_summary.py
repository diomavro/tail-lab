"""Probe tests -- see the module docstring in `research/backtest/roi_summary.py`."""

from __future__ import annotations

import pytest

from tail_lab.research.backtest.roi_summary import annualise_roi, describe_roi


def test_annualise_roi_compounds_back_to_the_total() -> None:
    rate = annualise_roi(0.44, 2.0)
    assert (1.0 + rate) ** 2.0 == pytest.approx(1.44)


def test_a_total_loss_annualises_to_minus_one() -> None:
    assert annualise_roi(-1.0, 3.0) == -1.0


def test_describe_roi_states_both_the_rate_and_the_total() -> None:
    assert describe_roi(0.44, 2.0) == "20.0%/yr over 2y (total 44.0%)"
