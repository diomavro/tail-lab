"""Tests for the hypothesis memory (``memory/store.py``, ``contracts/hypothesis.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tail_lab.contracts.hypothesis import RuleSpec
from tail_lab.lake.blob_store import BlobStore
from tail_lab.memory.store import HypothesisMemory


def _mem(tmp_path: Path) -> HypothesisMemory:
    return HypothesisMemory(BlobStore(tmp_path))


def _rec(mem: HypothesisMemory, spec: RuleSpec, regime: str, roi: float, run_id: str = "r1"):
    return mem.record(
        spec,
        regime,
        roi_on_premium=roi,
        n_cycles=50,
        hit_rate=0.2,
        biggest_payoff_mult=9.9,
        run_id=run_id,
    )


# ---------- rule hash canonicalization ----------


def test_equivalent_specs_collide_on_hash() -> None:
    a = RuleSpec(asset="SPY", moneyness_pct=5, tenor_weeks=4, lookback_years=4)
    b = RuleSpec(asset=" spy ", moneyness_pct=5.0, tenor_weeks=4.00, lookback_years=4)
    assert a.rule_hash() == b.rule_hash()
    assert a.rule_hash().startswith("h-")


def test_different_specs_do_not_collide() -> None:
    base = RuleSpec(asset="spy", moneyness_pct=5, tenor_weeks=4, lookback_years=4)
    assert (
        base.rule_hash()
        != RuleSpec(asset="qqq", moneyness_pct=5, tenor_weeks=4, lookback_years=4).rule_hash()
    )
    assert (
        base.rule_hash()
        != RuleSpec(asset="spy", moneyness_pct=10, tenor_weeks=4, lookback_years=4).rule_hash()
    )


# ---------- record / lookup / run_count ----------


def test_record_then_lookup_roundtrips(tmp_path: Path) -> None:
    mem = _mem(tmp_path)
    spec = RuleSpec(asset="spy", moneyness_pct=5, tenor_weeks=4, lookback_years=4)
    _rec(mem, spec, "crisis", -0.8)
    got = mem.lookup(spec.rule_hash(), "crisis")
    assert got is not None
    assert got.rule_hash == spec.rule_hash()
    assert got.regime == "crisis"
    assert got.roi_on_premium == pytest.approx(-0.8)
    assert got.run_count == 1
    assert not got.paid_off


def test_lookup_unknown_pair_is_none(tmp_path: Path) -> None:
    mem = _mem(tmp_path)
    assert mem.lookup("h-deadbeef", "calm") is None


def test_repeat_record_bumps_run_count_not_a_new_node(tmp_path: Path) -> None:
    mem = _mem(tmp_path)
    spec = RuleSpec(asset="spy", moneyness_pct=5, tenor_weeks=4, lookback_years=4)
    first = _rec(mem, spec, "crisis", 0.2, run_id="run-1")
    second = _rec(mem, spec, "crisis", 0.3, run_id="run-2")
    assert first.run_count == 1
    assert second.run_count == 2
    assert second.first_seen == first.first_seen  # same node, not a fresh one
    assert second.last_run_id == "run-2"
    assert len(mem.outcomes_for_rule(spec.rule_hash())) == 1  # still one (rule, regime) node


# ---------- the crux: regime_only != confirmed ----------


def test_verdict_regime_only_when_paid_in_one_regime(tmp_path: Path) -> None:
    mem = _mem(tmp_path)
    spec = RuleSpec(asset="spy", moneyness_pct=10, tenor_weeks=12, lookback_years=4)
    _rec(mem, spec, "crisis", 0.5)  # paid off
    _rec(mem, spec, "calm", -0.9)  # bled
    assert mem.verdict_for_rule(spec.rule_hash()) == "regime_only"


def test_verdict_confirmed_when_paid_across_regimes(tmp_path: Path) -> None:
    mem = _mem(tmp_path)
    spec = RuleSpec(asset="tsla", moneyness_pct=10, tenor_weeks=4, lookback_years=4)
    _rec(mem, spec, "crisis", 0.5)
    _rec(mem, spec, "elevated", 0.1)
    assert mem.verdict_for_rule(spec.rule_hash()) == "confirmed"


def test_verdict_failed_when_never_paid(tmp_path: Path) -> None:
    mem = _mem(tmp_path)
    spec = RuleSpec(asset="spy", moneyness_pct=5, tenor_weeks=4, lookback_years=4)
    _rec(mem, spec, "calm", -0.8)
    _rec(mem, spec, "crisis", -0.4)
    assert mem.verdict_for_rule(spec.rule_hash()) == "failed"


def test_verdict_untested_for_unknown_rule(tmp_path: Path) -> None:
    assert _mem(tmp_path).verdict_for_rule("h-nope") == "untested"


def test_negative_results_are_retained(tmp_path: Path) -> None:
    """Failures are first-class — never pruned from the retrieval path."""
    mem = _mem(tmp_path)
    spec = RuleSpec(asset="spy", moneyness_pct=5, tenor_weeks=4, lookback_years=4)
    _rec(mem, spec, "calm", -0.8)
    assert len(mem.all_outcomes()) == 1
    assert mem.all_outcomes()[0].roi_on_premium < 0


# ---------- DuckDB coverage rollup ----------


def test_coverage_by_regime_rolls_up(tmp_path: Path) -> None:
    mem = _mem(tmp_path)
    s1 = RuleSpec(asset="spy", moneyness_pct=5, tenor_weeks=4, lookback_years=4)
    s2 = RuleSpec(asset="qqq", moneyness_pct=5, tenor_weeks=4, lookback_years=4)
    _rec(mem, s1, "crisis", 0.5)
    _rec(mem, s2, "crisis", -0.2)
    _rec(mem, s1, "calm", -0.9)
    cov = mem.coverage_by_regime()
    crisis = cov[cov["regime"] == "crisis"].iloc[0]
    assert int(crisis["rules"]) == 2
    assert int(crisis["paid_off"]) == 1
    assert {"regime", "rules", "paid_off", "total_runs", "avg_roi"} <= set(cov.columns)


def test_coverage_empty_when_no_records(tmp_path: Path) -> None:
    cov = _mem(tmp_path).coverage_by_regime()
    assert list(cov.columns) == ["regime", "rules", "paid_off", "total_runs", "avg_roi"]
    assert len(cov) == 0
