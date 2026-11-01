"""The beyond-volatility Fama-MacBeth test (``research/backtest/beyond_vol.py``).

Every test plants a world whose right answer is known, because the point of
the procedure is to be wrong in a known, small way: it must pass when
fragility matters beyond trailing vol, and must NOT pass when vol alone (in a
shape the controls can absorb) drives the outcome. Panels are small
(30 periods x 60 names) and share one ``T``, so the 100,000-draw fixed-b null
is simulated once per ``(T, L)`` and memoised across the module.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from dataclasses import replace

import numpy as np
import numpy.typing as npt
import pytest
from scipy import stats

from tail_lab.research.backtest import beyond_vol as bv
from tail_lab.research.backtest.walk_forward import SCREENS, PeriodInputs

FloatArray = npt.NDArray[np.float64]

NON_EQ = ("tlt", "gld", "ung", "hyg")
STRESS = {3, 4, 10, 17, 18, 19, 25}  # four episodes
T = 30


def _v_shape(rv20: FloatArray) -> FloatArray:
    """A nonlinear, non-monotone function of trailing vol alone."""
    return 6.0 * np.abs(np.log(rv20 / 0.27))


def _panel(
    *,
    n_periods: int = T,
    n_names: int | Callable[[int], int] = 60,
    seed: int = 0,
    effect: float = 0.0,
    vol_fn: Callable[[FloatArray], FloatArray] | None = None,
    comp_fn: Callable[[FloatArray], FloatArray] | None = None,
    spy: FloatArray | None = None,
    stress: set[int] | None = None,
    non_equity_shift: float = 0.0,
) -> list[PeriodInputs]:
    """Names have a persistent vol level; fragility ``frag`` is drawn
    independently of vol; the composite is ``frag`` plus a function of vol.
    The outcome is a latent score with a put paying when it exceeds a hurdle.

    ``effect`` multiplies ``frag`` in the latent score (times ``spy[i]`` when
    ``spy`` is given, which makes gamma_t proportional to the market move).
    ``vol_fn`` adds a function of ``rv20`` to the latent score;
    ``non_equity_shift`` moves the non-equity names' latent score AND their
    composite by the same amount (an asset-class effect)."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n_periods):
        n = n_names(i) if callable(n_names) else n_names
        symbols = NON_EQ + tuple(f"n{j:02d}" for j in range(n - len(NON_EQ)))
        level = np.random.default_rng(seed + 1).normal(-1.3, 0.35, n)  # persistent per name
        lr20 = level + rng.normal(0, 0.25, n)
        lr_long = level + rng.normal(0, 0.12, n)
        rv20, rv_long = np.exp(lr20), np.exp(lr_long)
        frag = rng.normal(size=n)
        z_vol = (lr20 - lr20.mean()) / lr20.std()
        is_non_eq = np.isin(symbols, NON_EQ).astype(float)
        comp = frag + 1.5 * (comp_fn(z_vol) if comp_fn else z_vol) + non_equity_shift * is_non_eq
        r = float(spy[i]) if spy is not None else float(rng.normal(0.01, 0.04))
        latent = rng.normal(size=n) + effect * frag * (r if spy is not None else 1.0)
        if vol_fn is not None:
            latent = latent + vol_fn(rv20)
        latent = latent + non_equity_shift * is_non_eq
        signals = {s: rng.normal(size=n) for s in SCREENS}
        signals.update(rv20_high=rv20, rv_long_high=rv_long, rv_ratio_high=rv_long / rv20)
        signals[bv.COMPOSITE] = comp
        day = dt.date(2021, 1, 4) + dt.timedelta(days=28 * i)
        out.append(
            PeriodInputs(
                slot=i,
                date=day,
                expiry_date=day + dt.timedelta(days=28),
                regime="calm",
                symbols=symbols,
                signals=signals,
                payoff=2.0 * np.maximum(latent - 1.0, 0.0),
                cost=np.full(n, 1.05),
                spy_payoff=3.0 if i in (STRESS if stress is None else stress) else 0.0,
                spy_cost=1.05,
                spy_return=r,
            )
        )
    return out


# ==========================================================================
# The positive and negative controls
# ==========================================================================


def test_fragility_that_matters_beyond_vol_passes() -> None:
    """Positive control: the outcome depends on a fragility factor drawn
    independently of vol, and the composite carries it. If the test could not
    pass this world it could never certify a real finding; every status gate
    (p, intercept test, D1, D3) is open here."""
    res = bv.beyond_vol_test(_panel(seed=2, effect=1.5))
    assert res.status == "predicts_beyond_trailing_vol"
    assert res.demotion is None
    assert res.gamma_bar is not None and res.gamma_bar > 0.3
    assert res.p_value is not None and res.p_value < bv.ALPHA
    assert res.ci90 is not None and res.ci90.low > 0.0


