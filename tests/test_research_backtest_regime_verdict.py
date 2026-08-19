"""Tests for the Put Lab live regime breakdown
(``research/backtest/regime_verdict.py``)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.hypothesis import RuleSpec
from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.put_roll import PutRollCycle
from tail_lab.research.backtest.regime_verdict import (
    compute_regime_verdict,
    regime_breakdown,
)


def _cycle(entry: dt.date, net: float, notional: float = 1000.0) -> PutRollCycle:
    # premium recovered by the breakdown as payoff - net, so payoff = notional + net.
    payoff = notional + net
    return PutRollCycle(
        entry_date=entry,
        expiry_date=entry,
        spot=100.0,
        strike=95.0,
        sigma=0.3,
        premium=10.0,
        contracts=100.0,
        payoff=payoff,
        net=net,
    )


def _timeline(mapping: dict[str, str]) -> pd.Series:
    idx = pd.to_datetime(list(mapping))
    return pd.Series(list(mapping.values()), index=idx)


def test_regime_only_when_paid_in_one_regime() -> None:
    timeline = _timeline({"2022-01-03": "crisis", "2022-01-04": "calm"})
    cycles = [
        _cycle(dt.date(2022, 1, 3), net=+800),  # crisis: net-pays
        _cycle(dt.date(2022, 1, 4), net=-900),  # calm: bleeds
    ]
    slices, verdict = regime_breakdown(cycles, timeline)
    assert verdict == "regime_only"
    by = {s.regime: s for s in slices}
    assert by["crisis"].paid_off is True
    assert by["calm"].paid_off is False
    # premium per cycle is the notional (1000); crisis roi = +800/1000.
    assert by["crisis"].roi_on_premium == pytest.approx(0.8)


def test_confirmed_when_paid_across_two_regimes() -> None:
    timeline = _timeline({"2022-01-03": "crisis", "2022-01-04": "elevated"})
    cycles = [_cycle(dt.date(2022, 1, 3), +500), _cycle(dt.date(2022, 1, 4), +100)]
    _, verdict = regime_breakdown(cycles, timeline)
    assert verdict == "confirmed"


def test_failed_when_never_pays() -> None:
    timeline = _timeline({"2022-01-03": "crisis", "2022-01-04": "calm"})
    cycles = [_cycle(dt.date(2022, 1, 3), -500), _cycle(dt.date(2022, 1, 4), -900)]
    _, verdict = regime_breakdown(cycles, timeline)
    assert verdict == "failed"


def test_cycle_predating_timeline_is_skipped_and_untested_if_all() -> None:
    timeline = _timeline({"2022-06-01": "calm"})
    slices, verdict = regime_breakdown([_cycle(dt.date(2020, 1, 1), +500)], timeline)
    assert slices == []
    assert verdict == "untested"


def test_slices_are_stress_ordered() -> None:
    timeline = _timeline({"2022-01-03": "crisis", "2022-01-04": "calm", "2022-01-05": "elevated"})
    cycles = [
        _cycle(dt.date(2022, 1, 3), +1),
        _cycle(dt.date(2022, 1, 4), +1),
        _cycle(dt.date(2022, 1, 5), +1),
    ]
    slices, _ = regime_breakdown(cycles, timeline)
    assert [s.regime for s in slices] == ["calm", "elevated", "crisis"]


# ---------- orchestration ----------


def _seed(store: DeltaLakeStore, ingest: dt.date, n: int = 300) -> None:
    rng = np.random.default_rng(5)
    closes = np.clip(100 + np.cumsum(rng.normal(0, 1.0, size=n)), 20, None)
    dates = pd.date_range(end=ingest, periods=n, freq="B")
    ohlcv = pd.DataFrame(
        {
            "symbol": "SPY",
            "trade_date": dates,
            "open": closes,
            "high": closes * 1.002,
            "low": closes * 0.998,
            "close": closes,
            "volume": np.full(n, 1_000_000, dtype=int),
            "adj_close": closes,
        }
    )
    store.write_bronze(dataset_id("spy"), ingest, ohlcv)
    # VIX spanning the same dates, oscillating across regime thresholds.
    vix = 14 + 12 * (np.sin(np.linspace(0, 12, n)) + 1)  # ~14..38 -> calm/elevated/crisis
    store.write_bronze("vix", ingest, pd.DataFrame({"date": dates, "close": vix}))


def test_compute_regime_verdict_end_to_end(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    _seed(store, ingest)
    rv = compute_regime_verdict(
        store,
        asset="spy",
        as_of=ingest,
        notional=1000.0,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
        years=1.0,
    )
    assert rv.asset == "spy"
    assert rv.verdict in {"confirmed", "regime_only", "failed", "untested"}
    assert (
        rv.rule_hash
        == RuleSpec(asset="spy", moneyness_pct=5.0, tenor_weeks=4.0, lookback_years=1).rule_hash()
    )
    # Cycles span multiple regimes given the oscillating VIX, so we get slices.
    assert rv.slices
    assert {s.regime for s in rv.slices} <= {"calm", "elevated", "crisis"}


def test_compute_regime_verdict_missing_vix_raises(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2026, 3, 2)
    rng = np.random.default_rng(1)
    n = 300
    closes = np.clip(100 + np.cumsum(rng.normal(0, 1.0, size=n)), 20, None)
    dates = pd.date_range(end=ingest, periods=n, freq="B")
    store.write_bronze(
        dataset_id("spy"),
        ingest,
        pd.DataFrame(
            {
                "symbol": "SPY",
                "trade_date": dates,
                "open": closes,
                "high": closes * 1.002,
                "low": closes * 0.998,
                "close": closes,
                "volume": np.full(n, 1_000_000, dtype=int),
                "adj_close": closes,
            }
        ),
    )  # OHLCV but no VIX
    with pytest.raises(LookupError, match="no VIX"):
        compute_regime_verdict(
            store,
            asset="spy",
            as_of=ingest,
            notional=1000.0,
            moneyness_pct=5.0,
            tenor_weeks=4.0,
            years=1.0,
        )
