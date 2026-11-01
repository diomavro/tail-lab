"""Tests for the out-of-sample signal test (``research/backtest/walk_forward.py``).

Two seams. ``evaluate_screens`` is pure, so signals are PLANTED into it
(a perfect signal, a zero-noise volatility signal) and the verdict is checked
against what that planting must produce. ``collect_periods`` reads a lake, so
it is tested for the thing that makes a backtest worth anything: that nothing
after ``t`` can reach the picks at ``t`` (``docs/adr/0009``).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest import walk_forward as wf
from tail_lab.research.backtest.put_roll import (
    DEFAULT_RATE,
    PricingBasis,
    RollTerms,
    clamp_realized_vol,
    price_model_roll,
    run_put_roll,
    trailing_realized_vol,
)
from tail_lab.research.backtest.strike_rule import ByDelta, ByMoneyness, StrikeRule
from tail_lab.research.backtest.walk_forward import (
    COMPOSITE,
    SCREENS,
    VOL_BASELINES,
    Diagnostics,
    PeriodInputs,
    collect_periods,
    evaluate_screens,
    walk_forward,
)

INGEST = dt.date(2026, 3, 2)
FloatArray = np.ndarray

# ==========================================================================
# Planted-signal fixtures for the pure seam
# ==========================================================================


def _payoffs(rng: np.random.Generator, n: int, p_pay: float = 0.3) -> FloatArray:
    """Mostly zero (an OTM put expires worthless) with ~``p_pay`` of names
    paying a heavy-tailed multiple -- several payers per period, so a basket of
    ``k`` can be better or worse than another."""
    pays = rng.random(n) < p_pay
    return np.where(pays, 1.05 * (1.0 + rng.exponential(4.0, n)), 0.0)


def _make_periods(
    *,
    n_periods: int = 30,
    n_names: int = 10,
    seed: int = 0,
    payoff_fn: Callable[[np.random.Generator, int], FloatArray] | None = None,
    plant: Callable[[np.random.Generator, int, FloatArray], dict[str, FloatArray]] | None = None,
    spy_stress: set[int] | None = None,
    regime_fn: Callable[[int], str] | None = None,
) -> list[PeriodInputs]:
    """Periods with noise in every screen, then ``plant``'s overrides on top.
    SPY pays in the periods listed in ``spy_stress`` (default: four episodes)."""
    rng = np.random.default_rng(seed)
    stress = {3, 4, 10, 17, 18, 19, 25} if spy_stress is None else spy_stress
    out: list[PeriodInputs] = []
    base = dt.date(2021, 1, 4)
    for i in range(n_periods):
        pay = payoff_fn(rng, i) if payoff_fn else _payoffs(rng, n_names)
        cost = np.full(n_names, 1.05)
        signals = {s: rng.normal(size=n_names) for s in SCREENS}
        if plant:
            signals.update(plant(rng, i, pay))
        out.append(
            PeriodInputs(
                slot=i,
                date=base + dt.timedelta(days=28 * i),
                expiry_date=base + dt.timedelta(days=28 * i + 28),
                regime=(regime_fn(i) if regime_fn else "calm"),  # type: ignore[arg-type]
                symbols=tuple(f"n{j}" for j in range(n_names)),
                signals=signals,
                payoff=pay,
                cost=cost,
                spy_payoff=3.0 if i in stress else 0.0,
                spy_cost=1.05,
            )
        )
    return out


def _roi(pay: FloatArray) -> FloatArray:
    return pay / 1.05 - 1.0


def _eval(periods: list[PeriodInputs], **kw: int) -> wf.Evaluation:
    return evaluate_screens(
        periods, top_k=kw.pop("top_k", 2), n_random=kw.pop("n_random", 400), **kw
    )


def _summary(ev: wf.Evaluation, screen: str) -> wf.ScreenSummary:
    return next(s for s in ev.screens if s.screen == screen)


# ==========================================================================
# Section 1: the pure statistics
# ==========================================================================


def test_a_perfect_signal_has_unit_ic_and_beats_random() -> None:
    """The positive control: a signal equal to the realised ROI (equal cost for
    every name) must be recognised -- IC is 1 wherever it is defined, nothing
    random matches its basket, and the edge survives dropping its best period.
    If this failed the test could never certify any real signal."""
    periods = _make_periods(plant=lambda rng, i, pay: {COMPOSITE: _roi(pay)})
    ev = _eval(periods)
    comp = _summary(ev, COMPOSITE)
    ics = [p.ic for p in ev.periods if p.ic is not None]
    assert len(ics) >= 10
    assert all(ic == pytest.approx(1.0) for ic in ics)
    assert comp.mean_ic == pytest.approx(1.0)
    assert comp.ic_hit_share == 1.0
    assert comp.n_ic_periods == len(ics)
    assert comp.p_random == pytest.approx(1 / 401)  # no random draw matched it
    assert comp.excess_ex_best_period is not None and comp.excess_ex_best_period > 0
    assert ev.verdict.status == "beats_random"
    assert ev.verdict.vol_baseline_as_good is False


@pytest.mark.parametrize("seed", range(5))
def test_pure_noise_does_not_beat_random(seed: int) -> None:
    """Smoke test on fixed seeds, not a calibration claim: a universe with
    heterogeneous payoff odds (some names pay far more often, like high-vol
    names at the floor-adjacent end) and signals that are pure noise must not
    reach ``beats_random``."""
    rng = np.random.default_rng(100 + seed)
    odds = np.linspace(0.02, 0.5, 12)  # heterogeneous vol, incl. near-never-paying names

    def pay(r: np.random.Generator, _i: int) -> FloatArray:
        return np.where(r.random(12) < odds, 1.05 * (1.0 + r.exponential(4.0, 12)), 0.0)

    del rng
    periods = _make_periods(n_names=12, seed=seed, payoff_fn=pay)
    ev = _eval(periods)
    assert ev.verdict.status != "beats_random"


@pytest.mark.parametrize("direction", ["high", "low"])
def test_a_planted_volatility_signal_sets_the_vol_baseline_flag(direction: str) -> None:
    """The point of the baselines: if the 'fragility' signal IS plain
    volatility (planted with zero noise, either direction), the result must say
    a plain-volatility screen did as well -- and still print the baseline's
    number, so a near-tie can never hide behind the flag."""
    sign = 1.0 if direction == "high" else -1.0

    def vol_pay(rng: np.random.Generator, _i: int) -> FloatArray:
        return _payoffs(rng, 10)

    def plant(rng: np.random.Generator, i: int, pay: FloatArray) -> dict[str, FloatArray]:
        # vol is what drives who pays: higher (or lower) vol -> pays
        vol = rng.normal(size=10)
        pay[:] = np.where(sign * vol > 0.4, 1.05 * 6.0, 0.0)
        measure = {"rv20": vol, "rv_long": vol, "rv_ratio": vol}
        sig: dict[str, FloatArray] = {}
        for m, v in measure.items():
            sig[f"{m}_high"] = v
            sig[f"{m}_low"] = -v
        sig[COMPOSITE] = sign * vol
        return sig

    ev = _eval(_make_periods(payoff_fn=vol_pay, plant=plant))
    comp = _summary(ev, COMPOSITE)
    twin = _summary(ev, f"rv20_{direction}")
    assert twin.excess_vs_universe == pytest.approx(comp.excess_vs_universe)
    assert ev.verdict.vol_baseline_as_good is True
    assert "a plain-volatility screen did as well" in ev.verdict.sentence
    assert "picked after the fact" in ev.verdict.sentence
    assert f"{ev.verdict.best_vol_baseline_excess * 100:+.1f}%" in ev.verdict.sentence  # type: ignore[operator]


def test_a_vol_independent_signal_clears_the_flag_and_the_sentence_says_so() -> None:
    """The negative control for the flag: the composite equals ROI, volatility
    is unrelated to who pays. First assert the premise (volatility picks pay
    strictly less than the composite's, and more than k names pay somewhere),
    then that the flag is clear and the sentence still prints the best
    baseline's number."""
    periods = _make_periods(plant=lambda rng, i, pay: {COMPOSITE: _roi(pay)})
    assert any((p.payoff > 0).sum() > 2 for p in periods)  # >k payers somewhere
    ev = _eval(periods)
    comp = _summary(ev, COMPOSITE)
    best = max((_summary(ev, b) for b in VOL_BASELINES), key=lambda s: s.excess_vs_universe)
    assert best.excess_vs_universe < comp.excess_vs_universe  # the strict gap
    v = ev.verdict
    assert v.vol_baseline_as_good is False
    assert v.status == "beats_random"
    assert "and better than any plain-volatility screen" in v.sentence
    assert f"{best.excess_vs_universe * 100:+.1f}%" in v.sentence
    assert v.best_vol_baseline == best.screen
    assert wf.BEST_BASELINE_LABEL in v.sentence  # always labelled as hindsight


def test_p_random_is_seeded_bounded_and_reproducible() -> None:
    periods = _make_periods(plant=lambda rng, i, pay: {COMPOSITE: _roi(pay)})
    a = _eval(periods, seed=7)
    b = _eval(periods, seed=7)
    c = _eval(periods, seed=8, n_random=50)
    assert [s.p_random for s in a.screens] == [s.p_random for s in b.screens]
    assert [p.band_low for p in a.periods] == [p.band_low for p in b.periods]
    for s in [*a.screens, *c.screens]:
        assert 0.0 < s.p_random <= 1.0
    assert min(s.p_random for s in c.screens) >= 1 / 51  # +1 smoothing floor
    for p in a.periods:
        assert p.band_low <= p.band_high


def test_episodes_come_from_the_spy_payoff() -> None:
    periods = _make_periods(n_periods=12, spy_stress={1, 2, 3, 6, 11})
    ev = _eval(periods)
    assert [(e.start, e.n_periods) for e in ev.episodes] == [
        (periods[1].date, 3),
        (periods[6].date, 1),
        (periods[11].date, 1),
    ]
    assert ev.episodes[0].end == periods[3].expiry_date
    assert ev.n_episodes == 3
    assert [p.stress for p in ev.periods] == [i in {1, 2, 3, 6, 11} for i in range(12)]


def test_a_sat_out_slot_splits_a_stress_run_into_two_episodes() -> None:
    """Episodes are runs on the REBALANCE calendar. Periods at slots 0, 1, 3, 4
    are all stressed, but slot 2 sat out: nobody observed it, so two stress
    runs are two episodes, not one merged by the kept-period list."""
    base = _make_periods(n_periods=4, spy_stress={0, 1, 2, 3})
    gap = [
        dataclasses.replace(per, slot=slot) for per, slot in zip(base, [0, 1, 3, 4], strict=True)
    ]
    ev = _eval(gap)
    assert [(e.start, e.n_periods) for e in ev.episodes] == [(gap[0].date, 2), (gap[2].date, 2)]
    assert ev.n_episodes == 2
    consecutive = _eval(base)
    assert [(e.start, e.n_periods) for e in consecutive.episodes] == [(base[0].date, 4)]


def test_ties_are_broken_by_the_seeded_rng_not_by_input_order() -> None:
    """A constant composite ties every name. Names arrive in symbol order, so
    "first wins" would always pick n0 and n1; the seeded stream must spread the
    picks, reproducibly for one seed and differently for another."""
    periods = _make_periods(
        n_periods=40, n_names=10, plant=lambda rng, i, pay: {COMPOSITE: np.zeros(10)}
    )

    def picks(seed: int) -> list[tuple[str, ...]]:
        return [tuple(p.top_k_assets) for p in _eval(periods, seed=seed).periods]

    assert picks(3) == picks(3)
    assert picks(3) != picks(4)
    assert {a for chosen in picks(3) for a in chosen} == {f"n{j}" for j in range(10)}
    # a real winner is never overridden by the tie-break
    best = _make_periods(
        n_periods=5, n_names=10, plant=lambda rng, i, pay: {COMPOSITE: np.arange(10.0)}
    )
    assert all(set(p.top_k_assets) == {"n9", "n8"} for p in _eval(best, seed=9).periods)


def test_totals_include_the_spread() -> None:
    """ROI is payoff / (notional + spread) - 1: with cost 1.2 per name and a
    single payer of 6, the basket total is 6/2.4 - 1, not 6/2 - 1."""
    sig = np.array([1.0, 0.0])
    zeros = {s: sig for s in SCREENS}
    p0 = PeriodInputs(
        slot=0,
        date=dt.date(2022, 1, 3),
        expiry_date=dt.date(2022, 1, 31),
        regime="calm",
        symbols=("a", "b"),
        signals=zeros,
        payoff=np.array([6.0, 0.0]),
        cost=np.array([1.2, 1.2]),
        spy_payoff=0.0,
        spy_cost=1.2,
    )
    ev = evaluate_screens([p0], top_k=1, n_random=10)
    s = _summary(ev, COMPOSITE)
    assert s.basket_total_roi == pytest.approx(6.0 / 1.2 - 1.0)
    assert ev.universe_total_roi == pytest.approx(6.0 / 2.4 - 1.0)
    assert ev.spy_total_roi == pytest.approx(-1.0)
    assert s.excess_vs_universe == pytest.approx(4.0 - 1.5)
    assert s.excess_vs_spy == pytest.approx(4.0 + 1.0)


def _hand_periods() -> list[PeriodInputs]:
    """k = 1, three names, unit costs. Composite picks name 2 each time.
    payoffs: [0,0,10], [0,0,0], [0,2,0] -> basket pays 10, 0, 0;
    universe pays 10, 0, 2."""
    rows = [np.array([0.0, 0.0, 10.0]), np.zeros(3), np.array([0.0, 2.0, 0.0])]
    out = []
    for i, pay in enumerate(rows):
        sig = np.array([0.0, 0.5, 1.0])
        out.append(
            PeriodInputs(
                slot=i,
                date=dt.date(2022, 1, 3) + dt.timedelta(days=28 * i),
                expiry_date=dt.date(2022, 1, 31) + dt.timedelta(days=28 * i),
                regime="calm",
                symbols=("a", "b", "c"),
                signals=dict.fromkeys(SCREENS, sig),
                payoff=pay,
                cost=np.ones(3),
                spy_payoff=0.0,
                spy_cost=1.0,
            )
        )
    return out


def test_ex_best_values_are_pinned_by_hand() -> None:
    """Dropping the best PERIOD (period 0, basket payoff 10) leaves basket
    0/2 - 1 = -1 against universe 2/6 - 1 = -2/3: excess -1/3. Dropping the
    best NAME-PERIOD (payoff 10, cost 1) from both: basket 0/2 - 1 = -1,
    universe 2/8 - 1 = -0.75: excess -0.25. The headline excess is
    (10/3 - 1) - (12/9 - 1)."""
    s = _summary(evaluate_screens(_hand_periods(), top_k=1, n_random=20), COMPOSITE)
    assert s.excess_vs_universe == pytest.approx(10 / 3 - 12 / 9)
    assert s.excess_ex_best_period == pytest.approx(-1 / 3)
    assert s.excess_ex_best_name_period == pytest.approx(-0.25)


def test_ex_best_is_none_when_nothing_is_left() -> None:
    one = _hand_periods()[:1]
    s = _summary(evaluate_screens(one, top_k=1, n_random=10), COMPOSITE)
    assert s.excess_ex_best_period is None  # no other period to measure
    assert s.excess_ex_best_name_period is None  # k = 1: the only basket cell is the best one


def test_regime_breakdown_is_by_the_regime_at_entry() -> None:
    periods = _make_periods(
        n_periods=6,
        plant=lambda rng, i, pay: {COMPOSITE: _roi(pay)},
        regime_fn=lambda i: "calm" if i < 3 else "crisis",
    )
    s = _summary(_eval(periods), COMPOSITE)
    assert [(r.regime, r.n_periods) for r in s.excess_by_regime] == [("calm", 3), ("crisis", 3)]


# ---- verdict statuses: every one reachable ----


def test_insufficient_data_names_the_limit_that_fired() -> None:
    few = _eval(_make_periods(n_periods=10))
    assert few.verdict.status == "insufficient_data"
    assert "too_few_periods" in few.verdict.reasons
    assert "fewer than 24 periods" in few.verdict.sentence
    quiet = _eval(_make_periods(n_periods=30, spy_stress={5, 6}))
    assert quiet.verdict.reasons == ["too_few_episodes"]
    assert quiet.verdict.status == "insufficient_data"
    # the vol comparison is stated even then, neutrally
    assert "for comparison" in quiet.verdict.sentence
    assert "picked after the fact" in quiet.verdict.sentence


def test_no_test_window_when_there_are_no_periods() -> None:
    ev = evaluate_screens([], top_k=2, n_random=10)
    assert ev.verdict.status == "insufficient_data"
    assert ev.verdict.reasons == ["no_test_window"]
    assert ev.test_start is None and ev.test_end is None
    assert ev.screens == [] and ev.per_name == []
    assert ev.verdict.sentence == "Too little history to judge (no test window)."
    assert ev.universe_total_roi is None and ev.spy_total_roi is None


def test_a_worst_picking_signal_is_no_better_than_random() -> None:
    periods = _make_periods(plant=lambda rng, i, pay: {COMPOSITE: -_roi(pay)})
    ev = _eval(periods)
    assert ev.verdict.status == "no_better_than_random"
    assert "did not beat random baskets" in ev.verdict.sentence
    assert "did as well" not in ev.verdict.sentence
    assert "better than any plain-volatility" not in ev.verdict.sentence


def _crash_world() -> list[PeriodInputs]:
    """All the edge is one crash period: every name pays there (names differ,
    the composite picks the best two), and nothing pays anywhere else."""

    def pay(rng: np.random.Generator, i: int) -> FloatArray:
        if i == 12:
            return np.linspace(10.0, 120.0, 12)
        return np.zeros(12)

    def plant(rng: np.random.Generator, i: int, p: FloatArray) -> dict[str, FloatArray]:
        return {COMPOSITE: p + 0.001 * rng.normal(size=12)}

    return _make_periods(n_names=12, payoff_fn=pay, plant=plant)


def test_a_single_crash_carrying_the_edge_is_flagged_with_its_magnitude() -> None:
    ev = _eval(_crash_world())
    comp = _summary(ev, COMPOSITE)
    assert comp.p_random < 0.05 and comp.excess_vs_universe > 0  # it looks great...
    assert comp.excess_ex_best_period == pytest.approx(0.0)  # ...until the crash is dropped
    v = ev.verdict
    assert v.status == "crash_carried"
    assert "depends on its best period or best single name-period" in v.sentence
    assert "better than any plain-volatility" not in v.sentence
    assert f"{comp.excess_ex_best_period * 100:+.1f}%" in v.sentence  # type: ignore[operator]


def test_crash_carried_with_the_flag_says_a_vol_screen_did_as_well() -> None:
    def plant(rng: np.random.Generator, i: int, p: FloatArray) -> dict[str, FloatArray]:
        sig = p + 0.001 * rng.normal(size=12)
        return {COMPOSITE: sig, "rv20_high": sig}

    def pay(rng: np.random.Generator, i: int) -> FloatArray:
        return np.linspace(10.0, 120.0, 12) if i == 12 else np.zeros(12)

    ev = _eval(_make_periods(n_names=12, payoff_fn=pay, plant=plant))
    assert ev.verdict.status == "crash_carried"
    assert ev.verdict.vol_baseline_as_good
    assert "and a plain-volatility screen did as well" in ev.verdict.sentence


def _name_period_crash_world() -> list[PeriodInputs]:
    """60 names, k = 2; the composite always picks names 0 and 1.

    Period 12 is the "crash": name 0 pays 200x, and 20 OTHER names (not in the
    basket) pay 30x each. Everywhere else name 1 pays a steady 0.3.

    Dropping the whole crash PERIOD removes the 20 big universe payoffs too, so
    what is left is the basket's small steady edge: positive. Dropping only the
    single largest NAME-PERIOD (name 0 in period 12) leaves those 20 payoffs
    in the universe and takes the basket's windfall away: negative."""
    n = 60

    def pay(rng: np.random.Generator, i: int) -> FloatArray:
        out = np.zeros(n)
        if i == 12:
            out[0] = 200.0
            out[10:30] = 30.0
        else:
            out[1] = 0.3
        return out

    def plant(rng: np.random.Generator, i: int, p: FloatArray) -> dict[str, FloatArray]:
        sig = np.zeros(n)
        sig[0], sig[1] = 2.0, 1.0
        return {COMPOSITE: sig}

    return _make_periods(n_names=n, payoff_fn=pay, plant=plant)


def test_name_period_ex_best_alone_can_flag_a_crash() -> None:
    """``crash_carried`` must be reachable through the ex-best-NAME-PERIOD
    check by itself: the ex-best-PERIOD excess is positive here, so only the
    name-period test can (and must) flag it."""
    ev = _eval(_name_period_crash_world(), n_random=2000)
    comp = _summary(ev, COMPOSITE)
    assert comp.p_random < 0.05 and comp.excess_vs_universe > 0  # it looks significant...
    assert comp.excess_ex_best_period is not None and comp.excess_ex_best_period > 0
    assert comp.excess_ex_best_name_period is not None and comp.excess_ex_best_name_period < 0
    assert ev.verdict.status == "crash_carried"
    assert "depends on its best period or best single name-period" in ev.verdict.sentence
    assert f"{comp.excess_ex_best_name_period * 100:+.1f}%" in ev.verdict.sentence


def test_bh_runs_across_the_twelve_exploratory_screens_only() -> None:
    ev = _eval(_make_periods(plant=lambda rng, i, pay: {"tail_beta": _roi(pay)}))
    comp = _summary(ev, COMPOSITE)
    assert comp.p_bh is None and comp.significant_bh is None
    rest = [s for s in ev.screens if s.screen != COMPOSITE]
    assert len(rest) == 12
    assert all(s.p_bh is not None and s.significant_bh is not None for s in rest)
    assert all(s.p_bh >= s.p_random for s in rest if s.p_bh is not None)  # type: ignore[operator]
    planted = _summary(ev, "tail_beta")
    assert planted.significant_bh is True
    assert [s.screen for s in ev.screens] == list(SCREENS)


# ---- per-name descriptives ----


def _name_world() -> list[PeriodInputs]:
    """k = 1, names x / y / z, 8 periods. The composite always picks x.
    x pays in periods 0 (multiple 4) and 5 (multiple 12) of the 8 it is picked
    in; y pays once (multiple 3) in a period it is NOT picked. Regimes: 0-3
    calm, 4-7 crisis."""
    out = []
    for i in range(8):
        pay = np.zeros(3)
        if i == 0:
            pay[0] = 4.0 * 1.0
        if i == 5:
            pay[0] = 12.0 * 1.0
        if i == 2:
            pay[1] = 3.0 * 1.0
        out.append(
            PeriodInputs(
                slot=i,
                date=dt.date(2022, 1, 3) + dt.timedelta(days=28 * i),
                expiry_date=dt.date(2022, 1, 31) + dt.timedelta(days=28 * i),
                regime="calm" if i < 4 else "crisis",
                symbols=("x", "y", "z"),
                signals=dict.fromkeys(SCREENS, np.array([2.0, 1.0, 0.0])),
                payoff=pay,
                cost=np.ones(3),
                spy_payoff=0.0,
                spy_cost=1.0,
            )
        )
    return out


def test_per_name_is_descriptive_k_of_n_paid_and_largest_multiple() -> None:
    ev = evaluate_screens(_name_world(), top_k=1, n_random=10)
    rec = {r.symbol: r for r in ev.per_name}
    x = next(s for s in rec["x"].screens if s.screen == COMPOSITE)
    assert (rec["x"].n_eligible_periods, x.n_picked_periods) == (8, 8)
    assert (x.picked.n, x.picked.n_paid, x.picked.largest_payoff_multiple) == (8, 2, 12.0)
    assert x.other.n == 0 and x.other.largest_payoff_multiple is None
    y = next(s for s in rec["y"].screens if s.screen == COMPOSITE)
    assert (y.picked.n, y.other.n, y.other.n_paid, y.other.largest_payoff_multiple) == (
        0,
        8,
        1,
        3.0,
    )
    assert y.role == "composite"


def test_per_name_contribution_is_over_picked_periods_only_and_can_be_negative() -> None:
    """contribution = sum over picked periods of (ROI_it - universe_t) / k.
    x: period 0 (4-1) - mean(3,-1,-1)=1/3 -> 3 - 1/3; period 5 (11) - 3;
    period 2, where y pays 3 (ROI 2) and the universe averages 0: -1 - 0 = -1;
    the other five periods: -1 - (-1) = 0. y is never picked: contribution 0."""
    ev = evaluate_screens(_name_world(), top_k=1, n_random=10)
    rec = {r.symbol: r for r in ev.per_name}
    x = next(s for s in rec["x"].screens if s.screen == COMPOSITE)
    assert x.contribution == pytest.approx((3 - 1 / 3) + (11 - 3) + (-1 - 0))
    y = next(s for s in rec["y"].screens if s.screen == COMPOSITE)
    assert y.contribution == 0.0
    z = next(s for s in rec["z"].screens if s.screen == COMPOSITE)
    assert z.contribution == 0.0


def test_per_name_regime_breakdown_covers_all_eligible_periods() -> None:
    ev = evaluate_screens(_name_world(), top_k=1, n_random=10)
    rec = {r.symbol: r for r in ev.per_name}
    assert [(g.regime, g.n, g.n_paid, g.largest_payoff_multiple) for g in rec["x"].by_regime] == [
        ("calm", 4, 1, 4.0),
        ("crisis", 4, 1, 12.0),
    ]
    # y was never picked, yet its regime breakdown is still over its 8 periods
    assert sum(g.n for g in rec["y"].by_regime) == 8 == rec["y"].n_eligible_periods


def test_per_name_carries_no_p_values_or_averages_and_labels_the_hindsight_baseline() -> None:
    ev = evaluate_screens(_name_world(), top_k=1, n_random=10)
    fields = (
        set(wf.NameScreenRecord.model_fields)
        | set(wf.PaidGroup.model_fields)
        | set(wf.NameRecord.model_fields)
    )
    assert not any("p_" in f or "mean" in f or "median" in f or "roi" in f for f in fields)
    rec = ev.per_name[0]
    roles = {s.screen: s.role for s in rec.screens}
    best = ev.verdict.best_vol_baseline
    assert roles[COMPOSITE] == "composite"
    assert roles[best] == "best_vol_baseline"  # type: ignore[index]
    assert sum(r == "exploratory" for r in roles.values()) == 11
    assert len(rec.screens) == 13


def test_paid_means_payoff_above_cost_not_merely_positive_or_equal() -> None:
    """A payoff of 0.5x cost is a positive payoff that still LOST money; exactly
    1.0x is break-even. Neither is "paid" (the line is ``PutRollCycle.net >
    0``); only 1.5x is. The largest multiple still reports the best of all."""
    out = []
    for i, multiple in enumerate([0.5, 1.0, 1.5]):
        out.append(
            PeriodInputs(
                slot=i,
                date=dt.date(2022, 1, 3) + dt.timedelta(days=28 * i),
                expiry_date=dt.date(2022, 1, 31) + dt.timedelta(days=28 * i),
                regime="calm",
                symbols=("x", "y"),
                signals=dict.fromkeys(SCREENS, np.array([1.0, 0.0])),
                payoff=np.array([multiple, 0.0]),
                cost=np.ones(2),
                spy_payoff=0.0,
                spy_cost=1.0,
            )
        )
    ev = evaluate_screens(out, top_k=1, n_random=10)
    x = next(s for r in ev.per_name if r.symbol == "x" for s in r.screens if s.screen == COMPOSITE)
    assert (x.picked.n, x.picked.n_paid, x.picked.largest_payoff_multiple) == (3, 1, 1.5)
    rec = next(r for r in ev.per_name if r.symbol == "x")
    assert [(g.n, g.n_paid) for g in rec.by_regime] == [(3, 1)]


# ---- validation ----


def test_malformed_periods_are_refused() -> None:
    good = _make_periods(n_periods=2)
    with pytest.raises(ValueError, match="top_k and n_random"):
        evaluate_screens(good, top_k=0)
    bad_cost = PeriodInputs(**{**good[0].__dict__, "cost": np.zeros(10)})
    with pytest.raises(ValueError, match="costs must be positive"):
        evaluate_screens([bad_cost], top_k=2)
    short = PeriodInputs(**{**good[0].__dict__, "payoff": np.zeros(3)})
    with pytest.raises(ValueError, match="must match symbols"):
        evaluate_screens([short], top_k=2)
    sigs = dict(good[0].signals)
    sigs[COMPOSITE] = np.full(10, np.nan)
    nan = PeriodInputs(**{**good[0].__dict__, "signals": sigs})
    with pytest.raises(ValueError, match="non-finite"):
        evaluate_screens([nan], top_k=2)
    del sigs[COMPOSITE]
    missing = PeriodInputs(**{**good[0].__dict__, "signals": sigs})
    with pytest.raises(ValueError, match="missing"):
        evaluate_screens([missing], top_k=2)


def test_ic_is_none_when_roi_is_constant_or_the_signal_is() -> None:
    """Common case: no name paid, every ROI is -1. A correlation there is
    undefined, not zero."""
    zero = _make_periods(n_periods=3, payoff_fn=lambda rng, i: np.zeros(10))
    assert all(p.ic is None for p in _eval(zero).periods)
    flat = _make_periods(n_periods=3, plant=lambda rng, i, pay: {COMPOSITE: np.ones(10)}, seed=3)
    assert all(p.ic is None for p in _eval(flat).periods)
    tiny = _make_periods(n_periods=2, n_names=2)
    assert all(p.ic is None for p in evaluate_screens(tiny, top_k=1, n_random=10).periods)


# ---- properties ----


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(
    n_periods=st.integers(1, 30),
    n_names=st.integers(2, 8),
    top_k=st.integers(1, 4),
    seed=st.integers(0, 10_000),
    p_pay=st.floats(0.0, 1.0),
)
def test_properties_ic_p_and_the_verdict_are_always_well_formed(
    n_periods: int, n_names: int, top_k: int, seed: int, p_pay: float
) -> None:
    """For any valid inputs: every IC is in [-1, 1], every p is in (0, 1], the
    random band is ordered, and the verdict is total (one of the four statuses,
    a non-empty sentence)."""
    periods = _make_periods(
        n_periods=n_periods,
        n_names=n_names,
        seed=seed,
        payoff_fn=lambda rng, i: _payoffs(rng, n_names, p_pay),
    )
    ev = evaluate_screens(periods, top_k=top_k, n_random=30, seed=seed)
    for p in ev.periods:
        assert p.ic is None or -1.0 <= p.ic <= 1.0
        assert p.band_low <= p.band_high + 1e-12
    for s in ev.screens:
        assert 0.0 < s.p_random <= 1.0
        assert s.p_bh is None or 0.0 < s.p_bh <= 1.0
        assert s.mean_ic is None or -1.0 <= s.mean_ic <= 1.0
    assert ev.verdict.status in {
        "insufficient_data",
        "no_better_than_random",
        "crash_carried",
        "beats_random",
    }
    assert ev.verdict.sentence.endswith(".")


# ==========================================================================
# Section 2: the point-in-time seam (needs a lake)
# ==========================================================================

N_DAYS = 700
SIGNAL_YEARS = 0.5  # 126 days
TENOR_WEEKS = 4.0
TENOR_DAYS = 20


def _write_ohlcv(
    store: DeltaLakeStore, symbol: str, dates: pd.DatetimeIndex, closes: FloatArray
) -> None:
    store.write_bronze(
        dataset_id(symbol),
        INGEST,
        pd.DataFrame(
            {
                "symbol": symbol.upper(),
                "trade_date": dates,
                "open": closes,
                "high": closes * 1.003,
                "low": closes * 0.997,
                "close": closes,
                "volume": np.full(len(closes), 1_000_000, dtype=int),
                "adj_close": closes,
            }
        ),
    )


def _closes(n_names: int = 12, n: int = N_DAYS) -> tuple[pd.DatetimeIndex, dict[str, FloatArray]]:
    """SPY plus a universe whose names differ in beta and idiosyncratic vol, so
    fragility is cross-sectionally informative and the 20-day vol sits inside
    the clamp throughout."""
    dates = pd.date_range(end=INGEST, periods=n, freq="B")
    rng = np.random.default_rng(0)
    market = rng.normal(0.0003, 0.01, n)
    out = {"spy": 100.0 * np.exp(np.cumsum(market))}
    for j in range(n_names):
        r = np.random.default_rng(10 + j)
        ret = (0.5 + 0.15 * j) * market + r.normal(0.0, 0.006 + 0.001 * j, n)
        out[f"n{j:02d}"] = 100.0 * np.exp(np.cumsum(ret))
    return dates, out


def _store(
    tmp_path: Path,
    closes: dict[str, FloatArray],
    dates: pd.DatetimeIndex,
    *,
    drop: dict[str, list[pd.Timestamp]] | None = None,
    vix: FloatArray | None = None,
) -> DeltaLakeStore:
    store = DeltaLakeStore(tmp_path)
    if vix is None:
        vix = 15 + 10 * (np.sin(np.linspace(0, 25, len(dates))) + 1)
    store.write_bronze("vix", INGEST, pd.DataFrame({"date": dates, "close": vix}))
    for symbol, px in closes.items():
        keep = ~dates.isin((drop or {}).get(symbol, []))
        _write_ohlcv(store, symbol, dates[keep], px[keep])
    return store


def _collect(
    store: DeltaLakeStore,
    names: list[str],
    *,
    rule: StrikeRule | None = None,
    top_k: int = 2,
) -> wf.Collected:
    return collect_periods(
        store,
        symbols=names,
        as_of=INGEST,
        rule=rule or ByMoneyness(5.0),
        tenor_weeks=TENOR_WEEKS,
        signal_years=SIGNAL_YEARS,
        top_k=top_k,
    )


def _names(closes: dict[str, FloatArray]) -> list[str]:
    return [s for s in closes if s != "spy"]


def _by_date(col: wf.Collected) -> dict[dt.date, PeriodInputs]:
    return {p.date: p for p in col.periods}


def test_the_calendar_steps_by_tenor_and_leaves_room_for_a_full_roll(tmp_path: Path) -> None:
    dates, closes = _closes()
    col = _collect(_store(tmp_path, closes, dates), _names(closes))
    assert len(col.periods) >= 20
    spy_dates = [d.date() for d in dates]
    pos = [spy_dates.index(p.date) for p in col.periods]
    assert all(b - a == TENOR_DAYS for a, b in itertools.pairwise(pos))
    assert pos[0] >= round(SIGNAL_YEARS * 252)  # a full signal window precedes the first date
    assert pos[-1] + TENOR_DAYS <= len(dates) - 1
    assert col.periods[0].expiry_date == spy_dates[pos[0] + TENOR_DAYS]
    assert col.diagnostics.n_symbols_loaded == 12


def test_no_look_ahead_corrupting_prices_after_t_leaves_the_picks_at_t(tmp_path: Path) -> None:
    """Point-in-time correctness, the #1 invariant: every price after a cut is
    replaced with garbage (including SPY). Every period entered on or before
    the cut must see identical signals -- and so identical picks -- and every
    period that also EXPIRES by the cut must settle identically."""
    dates, closes = _closes()
    base = _collect(_store(tmp_path / "a", closes, dates), _names(closes))
    cut = 450
    rng = np.random.default_rng(99)
    bad = {s: px.copy() for s, px in closes.items()}
    for px in bad.values():
        px[cut + 1 :] *= rng.uniform(0.3, 3.0, len(px) - cut - 1)
    cut_date = dates[cut].date()
    other = _collect(_store(tmp_path / "b", bad, dates), _names(bad))
    a, b = _by_date(base), _by_date(other)
    compared = [d for d in a if d <= cut_date]
    assert len(compared) >= 8
    for d in compared:
        assert a[d].symbols == b[d].symbols
        for s in SCREENS:
            np.testing.assert_allclose(a[d].signals[s], b[d].signals[s], rtol=1e-9, atol=1e-12)
        if a[d].expiry_date <= cut_date:
            np.testing.assert_allclose(a[d].payoff, b[d].payoff, rtol=1e-9)
            np.testing.assert_allclose(a[d].cost, b[d].cost, rtol=1e-9)


def test_a_different_split_basis_for_old_prices_leaves_the_outcome_at_t_unchanged(
    tmp_path: Path,
) -> None:
    """Bronze OHLCV is split-adjusted, so the LEVEL at t embeds splits that
    happen after t (a 2022 close is quoted post-split). A snapshot that has
    absorbed one more split states all earlier prices at another level: here the
    whole history up to a date D is halved, with no jump in the series. A put's
    ROI is scale-free, so every period settling by D must come out the same --
    but ONLY because commission is zero: ``$0.65 * contracts / 100`` depends on
    the share count, which the level changes."""
    dates, closes = _closes()
    split_at = 520
    adjusted = {s: px.copy() for s, px in closes.items()}
    for px in adjusted.values():
        px[: split_at + 1] *= 0.5
    base = _collect(_store(tmp_path / "a", closes, dates), _names(closes))
    after = _collect(_store(tmp_path / "b", adjusted, dates), _names(adjusted))
    a, b = _by_date(base), _by_date(after)
    split_date = dates[split_at].date()
    compared = [d for d in a if a[d].expiry_date <= split_date]
    assert len(compared) >= 10
    for d in compared:
        assert a[d].symbols == b[d].symbols
        np.testing.assert_allclose(a[d].payoff, b[d].payoff, rtol=1e-9)
        np.testing.assert_allclose(a[d].cost, b[d].cost, rtol=1e-9)
        for s in SCREENS:
            np.testing.assert_allclose(a[d].signals[s], b[d].signals[s], rtol=1e-7, atol=1e-10)


def test_no_look_ahead_corrupting_vix_after_t_leaves_regime_signals_and_outcome(
    tmp_path: Path,
) -> None:
    """VIX feeds two things at t: the regime label and the vol-beta window.
    Replace every VIX print after a cut with garbage: every period entered on or
    before the cut must keep its regime, its signals (vol_beta included), its
    picks and its outcome. The cut sits one session after a rebalance date, so a
    window that peeked even a few rows ahead would see the garbage."""
    dates, closes = _closes()
    clean_vix = 15 + 10 * (np.sin(np.linspace(0, 25, len(dates))) + 1)
    base_store = _store(tmp_path / "a", closes, dates, vix=clean_vix)
    base = _collect(base_store, _names(closes))
    spy_dates = [d.date() for d in dates]
    cut = spy_dates.index(base.periods[12].date) + 1
    bad_vix = clean_vix.copy()
    bad_vix[cut + 1 :] *= np.random.default_rng(5).uniform(0.2, 5.0, len(bad_vix) - cut - 1)
    other = _collect(_store(tmp_path / "b", closes, dates, vix=bad_vix), _names(closes))
    a, b = _by_date(base), _by_date(other)
    compared = [d for d in a if spy_dates.index(d) <= cut]
    assert len(compared) >= 13
    for d in compared:
        assert a[d].regime == b[d].regime
        assert a[d].symbols == b[d].symbols
        for s in SCREENS:
            np.testing.assert_allclose(a[d].signals[s], b[d].signals[s], rtol=1e-12, atol=1e-14)
        np.testing.assert_allclose(a[d].payoff, b[d].payoff, rtol=1e-12)
        np.testing.assert_allclose(a[d].cost, b[d].cost, rtol=1e-12)
    # the corruption does bite later on, so the check above could have failed
    later = [d for d in a if d in b and spy_dates.index(d) > cut + 60]
    assert any(
        a[d].regime != b[d].regime
        or not np.allclose(a[d].signals["vol_beta"], b[d].signals["vol_beta"])
        for d in later
    )


def test_a_gappy_name_is_measured_on_exactly_the_signal_window(tmp_path: Path) -> None:
    """The metrics read the rows dated inside the trailing ``signal_years``
    window -- not "the last N aligned rows", which reach further back whenever
    a name has gaps. A name missing every 13th session (7.7%, above the 90%
    coverage bar) has its prices corrupted from before the window: its signals
    at t must not move."""
    dates, closes = _closes()
    victim = "n05"
    gaps = [dates[i] for i in range(0, len(dates), 13)]
    base = _collect(_store(tmp_path / "a", closes, dates, drop={victim: gaps}), _names(closes))
    spy_dates = [d.date() for d in dates]
    window = round(SIGNAL_YEARS * 252)
    present = {i for i in range(len(dates)) if i % 13 != 0}
    target = next(
        per
        for per in base.periods
        if victim in per.symbols
        and (pos := spy_dates.index(per.date)) >= 300
        and {pos - window, pos - window - 1} <= present
    )
    pos = spy_dates.index(target.date)
    # corrupt everything strictly before the row the window's first return needs
    bad = {s: px.copy() for s, px in closes.items()}
    bad[victim][: pos - window - 1] *= np.random.default_rng(1).uniform(0.3, 3.0, pos - window - 1)
    other = _collect(_store(tmp_path / "b", bad, dates, drop={victim: gaps}), _names(bad))
    got = _by_date(other)[target.date]
    assert got.symbols == target.symbols
    for s in SCREENS:
        np.testing.assert_allclose(got.signals[s], target.signals[s], rtol=1e-9, atol=1e-12)


@pytest.mark.parametrize("rule", [ByMoneyness(5.0), ByDelta(0.10)], ids=["moneyness", "delta"])
def test_per_name_outcomes_match_run_put_roll_on_a_gap_free_name(
    tmp_path: Path, rule: StrikeRule
) -> None:
    """On a name with no gaps the walk-forward and the tape price the SAME
    rolls: payoff equal, and cost equal to notional plus the model half-spread
    (the cost-free run: commission zero)."""
    dates, closes = _closes()
    store = _store(tmp_path, closes, dates)
    col = _collect(store, _names(closes), rule=rule)
    first = col.periods[0]
    start = [d.date() for d in dates].index(first.date)
    sym = first.symbols[0]
    px = pd.Series(closes[sym], index=dates)
    res = run_put_roll(
        px,
        trailing_realized_vol(px),
        asset=sym,
        as_of=INGEST,
        notional=1.0,
        rule=rule,
        tenor_weeks=TENOR_WEEKS,
        lookback_years=(len(dates) - start) / 252,
        commission_per_contract=0.0,
    )
    by_entry = {c.entry_date: c for c in res.cycles}
    matched = 0
    for per in col.periods:
        if sym not in per.symbols:
            continue
        cyc = by_entry[per.date]
        i = per.symbols.index(sym)
        assert per.payoff[i] == pytest.approx(cyc.payoff, rel=1e-12, abs=1e-15)
        assert per.cost[i] == pytest.approx(1.0 + cyc.cost, rel=1e-12)
        matched += 1
    assert matched >= 15


def test_expiry_settles_at_the_last_close_on_or_before_it_and_stale_ones_are_counted(
    tmp_path: Path,
) -> None:
    """A gap over the expiry date is never a reason to drop the name after t:
    it settles at its last close on or before expiry, and settlements more than
    three days stale are counted."""
    dates, closes = _closes()
    col0 = _collect(_store(tmp_path / "a", closes, dates), _names(closes))
    per = col0.periods[4]
    spy_dates = [d.date() for d in dates]
    exp_i = spy_dates.index(per.expiry_date)
    victim = per.symbols[0]
    gap = list(dates[exp_i - 2 : exp_i + 4])  # six sessions without a close, spanning expiry
    last_close_i = exp_i - 3
    col = _collect(_store(tmp_path / "b", closes, dates, drop={victim: gap}), _names(closes))
    got = _by_date(col)[per.date]
    assert victim in got.symbols  # NOT dropped for the gap after t
    assert col.diagnostics.n_stale_settlements >= 1
    assert col0.diagnostics.n_stale_settlements == 0

    px = pd.Series(closes[victim], index=dates)
    entry_i = spy_dates.index(per.date)
    terms = RollTerms(
        basis=PricingBasis(),
        rate=DEFAULT_RATE,
        notional=1.0,
        rule=ByMoneyness(5.0),
        tenor_weeks=TENOR_WEEKS,
        t_years=TENOR_DAYS / 252.0,
        commission_per_contract=0.0,
        spread_scale=1.0,
    )
    rv = trailing_realized_vol(px).to_numpy()
    expected = price_model_roll(
        px.iloc[entry_i],
        clamp_realized_vol(float(rv[entry_i])),
        px.iloc[last_close_i],
        per.date,
        terms,
    )
    assert got.payoff[got.symbols.index(victim)] == pytest.approx(expected.payoff, rel=1e-6)


def test_a_missing_entry_close_sits_the_name_out_and_is_counted(tmp_path: Path) -> None:
    dates, closes = _closes()
    col0 = _collect(_store(tmp_path / "a", closes, dates), _names(closes))
    per = col0.periods[3]
    victim = per.symbols[0]
    entry_ts = pd.Timestamp(per.date)
    col = _collect(_store(tmp_path / "b", closes, dates, drop={victim: [entry_ts]}), _names(closes))
    got = _by_date(col)
    assert victim not in got[per.date].symbols
    assert victim in got[col0.periods[2].date].symbols  # only that period is lost
    assert col.diagnostics.n_no_entry_close == 1


def test_the_volatility_floor_and_cap_make_a_name_ineligible(tmp_path: Path) -> None:
    """20-day vol must be strictly inside [0.06, 2.0]: a name that barely moves
    (its 'vol' is an artifact of a short estimator) and one that is wilder than
    the cap are priced by nothing but the clamp, so they are not candidates."""
    dates, closes = _closes()
    rng = np.random.default_rng(5)
    closes["flat"] = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.0004, N_DAYS)))
    closes["wild"] = 100.0 * np.exp(np.cumsum(np.clip(rng.normal(0, 0.35, N_DAYS), -0.8, 0.8)))
    col = _collect(_store(tmp_path, closes, dates), _names(closes))
    seen = {s for p in col.periods for s in p.symbols}
    assert "flat" not in seen and "wild" not in seen
    assert {"n00", "n05", "n11"} <= seen