def test_a_vol_only_effect_is_not_a_pass_and_the_controls_are_why() -> None:
    """The reason this test exists. The outcome is a V-shaped function of
    trailing vol and nothing else; the composite is correlated with that V
    (rank-by-rank it looks fragile). Without controls the composite 'predicts'
    strongly -- the very false positive the signal test's vol baselines warn
    about -- and with them gamma_bar is ~0. The gap between the two numbers is
    the controls working; if they stopped absorbing vol, both would be large."""
    world = _panel(seed=3, vol_fn=_v_shape, comp_fn=lambda z: np.abs(z - 0.3))
    res = bv.beyond_vol_test(world)
    assert res.status == "no_evidence_beyond_trailing_vol"
    assert res.gamma_no_controls is not None and res.gamma_no_controls > 0.3
    assert res.gamma_bar is not None and abs(res.gamma_bar) < 0.1
    assert res.gamma_no_controls > 5 * abs(res.gamma_bar)


def test_an_asset_class_effect_is_absorbed_by_the_non_equity_dummy() -> None:
    """Non-equity ETFs pay more AND score higher on the composite: a pure
    asset-class story. The dummy must take it, so no pass; the uncontrolled
    slope shows how much of the apparent effect it was."""
    res = bv.beyond_vol_test(_panel(seed=1, non_equity_shift=3.0))
    assert res.status == "no_evidence_beyond_trailing_vol"
    assert res.gamma_no_controls is not None and res.gamma_bar is not None
    assert res.gamma_no_controls > 0.07
    assert abs(res.gamma_bar) < 0.05


def test_pure_noise_does_not_pass() -> None:
    res = bv.beyond_vol_test(_panel(seed=1))
    assert res.status == "no_evidence_beyond_trailing_vol"
    assert res.demotion is None  # demotions are only ever reported against a significant result
    assert res.p_value is not None and res.p_value > 0.2


# ==========================================================================
# Status order
# ==========================================================================


def test_too_few_periods_is_insufficient_data_and_reports_nothing_inferred() -> None:
    """ADR 0033's own floor (24 periods): below it there is no test, so no
    number may look like one."""
    res = bv.beyond_vol_test(_panel(n_periods=bv.MIN_PERIODS - 1, seed=2, effect=1.5))
    assert res.status == "insufficient_data"
    assert (res.gamma_bar, res.p_value, res.ci90, res.a_p_value) == (None, None, None, None)
    assert res.n_periods == bv.MIN_PERIODS - 1


def test_fewer_than_three_stress_episodes_is_insufficient_data() -> None:
    """Enough periods but the market was stressed in only two runs: gamma_bar
    cannot be checked against dropping its best episode, so it is not a test.
    Slots 3 and 4 are one episode; 10 is another."""
    res = bv.beyond_vol_test(_panel(seed=2, effect=1.5, stress={3, 4, 10}))
    assert res.status == "insufficient_data"
    assert res.n_periods == T
    # ... and three episodes are enough: the same world with one more run passes.
    assert bv.beyond_vol_test(_panel(seed=2, effect=1.5, stress={3, 4, 10, 17})).status != (
        "insufficient_data"
    )


def test_a_gamma_driven_by_the_market_move_is_not_robust_not_a_pass() -> None:
    """gamma_t is proportional to SPY's return, which is positive in every
    period (a bull sample). gamma_bar is significantly positive -- but it is the
    sample's market path, not fragility: the market-neutral intercept a is ~0.
    The status must be not_robust and name the intercept test."""
    spy = np.random.default_rng(9).uniform(0.01, 0.03, T)
    res = bv.beyond_vol_test(_panel(seed=5, effect=15.0, spy=spy))
    assert res.p_value is not None and res.p_value < bv.ALPHA
    assert res.a_p_value is not None and res.a_p_value >= bv.ALPHA
    assert res.status == "not_robust"
    assert res.demotion == "intercept"
    assert res.b_slope is not None and res.b_slope > 5.0  # gamma_t does follow the market


def test_a_non_significant_result_is_never_called_not_robust() -> None:
    """Order: p >= 0.05 is no_evidence even when the intercept test also fails
    (here it does, a is noise); demotions only ever downgrade a significant result."""
    res = bv.beyond_vol_test(_panel(seed=1))
    assert res.a_p_value is not None and res.a_p_value > bv.ALPHA
    assert res.status == "no_evidence_beyond_trailing_vol"
    assert res.demotion is None


