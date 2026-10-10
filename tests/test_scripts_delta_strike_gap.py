"""The offline strike-gap study's arithmetic (scripts/delta_strike_gap.py).

Its numbers go into ADR 0029 and the PR body as the reason "0.10 delta at
realised vol" is labelled so; a market strike read off the vendor's delta, or
an unreachable target counted as reached, would make that case on a wrong
number.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.backtest.strike_rule import put_delta


def _load() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "delta_strike_gap.py"
    spec = importlib.util.spec_from_file_location("delta_strike_gap", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["delta_strike_gap"] = module
    spec.loader.exec_module(module)
    return module


gap = _load()


def _day(iv: float, deepest: float = 0.60, dte: int = 30) -> pd.DataFrame:
    strikes = np.arange(100 * deepest, 102.0, 0.5)
    return pd.DataFrame(
        {
            "expiration": pd.Timestamp("2020-03-31"),
            "strike": strikes,
            "spot": 100.0,
            "iv": iv,
            "dte": dte,
            # Deliberately wrong vendor deltas: the study must never read them.
            "delta": -0.5,
        }
    )


def test_the_market_strike_is_our_delta_on_the_vendor_iv() -> None:
    pct, reachable = gap.market_strike_pct(_day(0.25), 0.10, 0.015)
    assert reachable
    k = 100 * (1 - pct / 100)
    d = -put_delta(spot=100.0, strike=k, sigma=0.25, t_years=30 / 365, r=gap.RATE, q=0.015)
    for other in (k - 0.5, k + 0.5):
        o = -put_delta(spot=100.0, strike=other, sigma=0.25, t_years=30 / 365, r=gap.RATE, q=0.015)
        assert abs(o - 0.10) >= abs(d - 0.10)


def test_a_target_below_the_panel_is_unreachable_not_rounded_to_its_edge() -> None:
    # Crisis IV with the panel cut at 0.95 moneyness: even the deepest listed
    # put is far above 0.05 delta.
    pct, reachable = gap.market_strike_pct(_day(0.9, deepest=0.95), 0.05, 0.015)
    assert (pct, reachable) == (None, False)


def test_no_expiry_far_enough_is_no_answer_but_not_unreachable() -> None:
    assert gap.market_strike_pct(_day(0.25, dte=10), 0.10, 0.015) == (None, True)


def test_gap_rows_use_realised_vol_and_the_platform_regime_bands() -> None:
    idx = pd.bdate_range("2020-01-01", periods=40)
    rng = np.random.default_rng(1)
    spot = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 40))), index=idx)
    day = idx[-1]
    vix = pd.Series([12.0, 20.0, 40.0], index=[day, idx[0], idx[1]])
    rows = gap.gap_rows({day: _day(0.25)}, spot, vix, q=0.015, targets=(0.10,))
    assert len(rows) == 1 and rows[0].regime == "calm"
    assert [gap.regime_of(v) for v in (16.9, 17.0, 27.9, 28.0)] == [
        "calm",
        "elevated",
        "elevated",
        "crisis",
    ]
    # The model side is ByDelta on the realised vol, so it sits closer to spot
    # than the market's 25% IV puts it in a calm 1%-a-day series.
    assert rows[0].model_pct < rows[0].market_pct  # type: ignore[operator]


def test_the_market_expiry_is_the_first_four_weeks_out_as_in_the_preview() -> None:
    # The served preview takes the first expiry on or after the tenor; the
    # study must answer the same question, capped at 35 days so a monthly-only
    # chain never compares a 7-week option with a 4-week model.
    first, later = _day(0.25, dte=30), _day(0.40, dte=33)
    later["expiration"] = pd.Timestamp("2020-04-30")
    both, _ = gap.market_strike_pct(pd.concat([later, first]), 0.10, 0.015)
    assert both == gap.market_strike_pct(first, 0.10, 0.015)[0]
    assert gap.market_strike_pct(_day(0.25, dte=27), 0.10, 0.015) == (None, True)
    assert gap.market_strike_pct(_day(0.25, dte=36), 0.10, 0.015) == (None, True)
    assert gap.market_strike_pct(_day(0.25, dte=28), 0.10, 0.015)[0] is not None


def test_unreachable_is_exactly_the_deepest_iv_bearing_put_missing_the_target() -> None:
    day = _day(0.9, deepest=0.95)
    t = 30 / 365
    deepest = -put_delta(spot=100.0, strike=95.0, sigma=0.9, t_years=t, r=gap.RATE, q=0.015)
    assert gap.market_strike_pct(day, deepest + 1e-6, 0.015)[1] is True
    assert gap.market_strike_pct(day, deepest - 1e-6, 0.015) == (None, False)
    # A deeper strike WITHOUT an implied vol does not make the target reachable.
    blank = day.copy()
    blank.loc[len(blank)] = {**blank.iloc[0].to_dict(), "strike": 60.0, "iv": float("nan")}
    assert gap.market_strike_pct(blank, deepest - 1e-6, 0.015) == (None, False)


def test_a_split_name_is_refused() -> None:
    with pytest.raises(SystemExit):
        gap.main(["nvda"])


def test_make_with_no_names_runs_every_symbol(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `make delta-strike-gap` passes no names: that is "all", and with no
    # corpus present it skips with a message -- it must not be refused.
    assert gap.main(["--vendor", str(tmp_path)]) == 0
    assert "SKIPPED" in capsys.readouterr().out


def test_the_report_counts_every_day_and_gaps_only_answered_ones() -> None:
    day1, day2 = pd.Timestamp("2020-01-02"), pd.Timestamp("2020-02-03")
    rows = [
        gap.GapRow(day1, 0.10, "calm", 2.0, 5.0, True),
        gap.GapRow(day2, 0.10, "calm", 4.0, None, False),  # unreachable that day
    ]
    lines = gap.report("spy", rows).splitlines()
    line = next(row for row in lines if row.strip().startswith("0.10"))
    _, _, days, answered, unreach, model, market, gap_pp = line.split()
    # model% is over every day (2 and 4 -> 3.00); the gap is that day's own 5 - 2.
    assert (days, answered, unreach) == ("2", "1", "1")
    assert (float(model), float(market), float(gap_pp)) == (3.0, 5.0, 3.0)


def test_the_study_clamps_realised_vol_like_the_backtest() -> None:
    from tail_lab.research.backtest.put_roll import REALIZED_VOL_FLOOR
    from tail_lab.research.backtest.strike_rule import ByDelta

    idx = pd.bdate_range("2020-01-01", periods=40)
    spot = pd.Series(100.0 * (1 + 1e-7 * np.arange(40)), index=idx)
    day = idx[-1]
    rows = gap.gap_rows(
        {day: _day(0.25)}, spot, pd.Series([12.0], index=[day]), q=0.015, targets=(0.10,)
    )
    k = ByDelta(0.10).strike(
        spot=float(spot[day]), sigma=REALIZED_VOL_FLOOR, t_years=gap.MODEL_T, r=gap.RATE, q=0.015
    )
    assert rows[0].model_pct == pytest.approx((1 - k / float(spot[day])) * 100)