def test_under_moneyness_a_too_deep_strike_removes_every_name(tmp_path: Path) -> None:
    """Past 10% the model's premium is not a price. A moneyness rule that deep
    leaves nothing to test (and says why); a delta rule only reports the share."""
    dates, closes = _closes()
    store = _store(tmp_path, closes, dates)
    col = _collect(store, _names(closes), rule=ByMoneyness(12.0))
    assert col.periods == []
    # every priced name-period was removed, so none entered the test
    assert col.diagnostics.n_removed_floor_or_depth == 12 * col.diagnostics.n_rebalance_dates
    assert col.diagnostics.n_name_periods_priced == 0
    assert col.diagnostics.depth_beyond_10pct_share is None
    result = walk_forward(
        store,
        symbols=_names(closes),
        as_of=INGEST,
        rule=ByMoneyness(12.0),
        tenor_weeks=TENOR_WEEKS,
        signal_years=SIGNAL_YEARS,
        top_k=2,
        n_random=50,
    )
    assert result.verdict.status == "insufficient_data"
    assert result.verdict.reasons == ["no_test_window"]


def test_a_floored_premium_removes_the_name_under_moneyness_only(tmp_path: Path) -> None:
    dates, closes = _closes()
    rng = np.random.default_rng(8)
    # ~6.5% annualised vol: an 8% strike over 4 weeks is worth less than the
    # 1e-4 floor in some windows -- a floor, not a price.
    closes["quiet"] = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.0041, N_DAYS)))
    store = _store(tmp_path, closes, dates)
    names = _names(closes)
    money = _collect(store, names, rule=ByMoneyness(8.0))
    assert money.diagnostics.n_removed_floor_or_depth > 0
    delta = _collect(store, names, rule=ByDelta(0.10))
    assert delta.diagnostics.n_removed_floor_or_depth == 0
    quiet_money = sum("quiet" in p.symbols for p in money.periods)
    quiet_delta = sum("quiet" in p.symbols for p in delta.periods)
    assert quiet_money < quiet_delta