def test_demotions_fire_on_a_one_period_or_one_episode_or_asset_class_result() -> None:
    """D1 and D3 by hand, on gamma_t series whose answers are obvious.
    One period of 24 carries the mean (drops to <= 0); one episode carries it;
    equity-only gamma_bar is not positive; or it could not be measured."""
    none = np.zeros(24, dtype=bool)
    g = np.full(24, 0.1)
    g[7] = 3.0
    g[8:] = -0.05  # without period 7 the mean is (7 * 0.1 - 16 * 0.05) / 23 < 0
    assert bv._first_demotion(g, [none], 0.2) == "drop_best_period"
    spike = np.full(24, 0.05)
    spike[4] = 5.0
    assert bv._first_demotion(spike, [none], 0.2) is None  # others positive: not D1
    lopsided = np.concatenate([np.full(12, -0.1), np.full(12, 0.5)])
    block = np.arange(24) >= 12
    assert bv._first_demotion(lopsided, [block], 0.2) == "drop_best_episode"
    assert bv._first_demotion(np.full(24, 0.1), [none], -0.01) == "equity_only"
    assert bv._first_demotion(np.full(24, 0.1), [none], None) == "equity_only"
    assert bv._first_demotion(np.full(24, 0.1), [none], 0.2) is None
    # the best single period is what D1 drops, not the first one
    lone = np.full(24, -0.1)
    lone[11] = 2.0
    assert bv._first_demotion(lone, [none], 0.2) == "drop_best_period"


# ==========================================================================
# Skipping, determinism, validation
# ==========================================================================


def test_periods_below_n_min_names_are_skipped_and_counted() -> None:
    """A cross-section with fewer than N_MIN names cannot carry 29 regressors.
    Those periods must not enter gamma_bar, and the count must say so."""
    small = {2, 9, 20}
    world = _panel(seed=2, effect=1.5, n_periods=T + 3, n_names=lambda i: 25 if i in small else 60)
    res = bv.beyond_vol_test(world)
    assert res.n_skipped == 3
    assert res.n_periods == T
    kept = {p.date for p in world if len(p.symbols) >= bv.N_MIN}
    assert {g.date for g in res.per_period} == kept
    assert res.status == "predicts_beyond_trailing_vol"


def test_a_period_whose_design_is_rank_deficient_is_skipped_not_solved() -> None:
    """Every name with the same 20-day vol makes the vol columns constant, so
    gamma_t is not identified. The period is dropped and counted rather than
    handed to a pseudo-inverse that would return an arbitrary slope."""
    world = _panel(seed=2, effect=1.5)
    flat = dict(world[6].signals)
    flat["rv20_high"] = np.full(60, 0.25)
    world[6] = PeriodInputs(**{**world[6].__dict__, "signals": flat})
    res = bv.beyond_vol_test(world)
    assert (res.n_skipped, res.n_periods) == (1, T - 1)
    assert world[6].date not in {g.date for g in res.per_period}
    assert bv._equity_gamma(world[6], world[6].payoff) is None


def test_the_result_is_deterministic_including_the_simulated_null() -> None:
    """A pre-registered p-value must be reproducible byte for byte: same seed,
    same draws, even after the memoised null is thrown away and re-simulated."""
    world = _panel(seed=2, effect=0.3)
    first = bv.beyond_vol_test(world)
    bv._null_mean_t.cache_clear()
    bv._null_intercept_t.cache_clear()
    second = bv.beyond_vol_test(world)
    assert first.model_dump() == second.model_dump()


def test_the_frozen_constants_are_pinned_and_the_no_evidence_path_exposes_the_mde() -> None:
    """The MDE is a measured number (frozen-module simulation, 9.6pp), not a
    placeholder: a 'no evidence' result must carry it so the reader can see what
    the test could not have detected."""
    assert bv.MDE_PAY_RATE_GAP == 0.096
    res = bv.beyond_vol_test(_panel(seed=1))
    assert res.status == "no_evidence_beyond_trailing_vol"
    assert res.bandwidth == 16
    assert res.mde_pay_rate_gap == 0.096
    assert bv.beyond_vol_test(_panel(n_periods=5)).mde_pay_rate_gap == 0.096


def test_degenerate_inputs_are_refused() -> None:
    world = _panel(seed=2)
    bad_vol = dict(world[0].signals)
    bad_vol["rv20_high"] = np.zeros(60)
    with pytest.raises(ValueError, match="volatility signals must be positive"):
        bv.beyond_vol_test([PeriodInputs(**{**world[0].__dict__, "signals": bad_vol}), *world[1:]])
    flat = [PeriodInputs(**{**p.__dict__, "spy_return": 0.01}) for p in world]
    with pytest.raises(ValueError, match="spy_return is constant"):
        bv.beyond_vol_test(flat)


