"""Tests for downside capture (``research/metrics/downside_capture.py``)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.metrics.downside_capture import downside_capture


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=pd.RangeIndex(len(vals)))


def test_amplifying_the_benchmark_pins_the_ratio() -> None:
    """downside_capture(2*bench, bench) == 2.0: an asset that exactly doubles
    the benchmark on down-days captures 200% of the decline."""
    rng = np.random.default_rng(0)
    bench = _series([*rng.normal(-0.01, 0.02, size=250), -0.15, -0.12])
    fragile = _series([2.0 * b for b in bench])
    assert downside_capture(fragile, bench) == pytest.approx(2.0)


def test_defensive_name_captures_less() -> None:
    """A name that only falls 0.3x as much as the benchmark on down-days
    captures 30% of the decline."""
    rng = np.random.default_rng(0)
    bench = _series([*rng.normal(-0.01, 0.02, size=250), -0.15, -0.12])
    defensive = _series([0.3 * b for b in bench])
    assert downside_capture(defensive, bench) == pytest.approx(0.3)


def test_fragile_name_scores_higher_than_defensive() -> None:
    rng = np.random.default_rng(0)
    bench = _series([*rng.normal(-0.01, 0.02, size=250), -0.15, -0.12])
    fragile = _series([2.0 * b for b in bench])
    defensive = _series([0.3 * b for b in bench])
    assert downside_capture(fragile, bench) > downside_capture(defensive, bench)


def test_validation() -> None:
    x = _series([0.01, -0.02, 0.03, -0.01, 0.02, 0.01, -0.03, 0.02])
    with pytest.raises(ValueError, match="same"):
        downside_capture(x, x.iloc[:-1])
    with pytest.raises(ValueError, match="at least"):
        downside_capture(_series([0.1, 0.2]), _series([0.1, 0.2]))

    # No down-days at all.
    all_up = _series([0.01, 0.02, 0.03, 0.01, 0.02, 0.01, 0.03, 0.02])
    with pytest.raises(ValueError, match="down-days"):
        downside_capture(all_up, all_up)

    # Only 1 down-day -- below the floor of 2 needed at all.
    one_down = _series([0.01, 0.02, 0.03, 0.01, 0.02, 0.01, 0.03, -0.02])
    with pytest.raises(ValueError, match="down-days"):
        downside_capture(one_down, one_down)

    # The "zero mean down-day return" guard (mirrors the other metrics'
    # zero-variance guards) is not reachable via realistic float64 data --
    # every selected value is strictly negative, so their mean can only hit
    # exactly 0.0 through subnormal-float underflow -- so it's left
    # untested here and marked `# pragma: no cover` in the implementation.