def test_too_few_eligible_names_means_no_periods(tmp_path: Path) -> None:
    dates, closes = _closes(n_names=3)
    col = _collect(_store(tmp_path, closes, dates), _names(closes), top_k=2)  # 3 < 2k = 4
    assert col.periods == []
    assert col.diagnostics.n_rebalance_dates == 0


def test_a_signal_missing_for_a_name_drops_it_from_every_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shared eligibility: if ``tail_beta`` cannot be estimated for a name at
    t, the name is out for ALL 13 screens that period (one candidate set, so
    the baskets, the universe ROI and the excesses are comparable) and the
    removal is counted against that signal."""
    dates, closes = _closes()
    store = _store(tmp_path, closes, dates)
    real = wf._measure

    def flaky(name: wf._Name, p: int, cal: wf._Calendar) -> dict[str, float | None]:
        values = real(name, p, cal)
        if name.symbol == "n03" and (p // TENOR_DAYS) % 2 == 0:
            values["tail_beta"] = None
        return values

    monkeypatch.setattr(wf, "_measure", flaky)
    col = _collect(store, _names(closes))
    missing = [p for p in col.periods if "n03" not in p.symbols]
    present = [p for p in col.periods if "n03" in p.symbols]
    assert missing and present
    for p in col.periods:
        assert {len(p.signals[s]) for s in SCREENS} == {len(p.symbols)}
    assert col.diagnostics.signal_removed == {"tail_beta": len(missing)}


def test_a_period_left_with_fewer_than_two_k_names_sits_out_and_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dates, closes = _closes()
    store = _store(tmp_path, closes, dates)
    clean = _collect(store, _names(closes))
    victim_date = clean.periods[2].date
    real = wf._measure

    def starve(name: wf._Name, p: int, cal: wf._Calendar) -> dict[str, float | None]:
        values = real(name, p, cal)
        # leave 3 names intact: fewer than 2k = 4, so the period sits out with a
        # NON-empty candidate set whose names must still not be counted
        if cal.dates[p].date() == victim_date and name.symbol not in {"n00", "n01", "n02"}:
            values["vol_beta"] = None
        return values

    monkeypatch.setattr(wf, "_measure", starve)
    col = _collect(store, _names(closes))
    assert victim_date not in _by_date(col)
    assert col.diagnostics.n_sat_out == 1
    assert len(col.periods) == len(clean.periods) - 1
    assert col.diagnostics.signal_removed["vol_beta"] == 9
    # the sat-out period is a hole in the slot sequence, not a renumbering
    assert [p.slot for p in col.periods] == [
        s for s in range(col.diagnostics.n_rebalance_dates) if s != clean.periods[2].slot
    ]
    # diagnostics describe the name-periods that ENTERED the test: the 12 priced
    # in the starved period are not counted
    assert col.diagnostics.n_name_periods_priced == sum(len(p.symbols) for p in col.periods)
    assert col.diagnostics.n_name_periods_priced == clean.diagnostics.n_name_periods_priced - 12


def test_stale_settlements_in_a_sat_out_period_are_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dates, closes = _closes()
    clean = _collect(_store(tmp_path / "a", closes, dates), _names(closes))
    per = clean.periods[4]
    exp_i = [d.date() for d in dates].index(per.expiry_date)
    gap = list(dates[exp_i - 2 : exp_i + 4])
    store = _store(tmp_path / "b", closes, dates, drop={per.symbols[0]: gap})
    assert _collect(store, _names(closes)).diagnostics.n_stale_settlements >= 1
    real = wf._measure

    def starve(name: wf._Name, p: int, cal: wf._Calendar) -> dict[str, float | None]:
        values = real(name, p, cal)
        if cal.dates[p].date() == per.date and name.symbol not in {"n00", "n01", "n02"}:
            values["vol_beta"] = None  # 3 intact names < 2k = 4: the period sits out
        return values

    monkeypatch.setattr(wf, "_measure", starve)
    col = _collect(store, _names(closes))
    assert per.date not in _by_date(col)  # the stale name's period sat out
    assert col.diagnostics.n_stale_settlements == 0


def test_a_rebalance_before_the_first_vix_print_sits_out(tmp_path: Path) -> None:
    dates, closes = _closes()
    store = DeltaLakeStore(tmp_path)
    late = dates[300:]  # VIX only from day 300: early rebalances have no regime
    vix = 15 + 10 * (np.sin(np.linspace(0, 25, len(late))) + 1)
    store.write_bronze("vix", INGEST, pd.DataFrame({"date": late, "close": vix}))
    for symbol, px in closes.items():
        _write_ohlcv(store, symbol, dates, px)
    col = _collect(store, _names(closes))
    assert col.diagnostics.n_no_regime > 0
    assert col.diagnostics.n_sat_out == col.diagnostics.n_no_regime
    assert all(p.date >= late[0].date() for p in col.periods)


def test_a_missing_name_is_skipped_and_reported(tmp_path: Path) -> None:
    dates, closes = _closes()
    col = _collect(_store(tmp_path, closes, dates), [*_names(closes), "ghost"])
    assert col.diagnostics.skipped_symbols == ["ghost"]
    assert col.diagnostics.n_symbols_requested == 13
    assert col.diagnostics.n_symbols_loaded == 12


def test_missing_benchmark_or_vix_or_bad_parameters_raise(tmp_path: Path) -> None:
    dates, closes = _closes()
    no_spy = {s: px for s, px in closes.items() if s != "spy"}
    with pytest.raises(LookupError, match="SPY benchmark"):
        _collect(_store(tmp_path / "a", no_spy, dates), list(no_spy))
    empty = DeltaLakeStore(tmp_path / "b")
    with pytest.raises(LookupError, match="VIX"):
        _collect(empty, ["n00"])
    store = _store(tmp_path / "c", closes, dates)
    for kw in (
        {"tenor_weeks": 0.0},
        {"signal_years": 0.0},
        {"top_k": 0},
        {"signal_years": 0.05},
    ):
        args: dict[str, object] = {
            "symbols": ["n00"],
            "as_of": INGEST,
            "rule": ByMoneyness(5.0),
            "tenor_weeks": 4.0,
            "signal_years": 0.5,
            "top_k": 2,
            **kw,
        }
        with pytest.raises(ValueError):
            collect_periods(store, **args)  # type: ignore[arg-type]


def test_dividends_enter_the_price_at_the_entry_date(tmp_path: Path) -> None:
    """With a Tiingo snapshot the model prices at the name's measured yield
    (read point-in-time through ``dividend_lookup``): every payoff differs from
    the q = 0 run on the same prices."""
    from tail_lab.research import dividends
    from tests.test_research_dividends import seed_tiingo_eod

    dates, closes = _closes()
    plain = _collect(_store(tmp_path / "a", closes, dates), _names(closes), rule=ByDelta(0.10))
    store = _store(tmp_path / "b", closes, dates)
    dividends._memo.clear()
    seed_tiingo_eod(store, _names(closes), INGEST, annual_yield=0.05)
    paying = _collect(store, _names(closes), rule=ByDelta(0.10))
    dividends._memo.clear()
    a, b = _by_date(plain), _by_date(paying)
    common = sorted(set(a) & set(b))
    assert common
    assert any(not np.allclose(a[d].cost, b[d].cost) for d in common)


def test_walk_forward_end_to_end_is_complete_seeded_and_serialisable(tmp_path: Path) -> None:
    dates, closes = _closes()
    store = _store(tmp_path, closes, dates)
    kw = dict(
        symbols=_names(closes),
        as_of=INGEST,
        rule=ByDelta(0.10),
        tenor_weeks=TENOR_WEEKS,
        signal_years=SIGNAL_YEARS,
        top_k=2,
        n_random=200,
    )
    res = walk_forward(store, **kw)  # type: ignore[arg-type]
    again = walk_forward(store, **kw)  # type: ignore[arg-type]
    assert res.model_dump() == again.model_dump()
    assert res.strike_rule == "delta" and res.target_delta == 0.10 and res.moneyness_pct is None
    assert res.universe_size == 12 and res.n_periods == len(res.periods)
    assert res.test_start == res.periods[0].date and res.test_end == res.periods[-1].expiry_date
    assert [s.screen for s in res.screens] == list(SCREENS)
    assert res.caveats == ["survivorship", "model_priced_signal"]
    assert "caveats" not in wf.SignalVerdict.model_fields  # stated once, on the result
    assert res.code_sha == "unknown"
    # provenance: VIX, the benchmark and every loaded name are cited
    assert len(res.snapshot_ids) >= 14
    assert res.diagnostics.n_symbols_loaded == 12
    assert {r.symbol for r in res.per_name} <= set(_names(closes))
    assert res.model_dump_json()
    # the moneyness path labels itself too
    m = walk_forward(store, **{**kw, "rule": ByMoneyness(5.0)})  # type: ignore[arg-type]
    assert m.strike_rule == "moneyness" and m.moneyness_pct == 5.0 and m.target_delta is None


def _walk(store: DeltaLakeStore, names: list[str]) -> wf.SignalTestResult:
    return walk_forward(
        store,
        symbols=names,
        as_of=INGEST,
        rule=ByDelta(0.10),
        tenor_weeks=TENOR_WEEKS,
        signal_years=SIGNAL_YEARS,
        top_k=2,
        n_random=50,
    )


@pytest.mark.parametrize("with_dividends", [False, True], ids=["no-tiingo", "tiingo"])
def test_provenance_is_what_the_ranking_would_cite_and_each_table_opens_twice_at_most(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_dividends: bool
) -> None:
    """``snapshot_ids`` must be the same list ``ranking.snapshot_ids_for`` gives
    (VIX, SPY and every name's price snapshots, then the dividend snapshot) --
    but collected as the data was read, so no table is opened a second time just
    to identify it. A Delta table is opened once to resolve the as-of partition
    (which also yields the id) and once to read it: never a third time."""
    from tail_lab.lake import store as store_module
    from tail_lab.research.backtest.ranking import BENCHMARK, snapshot_ids_for
    from tail_lab.research.dividends import _memo as dividend_memo
    from tests.test_research_dividends import seed_tiingo_eod

    dates, closes = _closes()
    names = _names(closes)
    root = tmp_path / "lake"
    seeded = _store(root, closes, dates)
    dividend_memo.clear()
    if with_dividends:
        seed_tiingo_eod(seeded, names, INGEST, annual_yield=0.03)
    opens: Counter[str] = Counter()

    class CountingTable(store_module.DeltaTable):  # type: ignore[misc]
        def __init__(self, table_uri: str, *args: object, **kwargs: object) -> None:
            opens[table_uri] += 1
            super().__init__(table_uri, *args, **kwargs)

    monkeypatch.setattr(store_module, "DeltaTable", CountingTable)
    fresh = DeltaLakeStore(root)  # cold caches: every resolution really opens
    dividend_memo.clear()
    cited = _walk(fresh, names).snapshot_ids
    existing = {
        uri: n for uri, n in opens.items() if Path(uri).exists()
    }  # absent tables fail to open
    assert len(existing) >= 14  # VIX, SPY, 12 names (+ the dividend table)
    assert max(existing.values()) <= 2, existing

    monkeypatch.undo()
    expected = snapshot_ids_for(DeltaLakeStore(root), INGEST, (BENCHMARK, *names))
    dividend_memo.clear()
    assert cited == expected
    assert (len(cited) == 1 + 13 + 1) if with_dividends else (len(cited) == 1 + 13)


def test_the_hindsight_diagnostics_default_is_empty() -> None:
    d = Diagnostics()
    assert d.n_sat_out == 0 and d.signal_removed == {} and d.depth_beyond_10pct_share is None