def test_the_unmeasurable_equity_subset_cannot_be_checked_so_is_not_a_pass() -> None:
    """An equity subset with no more names than design columns (28) cannot be
    fitted; the demotion machinery treats 'cannot check' as 'not robust'."""
    assert bv._equity_gamma(_panel(n_periods=1, n_names=30)[0], np.arange(30.0)) is None
    assert bv._first_demotion(np.full(24, 0.1), [np.zeros(24, dtype=bool)], None) == "equity_only"


# ==========================================================================
# Fixed-b null: simulated, not looked up
# ==========================================================================


def test_the_simulated_null_matches_the_kiefer_vogelsang_critical_value() -> None:
    """At T = 81, L = 16 (b = 17/81) the 95% quantile of the simulated NW t is
    the one-sided fixed-b critical value. The polynomial 1.6449 + 2.1859 b +
    0.3142 b^2 - 0.3427 b^3 is the published approximation; the simulation sits
    ~0.04 below it here (measured 2.077 vs 2.114, and the same at T = 405 with
    the same b, so it is the polynomial's fit error, not small-T), hence the
    0.05 tolerance. Either is far from the normal 1.645, which is the point."""
    b = 17 / 81
    poly = 1.6449 + 2.1859 * b + 0.3142 * b**2 - 0.3427 * b**3
    null = bv._null_mean_t(81, bv.NULL_DRAWS, bv.BANDWIDTH)
    q95 = float(np.quantile(null, 0.95))
    assert abs(q95 - poly) < 0.05
    assert q95 > 1.645 + 0.3  # a normal table would over-reject by a lot at this bandwidth
    assert bv._p_upper(null, q95) == pytest.approx(0.05, abs=1e-3)


def test_the_intercept_null_is_simulated_with_the_same_machinery() -> None:
    """The HAC intercept t has a fixed-b null of its own; at T = 81 it must be
    of the same order as the mean's (about 2.1), not a normal 1.645."""
    q95 = float(np.quantile(bv._null_intercept_t(81, bv.NULL_DRAWS, bv.BANDWIDTH), 0.95))
    assert (
        abs(q95 - float(np.quantile(bv._null_mean_t(81, bv.NULL_DRAWS, bv.BANDWIDTH), 0.95))) < 0.1
    )


def test_the_null_has_the_documented_size_and_is_read_only() -> None:
    null = bv._null_mean_t(T, bv.NULL_DRAWS, bv.BANDWIDTH)
    assert len(null) == bv.NULL_DRAWS
    assert not null.flags.writeable
    assert bv._p_upper(null, float(null[-1]) + 1.0) == 0.0
    assert bv._p_upper(null, float(null[0]) - 1.0) == 1.0


# ==========================================================================
# Building blocks, pinned by hand
# ==========================================================================


def test_ranks_are_midranks_scaled_to_a_half_either_side() -> None:
    """Three names, the first two tied: mid-ranks 1.5, 1.5, 3 -> -0.25, -0.25, 0.5.
    Ties are the common case (about 80% of put outcomes are exactly -100%)."""
    np.testing.assert_allclose(bv._rank_scaled(np.array([5.0, 5.0, 9.0])), [-0.25, -0.25, 0.5])


def test_the_spline_is_linear_beyond_its_outer_knots() -> None:
    """Restricted cubic splines are linear past the last knot -- the 'restricted'
    that stops the vol controls from fitting wild tails."""
    x = np.array([0.45, 0.5, 0.55, 0.6])
    cols = bv._rcs(x, bv.RANK_KNOTS)
    assert cols.shape == (4, 3)
    np.testing.assert_allclose(np.diff(cols, n=2, axis=0), 0.0, atol=1e-12)


def test_the_design_has_the_frozen_number_of_columns() -> None:
    """constant + composite + 15 tensor + 3 ratio + 4 + 4 level splines + dummy."""
    per = _panel(n_periods=1)[0]
    keep = np.ones(60, dtype=bool)
    assert bv._design(per, keep, with_dummy=True).shape == (60, 29)
    assert bv._design(per, keep, with_dummy=False).shape == (60, 28)


def test_newey_west_matches_a_hand_calculation() -> None:
    """g = (1, -1, 1, -1), one lag: deviations equal g; gamma_0 = 1, gamma_1 =
    -3/4; Bartlett weight 1/2 -> s = 1 + 2 * 0.5 * (-0.75) = 0.25; var of the
    mean = s / 4."""
    out = bv._nw_var_mean(np.array([1.0, -1.0, 1.0, -1.0]), 1)
    assert float(out) == pytest.approx(0.25 / 4)


def test_the_hac_intercept_regression_is_ols() -> None:
    rng = np.random.default_rng(0)
    z = rng.normal(size=40)
    g = 0.3 + 2.0 * z + rng.normal(0, 0.1, 40)
    a, b, t = (float(v) for v in bv._hac_intercept(g, z, bv.BANDWIDTH))
    slope, intercept = np.polyfit(z, g, 1)
    assert (a, b) == (pytest.approx(intercept), pytest.approx(slope))
    assert t > 0


def test_the_name_cluster_variance_exceeds_the_time_variance_with_persistent_name_effects() -> None:
    """The reason V is a max: when a few names keep winning, the periods look
    independent but are not, and the name-clustered variance is the larger.
    Names n00..n03 win every period regardless of fragility."""
    world = _panel(seed=4)
    boosted = []
    for p in world:
        pay = p.payoff.copy()
        pay[4:8] += 4.0  # four fixed names
        boosted.append(PeriodInputs(**{**p.__dict__, "payoff": pay}))
    panel = bv._prepare(boosted)
    ys = panel.outcomes("rank")
    fit = bv._fit(panel.crosses, ys, panel.n_names)
    v_time = float(bv._nw_var_mean(fit.g, bv.BANDWIDTH)) * T / (T - 1)
    assert bv._name_variance(panel.crosses, ys, fit.g, panel.n_names) > v_time
    assert fit.var == pytest.approx(
        max(bv._name_variance(panel.crosses, ys, fit.g, panel.n_names), v_time)
    )


def test_the_time_variance_binds_when_gamma_is_serially_correlated() -> None:
    """The other branch of the max. gamma_t follows a slow market cycle (a
    sine in the SPY return that scales the effect), so adjacent periods are
    alike and the Newey-West variance is the larger; V must be that, not the
    smaller name-clustered one."""
    spy = 0.02 + 0.015 * np.sin(np.linspace(0, 2.5 * np.pi, T))
    panel = bv._prepare(_panel(seed=6, effect=20.0, spy=spy))
    ys = panel.outcomes("rank")
    fit = bv._fit(panel.crosses, ys, panel.n_names)
    v_time = float(bv._nw_var_mean(fit.g, bv.BANDWIDTH)) * T / (T - 1)
    v_name = bv._name_variance(panel.crosses, ys, fit.g, panel.n_names)
    assert v_time > v_name
    assert fit.var == pytest.approx(v_time)


def test_the_status_rule_is_applied_in_the_documented_order() -> None:
    """p at the line (0.05) is no evidence, whatever else is true; below it a
    failed intercept test or any demotion is not_robust; only all-clear passes."""
    assert bv._status(0.049, 0.01, None) == "predicts_beyond_trailing_vol"
    assert bv._status(0.05, 0.01, None) == "no_evidence_beyond_trailing_vol"
    assert bv._status(0.30, 0.90, "equity_only") == "no_evidence_beyond_trailing_vol"
    assert bv._status(0.049, 0.05, None) == "not_robust"
    assert bv._status(0.049, 0.01, "equity_only") == "not_robust"


def test_the_p_value_counts_ties_with_the_null_as_exceedances() -> None:
    """'The share of the null that is >= t': a t equal to a simulated value
    counts that value."""
    null = np.array([1.0, 2.0, 3.0])
    assert bv._p_upper(null, 2.0) == pytest.approx(2 / 3)
    assert bv._p_upper(null, 2.5) == pytest.approx(1 / 3)


# ==========================================================================
# D3 has no name-count floor beyond identifiability
# ==========================================================================


def test_one_period_with_39_equity_names_does_not_sink_a_clean_pass() -> None:
    """Regression: D3 once applied N_MIN to the equity subset, so a single
    period of 43 names (39 equity, 4 non-equity) made the equity gamma
    unmeasurable and a clean strong effect came out not_robust / equity_only.
    39 equity names fit 28 columns, so the period is measured and D3 stays quiet."""
    world = _panel(seed=2, effect=1.5, n_names=lambda i: 43 if i == 5 else 60)
    assert sum(s not in bv.NON_EQUITY for s in world[5].symbols) == 39
    assert bv._equity_gamma(world[5], world[5].payoff / world[5].cost - 1.0) is not None
    res = bv.beyond_vol_test(world)
    assert res.status == "predicts_beyond_trailing_vol"
    assert res.demotion is None


def test_d3_fires_only_when_fewer_than_half_the_periods_could_be_measured() -> None:
    assert bv._equity_bar([0.1] * 12 + [None] * 12, 24) == pytest.approx(0.1)
    assert bv._equity_bar([0.1] * 11 + [None] * 13, 24) is None
    assert bv._equity_bar([None] * 24, 24) is None
    assert bv._equity_bar([0.3, None, -0.1, None], 4) == pytest.approx(0.1)


def test_d3_ranks_are_recomputed_inside_the_equity_subset() -> None:
    """Non-equity names get extreme payoffs. With ranks recomputed inside the
    subset the equity outcome is a clean rank of the equity ROIs; ranking the
    full sample first and then subsetting gives a different (compressed) y."""
    per = _panel(n_periods=1, seed=3)[0]
    roi = per.payoff / per.cost - 1.0
    roi[:4] = 1e6  # the four non-equity names
    keep = np.array([s not in bv.NON_EQUITY for s in per.symbols])
    X = bv._design(per, keep, with_dummy=False)
    y = stats.rankdata(roi[keep])
    y = (y - 1) / (len(y) - 1) - 0.5
    expected = float(np.linalg.lstsq(X, y, rcond=None)[0][1])
    got = bv._equity_gamma(per, roi)
    assert got == pytest.approx(expected)
    y_wrong = (stats.rankdata(roi) - 1) / (len(roi) - 1) - 0.5
    wrong = float(np.linalg.lstsq(X, y_wrong[keep], rcond=None)[0][1])
    assert abs(wrong - expected) > 1e-6  # the mutation would be visible


# ==========================================================================
# Non-finite payoff cannot fail open
# ==========================================================================


def test_a_non_finite_payoff_is_refused_not_ranked() -> None:
    world = _panel(seed=2, effect=1.5)
    for bad in (np.nan, np.inf, -np.inf):
        pay = world[5].payoff.copy()
        pay[0] = bad
        broken = [*world[:5], replace(world[5], payoff=pay), *world[6:]]
        with pytest.raises(ValueError, match="must be finite"):
            bv.beyond_vol_test(broken)
        spy = [*world[:5], replace(world[5], spy_payoff=bad), *world[6:]]
        with pytest.raises(ValueError, match="must be finite"):
            bv.beyond_vol_test(spy)


def test_non_finite_statistics_are_refused_before_a_p_value_is_computed() -> None:
    with pytest.raises(ValueError, match="gamma_bar is not finite"):
        bv._require_finite(gamma_bar=float("nan"))
    with pytest.raises(ValueError, match="V is not finite"):
        bv._require_finite(gamma_bar=0.1, V=float("inf"))
    bv._require_finite(gamma_bar=0.1, V=0.2, t=-3.0)


@pytest.mark.parametrize(
    ("g", "var", "name"),
    [
        (np.full(T, np.nan), 1.0, "gamma_bar"),
        (np.full(T, 0.1), float("inf"), "V"),  # t would be 0.0: finite, a silent no-evidence
        (np.full(T, 0.1), float("nan"), "V"),
    ],
)
def test_a_non_finite_fit_reaching_the_decision_is_refused(
    monkeypatch: pytest.MonkeyPatch, g: FloatArray, var: float, name: str
) -> None:
    monkeypatch.setattr(bv, "_fit", lambda *_: bv._Fit(g=g, var=var))
    with pytest.raises(ValueError, match=f"{name} is not finite"):
        bv.beyond_vol_test(_panel(seed=2, effect=1.5))


# ==========================================================================
# Critical value == the p-value rule
# ==========================================================================


@pytest.mark.parametrize("n", [20, 100, 1_000, 7_777, 100_000])
def test_the_critical_value_is_the_exact_threshold_of_the_p_rule(n: int) -> None:
    """p < ALPHA exactly when t is strictly above the critical value: at it the
    p-value is ALPHA (not below), one ulp above it is below."""
    null = np.sort(np.random.default_rng(n).standard_normal(n))
    crit = bv._critical_value(null)
    assert bv._p_upper(null, crit) >= bv.ALPHA
    assert bv._p_upper(null, float(np.nextafter(crit, np.inf))) < bv.ALPHA
    assert bv._p_upper(null, float(np.nextafter(crit, -np.inf))) >= bv.ALPHA
    for t in null[:: max(1, n // 50)]:
        assert (bv._p_upper(null, float(t)) < bv.ALPHA) == (t > crit)


def test_the_real_null_critical_value_and_ci90_use_it() -> None:
    null = bv._null_mean_t(T, bv.NULL_DRAWS, bv.BANDWIDTH)
    assert bv._critical_value(null) == null[bv.NULL_DRAWS - 5_000]
    assert bv._p_upper(null, float(null[bv.NULL_DRAWS - 5_000])) == bv.ALPHA


# ==========================================================================
# Nulls are chunk-independent
# ==========================================================================


def test_changing_the_chunk_size_does_not_change_either_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mean = bv._null_mean_t.__wrapped__
    icpt = bv._null_intercept_t.__wrapped__
    monkeypatch.setattr(bv, "_CHUNK", 5_000)
    a_mean, a_icpt = mean(12, 3_000, 4), icpt(12, 3_000, 4)
    for chunk in (7, 1_000, 10_000):
        monkeypatch.setattr(bv, "_CHUNK", chunk)
        np.testing.assert_array_equal(mean(12, 3_000, 4), a_mean)
        np.testing.assert_array_equal(icpt(12, 3_000, 4), a_icpt)


def test_the_intercept_null_is_drawn_outcome_first_then_regressor() -> None:
    rng = np.random.default_rng(bv.NULL_SEED)
    y, z = rng.standard_normal((50, 10)), rng.standard_normal((50, 10))
    expected = np.sort(bv._hac_intercept(y, z, 3)[2])
    np.testing.assert_array_equal(bv._null_intercept_t.__wrapped__(10, 50, 3), expected)


# ==========================================================================
# The variance, pinned against an independent from-scratch implementation
# ==========================================================================


def _shuffled(periods: list[PeriodInputs], seed: int) -> list[PeriodInputs]:
    """Same data, but every period lists its names in a different order."""
    rng = np.random.default_rng(seed)
    out = []
    for p in periods:
        perm = rng.permutation(len(p.symbols))
        out.append(
            replace(
                p,
                symbols=tuple(p.symbols[i] for i in perm),
                signals={k: v[perm] for k, v in p.signals.items()},
                payoff=p.payoff[perm],
                cost=p.cost[perm],
            )
        )
    return out


def _slow_name_variance(periods: list[PeriodInputs]) -> tuple[float, float, float]:
    """(gamma_bar, V_name, V_time), simple loops, no shared code with beyond_vol
    beyond the design matrix and the rank scaling (each pinned on its own)."""
    names = sorted({s for p in periods for s in p.symbols})
    T = len(periods)
    fits = []
    for p in periods:
        X = bv._design(p, np.ones(len(p.symbols), dtype=bool), with_dummy=True)
        y = bv._rank_scaled(p.payoff / p.cost - 1.0)
        XtXi = np.linalg.inv(X.T @ X)
        beta = XtXi @ X.T @ y
        x = X[:, 1]
        others = np.delete(X, 1, axis=1)
        x_res = x - others @ np.linalg.solve(others.T @ others, others.T @ x)
        fits.append((p, X, XtXi, y - X @ beta, x_res, float(beta[1])))
    g = [f[5] for f in fits]
    gbar = sum(g) / T
    score = dict.fromkeys(names, 0.0)
    for p, X, XtXi, u, x_res, g_t in fits:
        for i, sym in enumerate(p.symbols):
            h = float(X[i] @ XtXi @ X[i])
            w_i = float(XtXi[1] @ X[i])  # this name's weight in gamma_t
            e = u[i] / math.sqrt(1.0 - h) + (g_t - gbar) * x_res[i]
            score[sym] += e * w_i / T
    v_name = sum(v * v for v in score.values()) * len(names) / (len(names) - 1)
    dev = [v - gbar for v in g]
    s = sum(d * d for d in dev) / T
    for lag in range(1, bv.BANDWIDTH + 1):
        cov = sum(dev[t] * dev[t - lag] for t in range(lag, T)) / T
        s += 2.0 * (1.0 - lag / (bv.BANDWIDTH + 1.0)) * cov
    return gbar, v_name, s / T * T / (T - 1)


def _name_effect_world() -> list[PeriodInputs]:
    """Varying cross-sections (so skipped/unequal membership), four names that
    win every period, so V_name binds."""
    world = _panel(seed=4, n_periods=24, n_names=lambda i: 44 + (i * 7) % 17)
    out = []
    for p in world:
        pay = p.payoff.copy()
        pay[4:8] += 4.0
        out.append(replace(p, payoff=pay))
    return out


def test_v_name_matches_an_independent_implementation_whatever_the_row_order() -> None:
    """Pins: clustering by NAME (not row position), HC2 residuals, the
    (gamma_t - gamma_bar) * x-tilde term, and the G / (G - 1) factor.
    Dropping any of them moves V_name by far more than the tolerance."""
    world = _name_effect_world()
    gbar, v_name, v_time = _slow_name_variance(world)
    assert v_name > v_time  # so V_name is what binds in the public result
    for order in (world, _shuffled(world, 1), _shuffled(world, 2)):
        panel = bv._prepare(order)
        ys = panel.outcomes("rank")
        fit = bv._fit(panel.crosses, ys, panel.n_names)
        assert fit.gbar == pytest.approx(gbar, rel=1e-9)
        assert bv._name_variance(panel.crosses, ys, fit.g, panel.n_names) == pytest.approx(
            v_name, rel=1e-8
        )
        assert fit.var == pytest.approx(v_name, rel=1e-8)


def test_the_public_ci90_p_and_gamma_follow_the_independent_variance() -> None:
    """End to end: ci90 = gamma_bar +- crit * sqrt(V), p = share of the null >=
    t, with V, crit and p all recomputed independently here."""
    world = _name_effect_world()
    gbar, v_name, v_time = _slow_name_variance(world)
    v = max(v_name, v_time)
    null = bv._null_mean_t(len(world), bv.NULL_DRAWS, bv.BANDWIDTH)
    crit = float(null[len(null) - math.ceil(0.05 * len(null))])
    res = bv.beyond_vol_test(_shuffled(world, 3))
    assert res.gamma_bar == pytest.approx(gbar, rel=1e-9)
    assert res.ci90 is not None
    assert res.ci90.low == pytest.approx(gbar - crit * math.sqrt(v), rel=1e-8)
    assert res.ci90.high == pytest.approx(gbar + crit * math.sqrt(v), rel=1e-8)
    assert res.p_value == pytest.approx(float(np.mean(null >= gbar / math.sqrt(v))), abs=1e-12)
    assert res.log1p_ci90 is not None and res.log1p_gamma_bar is not None
    half = (res.log1p_ci90.high - res.log1p_ci90.low) / 2
    assert res.log1p_gamma_bar == pytest.approx((res.log1p_ci90.high + res.log1p_ci90.low) / 2)
    assert half > 0.0


# ==========================================================================
# Episode masks, which null the a-test reads, frozen constants
# ==========================================================================


def test_episode_masks_follow_walk_forward_episodes_in_the_used_index() -> None:
    """Stress slots 3,4 | 10 | 17,18,19 | 25 become runs of the periods that
    were USED: skipped slots 1 and 12 shift every later index down."""
    small = {1, 12}
    world = _panel(
        seed=2, n_periods=T + 2, n_names=lambda i: 25 if i in small else 60, stress=STRESS
    )
    used = bv._prepare(world).used
    runs = [list(np.flatnonzero(m)) for m in bv._episode_masks(used)]
    slot_to_idx = {p.slot: i for i, p in enumerate(used)}
    assert runs == [[slot_to_idx[s] for s in run] for run in ([3, 4], [10], [17, 18, 19], [25])]
    assert all(used[i].spy_payoff > 0 for run in runs for i in run)
    # and every stressed used period sits in exactly one mask
    assert sum(len(r) for r in runs) == sum(p.spy_payoff > 0 for p in used)


def test_a_slot_gap_between_two_stressed_periods_splits_the_episode() -> None:
    world = _panel(seed=2, n_periods=T + 1, n_names=lambda i: 25 if i == 4 else 60, stress={3, 5})
    runs = [list(np.flatnonzero(m)) for m in bv._episode_masks(bv._prepare(world).used)]
    assert len(runs) == 2  # slot 4 sat out, so 3 and 5 are not one run


def test_the_a_test_reads_the_intercept_null_and_p_reads_the_mean_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _panel(seed=1)  # pure noise: both real p-values are strictly inside (0, 1)
    low = np.full(1_000, -1e9)
    high = np.full(1_000, 1e9)
    low.setflags(write=False)
    high.setflags(write=False)
    # p is the share of the null >= t: a null entirely below t gives 0, above gives 1
    monkeypatch.setattr(bv, "_null_intercept_t", lambda *_: low)
    res = bv.beyond_vol_test(world)
    assert res.a_p_value == 0.0
    assert res.p_value is not None and 0.0 < res.p_value < 1.0  # the mean null is untouched
    monkeypatch.setattr(bv, "_null_intercept_t", lambda *_: high)
    assert bv.beyond_vol_test(world).a_p_value == 1.0
    monkeypatch.undo()
    monkeypatch.setattr(bv, "_null_mean_t", lambda *_: high)
    res = bv.beyond_vol_test(world)
    assert res.p_value == 1.0
    assert res.a_p_value is not None and 0.0 < res.a_p_value < 1.0  # intercept null untouched
    monkeypatch.setattr(bv, "_null_mean_t", lambda *_: low)
    assert bv.beyond_vol_test(world).p_value == 0.0


def test_the_frozen_knots_and_asset_class_list_match_the_plan() -> None:
    assert bv.LEVEL_KNOTS_RV20 == (0.11, 0.19, 0.28, 0.40, 0.78)
    assert bv.LEVEL_KNOTS_RV_LONG == (0.15, 0.25, 0.34, 0.44, 0.68)
    np.testing.assert_allclose(bv.RANK_KNOTS, [-0.4, -0.4 / 3, 0.4 / 3, 0.4], rtol=1e-12)
    assert sorted(bv.NON_EQUITY) == ["dba", "dbc", "gld", "hyg", "lqd", "slv", "tlt", "ung", "uso"]
    assert (bv.BANDWIDTH, bv.NULL_SEED, bv.NULL_DRAWS, bv.N_MIN, bv.ALPHA) == (
        16,
        20261101,
        100_000,
        40,
        0.05,
    )
