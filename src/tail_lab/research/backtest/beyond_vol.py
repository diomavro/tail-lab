"""Does fragility predict put outcomes *beyond trailing volatility*?

A Fama-MacBeth test on the signal test's own periods (``PeriodInputs``). It is
the pre-registered procedure of ``PLAN_FM.md`` round 4 (ADR 0035), frozen as
ONE hard-coded procedure: no flags, options or menu. Changing anything below
changes the test, so its sha256 is recorded before real outcomes are opened.

Per period ``t`` with at least ``N_MIN`` eligible names:

* outcome ``y`` = the rank of the put's ROI within the period (mid-rank ties),
  scaled to [-0.5, 0.5];
* regressor of interest ``x`` = the rank of the composite, same scaling;
* controls = a tensor of restricted cubic splines (RCS) in the ranks of
  ``rv20`` and ``rv_long``, an RCS in the rank of ``rv_ratio``, absolute-level
  RCS in ``log rv20`` and ``log rv_long``, and a non-equity dummy;
* OLS gives the slope ``gamma_t`` on ``x``; ``gamma_bar`` is the mean.

Inference: ``V = max(V_name, V_time * T / (T - 1))`` where ``V_name`` clusters
on names (HC2 residuals) and ``V_time`` is Newey-West (Bartlett) at the fixed
``BANDWIDTH``. The p-value is read off a simulated fixed-b null of the same
t-statistic at the same ``(T, L)`` -- never a normal or t table.

Status, in this order (``insufficient_data`` first):

1. ``no_evidence_beyond_trailing_vol`` when ``p >= ALPHA``;
2. ``not_robust`` when ``p < ALPHA`` but the market-neutral intercept test
   fails, or a demotion (D1, D3) fires;
3. ``predicts_beyond_trailing_vol`` otherwise.

Pure: ``PeriodInputs`` in, a result out; nothing is read from the store.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel
from scipy import stats

from tail_lab.research.backtest.walk_forward import (
    COMPOSITE,
    MIN_EPISODES,
    MIN_PERIODS,
    PeriodInputs,
    _episodes,
    _validate_periods,
)

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.intp]
BoolArray = npt.NDArray[np.bool_]

#: Knots (on the [-0.5, 0.5] rank scale) of the rank splines: PLAN_FM round 1 fix F1.
RANK_KNOTS = (-0.4, -0.4 / 3.0, 0.4 / 3.0, 0.4)
#: Knots (annualised vol, logged inside the spline): pooled 5/27.5/50/72.5/95% quantiles, PLAN_FM round 3.
LEVEL_KNOTS_RV20 = (0.11, 0.19, 0.28, 0.40, 0.78)
#: As above, for the signal-window vol (``rv_long``).
LEVEL_KNOTS_RV_LONG = (0.15, 0.25, 0.34, 0.44, 0.68)
#: Funds whose holdings are not stocks, frozen in PLAN_FM round 3 (gdx, iyr and the crypto equities count as equity).
NON_EQUITY = frozenset({"tlt", "lqd", "hyg", "gld", "slv", "uso", "ung", "dba", "dbc"})
#: Newey-West bandwidth, fixed by Dio on 2026-11-01 (PLAN_FM round 4 §2): not a formula of T.
BANDWIDTH = 16
#: Seed of the fixed-b null simulation (PLAN_FM round 4 §2: the date of the freeze).
NULL_SEED = 20261101
#: Draws in the fixed-b null simulation (PLAN_FM round 4 §2).
NULL_DRAWS = 100_000
#: A period with fewer eligible names is skipped and counted (PLAN_FM round 2
#: "Data"; 40 not 30 at the freeze: the design has 29 columns, and 30 names
#: would leave one residual degree of freedom. The real panel's minimum is 43).
N_MIN = 40
#: One-sided size of every decision, and the 90% CI's tail (PLAN_FM round 4 §2).
ALPHA = 0.05
#: Pay-rate gap (top vs bottom composite quintile) detectable at 80% power.
#: 9.6pp beyond-vol pay-rate gap, top vs bottom composite quintile, at 80% power;
#: frozen-module simulation on the real signal panel, 2000 reps per world, linear
#: interpolation between a=0.20 (8.64pp, power .707) and a=0.25 (10.72pp, .907);
#: ~10.6pp with name effects (alpha sd 0.2). scratchpad fm/frozen_check.txt
MDE_PAY_RATE_GAP = 0.096

_VOL_SIGNALS = ("rv20_high", "rv_long_high", "rv_ratio_high")
_CHUNK = 5_000  # null draws computed at a time (memory only: every draw is made up front, so the nulls are chunk-independent)
_HAT_FLOOR = 1e-12  # 1 - leverage is floored here so a saturated fit cannot divide by zero

Status = Literal[
    "insufficient_data",
    "no_evidence_beyond_trailing_vol",
    "not_robust",
    "predicts_beyond_trailing_vol",
]
Demotion = Literal["intercept", "drop_best_period", "drop_best_episode", "equity_only"]


class Ci90(BaseModel):
    low: float
    high: float


class PeriodGamma(BaseModel):
    date: dt.date
    gamma: float


class BeyondVolTest(BaseModel):
    """The verdict. Every inference field is ``None`` under ``insufficient_data``."""

    status: Status
    #: Mean per-period slope of the outcome rank on the composite rank, controls in.
    gamma_bar: float | None
    ci90: Ci90 | None
    #: One-sided (gamma_bar > 0) fixed-b p-value.
    p_value: float | None
    #: Market-neutral gamma: intercept of ``gamma_t = a + b * spy_return_t``, and its p.
    a_intercept: float | None
    a_p_value: float | None
    b_slope: float | None
    #: Periods that entered the test.
    n_periods: int
    #: Periods dropped for fewer than ``N_MIN`` names (or a rank-deficient design).
    n_skipped: int
    bandwidth: int
    #: The first check that turned a significant result into ``not_robust``.
    demotion: Demotion | None
    #: Companions, none of them tested: no controls; log1p(payoff/cost) with its CI; raw ROI.
    gamma_no_controls: float | None
    log1p_gamma_bar: float | None
    log1p_ci90: Ci90 | None
    raw_roi_gamma_bar: float | None
    per_period: list[PeriodGamma]
    mde_pay_rate_gap: float


# --------------------------------------------------------------------------
# Design: ranks, splines, one cross-section's regression
# --------------------------------------------------------------------------


def _rank_scaled(a: FloatArray) -> FloatArray:
    """Mid-rank of ``a`` scaled to [-0.5, 0.5]."""
    return np.asarray((stats.rankdata(a) - 1.0) / (len(a) - 1.0) - 0.5, dtype=np.float64)


def _pos3(z: FloatArray) -> FloatArray:
    return np.maximum(z, 0.0) ** 3


def _rcs(x: FloatArray, knots: tuple[float, ...]) -> FloatArray:
    """Harrell's restricted cubic spline basis: ``x`` plus ``len(knots) - 2`` terms."""
    t = np.asarray(knots, dtype=np.float64)
    norm = (t[-1] - t[0]) ** 2
    cols = [x]
    for j in range(len(t) - 2):
        cols.append(
            (
                _pos3(x - t[j])
                - _pos3(x - t[-2]) * (t[-1] - t[j]) / (t[-1] - t[-2])
                + _pos3(x - t[-1]) * (t[-2] - t[j]) / (t[-1] - t[-2])
            )
            / norm
        )
    return np.column_stack(cols)


def _controls(rv20: FloatArray, rv_long: FloatArray, rv_ratio: FloatArray) -> FloatArray:
    n = len(rv20)
    u20, u_long = _rank_scaled(rv20), _rank_scaled(rv_long)
    b0 = np.column_stack([np.ones(n), _rcs(u20, RANK_KNOTS)])
    b1 = np.column_stack([np.ones(n), _rcs(u_long, RANK_KNOTS)])
    tensor = (b0[:, :, None] * b1[:, None, :]).reshape(n, -1)[:, 1:]
    return np.hstack(
        [
            tensor,
            _rcs(_rank_scaled(rv_ratio), RANK_KNOTS),
            _rcs(np.log(rv20), tuple(np.log(LEVEL_KNOTS_RV20))),
            _rcs(np.log(rv_long), tuple(np.log(LEVEL_KNOTS_RV_LONG))),
        ]
    )


def _design(per: PeriodInputs, keep: BoolArray, *, with_dummy: bool) -> FloatArray:
    """Columns: constant, composite rank, the controls, then (if ``with_dummy``
    and the period has any non-equity name) the non-equity dummy."""
    sig = per.signals
    cols = [
        np.ones(int(keep.sum())),
        _rank_scaled(sig[COMPOSITE][keep]),
        _controls(sig["rv20_high"][keep], sig["rv_long_high"][keep], sig["rv_ratio_high"][keep]),
    ]
    non_equity = np.array([s in NON_EQUITY for s in np.array(per.symbols)[keep]], dtype=float)
    if with_dummy and non_equity.any():
        cols.append(non_equity)
    return np.column_stack(cols)


@dataclass(frozen=True, eq=False)
class _Cross:
    """One usable cross-section: what turns an outcome vector into gamma_t and
    its name-cluster score."""

    date: dt.date
    gid: IntArray  # global name id per row
    w: FloatArray  # gamma_t = w @ y (row of the OLS pseudo-inverse for x)
    hat: FloatArray
    xt: FloatArray  # x residualised on the other columns


def _cross(per: PeriodInputs, gid_of: dict[str, int]) -> _Cross | None:
    """None if the design is rank deficient."""
    X = _design(per, np.ones(len(per.symbols), dtype=bool), with_dummy=True)
    if np.linalg.matrix_rank(X) < X.shape[1]:
        return None
    pinv = np.linalg.pinv(X)
    Xc = np.delete(X, 1, axis=1)
    xt = X[:, 1] - Xc @ np.linalg.lstsq(Xc, X[:, 1], rcond=None)[0]
    return _Cross(
        date=per.date,
        gid=np.array([gid_of[s] for s in per.symbols], dtype=np.intp),
        w=pinv[1],
        hat=X @ pinv,
        xt=xt,
    )


def _equity_gamma(per: PeriodInputs, y_raw: FloatArray) -> float | None:
    """gamma_t on the equity names alone, ranks recomputed inside the subset.

    No name-count floor beyond identifiability (``N_MIN`` is for the full
    design): ``None`` if the subset has no more rows than columns or the
    design is rank deficient."""
    keep = np.array([s not in NON_EQUITY for s in per.symbols])
    if keep.sum() < 3:
        return None
    X = _design(per, keep, with_dummy=False)
    if X.shape[0] <= X.shape[1] or np.linalg.matrix_rank(X) < X.shape[1]:
        return None
    return float(_rank_scaled(y_raw[keep]) @ np.linalg.pinv(X)[1])


# --------------------------------------------------------------------------
# Inference: variances, the HAC intercept test, the simulated fixed-b null
# --------------------------------------------------------------------------


def _nw_var_mean(g: FloatArray, lags: int) -> FloatArray:
    """Newey-West (Bartlett) variance of the mean of ``g`` along its last axis."""
    T = g.shape[-1]
    e = g - g.mean(-1, keepdims=True)
    s = (e * e).sum(-1) / T
    for lag in range(1, lags + 1):
        s = s + 2.0 * (1.0 - lag / (lags + 1.0)) * (e[..., lag:] * e[..., :-lag]).sum(-1) / T
    return np.asarray(np.maximum(s, 1e-300) / T)


def _name_variance(
    crosses: Sequence[_Cross], ys: Sequence[FloatArray], g: FloatArray, n_names: int
) -> float:
    """Name-clustered variance of gamma_bar: HC2-adjusted residual plus the
    gamma_t deviation, each name's score summed over its whole history."""
    T = len(crosses)
    gbar = g.mean()
    score = np.zeros(n_names)
    for c, y, g_t in zip(crosses, ys, g, strict=True):
        u = y - c.hat @ y
        e = u / np.sqrt(np.maximum(1.0 - np.diag(c.hat), _HAT_FLOOR)) + (g_t - gbar) * c.xt
        score[c.gid] += e * c.w / T
    return float((score**2).sum() * n_names / (n_names - 1))


@dataclass(frozen=True, eq=False)
class _Fit:
    g: FloatArray
    var: float  # V = max(V_name, V_time * T / (T - 1))

    @property
    def gbar(self) -> float:
        return float(self.g.mean())


def _fit(crosses: Sequence[_Cross], ys: Sequence[FloatArray], n_names: int) -> _Fit:
    T = len(crosses)
    g = np.array([float(y @ c.w) for c, y in zip(crosses, ys, strict=True)])
    v_time = float(_nw_var_mean(g, BANDWIDTH)) * T / (T - 1)
    return _Fit(g=g, var=max(_name_variance(crosses, ys, g, n_names), v_time))


def _hac_intercept(
    g: FloatArray, z: FloatArray, bandwidth: int
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """OLS of ``g`` on ``[1, z]`` along the last axis with a Bartlett-HAC
    sandwich at ``bandwidth``: returns (intercept, slope, intercept t)."""
    T = g.shape[-1]
    Z = np.stack([np.ones_like(z), z], axis=-1)  # (..., T, 2)
    ZtZi = np.linalg.inv(np.einsum("...tk,...tl->...kl", Z, Z))
    coef = np.einsum("...kl,...tl,...t->...k", ZtZi, Z, g)
    sc = Z * (g - np.einsum("...tk,...k->...t", Z, coef))[..., None]
    S = np.einsum("...tk,...tl->...kl", sc, sc)
    for lag in range(1, min(bandwidth, T - 1) + 1):
        G = np.einsum("...tk,...tl->...kl", sc[..., lag:, :], sc[..., :-lag, :])
        S = S + (1.0 - lag / (bandwidth + 1.0)) * (G + np.swapaxes(G, -1, -2))
    Vb = ZtZi @ S @ ZtZi
    return coef[..., 0], coef[..., 1], coef[..., 0] / np.sqrt(Vb[..., 0, 0])


@cache
def _null_mean_t(T: int, draws: int, bandwidth: int) -> FloatArray:
    """Sorted fixed-b null of the NW t-statistic: iid N(0,1) series of length T.
    All draws are made first, then computed in chunks, so ``_CHUNK`` cannot
    change the result."""
    g = np.random.default_rng(NULL_SEED).standard_normal((draws, T))
    out = [
        g[i : i + _CHUNK].mean(-1) / np.sqrt(_nw_var_mean(g[i : i + _CHUNK], bandwidth))
        for i in range(0, draws, _CHUNK)
    ]
    return _frozen(np.concatenate(out))


@cache
def _null_intercept_t(T: int, draws: int, bandwidth: int) -> FloatArray:
    """Sorted fixed-b null of the HAC intercept t: iid N(0,1) outcome on an
    iid N(0,1) regressor, both of length T, drawn in that order (outcome, then
    regressor) before any computing, so ``_CHUNK`` cannot change the result.
    The bandwidth is an argument, so it is part of the cache key: a patched
    bandwidth never hits a stale null."""
    rng = np.random.default_rng(NULL_SEED)
    y = rng.standard_normal((draws, T))
    z = rng.standard_normal((draws, T))
    out = [
        _hac_intercept(y[i : i + _CHUNK], z[i : i + _CHUNK], bandwidth)[2]
        for i in range(0, draws, _CHUNK)
    ]
    return _frozen(np.concatenate(out))


def _frozen(sample: FloatArray) -> FloatArray:
    sample = np.sort(sample)
    sample.setflags(write=False)
    return sample


def _p_upper(null: FloatArray, t: float) -> float:
    """Share of the simulated null that is >= ``t``."""
    return float((len(null) - np.searchsorted(null, t, side="left")) / len(null))


def _critical_value(null: FloatArray) -> float:
    """The order statistic the p-value rule implies: ``p < ALPHA`` holds exactly
    when ``t`` is strictly above this value (at it, ``p == ALPHA``)."""
    return float(null[len(null) - math.ceil(ALPHA * len(null))])


# --------------------------------------------------------------------------
# Demotions
# --------------------------------------------------------------------------


def _episode_masks(used: Sequence[PeriodInputs]) -> list[BoolArray]:
    """One mask over ``used`` per stress episode, as ``walk_forward`` defines
    episodes (runs of SPY-put-paid periods on consecutive rebalance slots)."""
    dates = [p.date for p in used]
    masks = []
    for ep in _episodes(used):
        first = dates.index(ep.start)
        mask = np.zeros(len(used), dtype=bool)
        mask[first : first + ep.n_periods] = True
        masks.append(mask)
    return masks


def _mean_without(g: FloatArray, drop: BoolArray) -> float:
    return float(g[~drop].mean()) if not drop.all() else math.inf


def _first_demotion(
    g: FloatArray, masks: Sequence[BoolArray], gamma_equity: float | None
) -> Demotion | None:
    """D1 (the single best period, or the best stress episode, carries the mean)
    and D3 (equity names alone show no effect). An unmeasurable equity subset
    fires D3 (see ``_equity_bar``): a result that cannot be checked is not robust."""
    best = np.zeros(len(g), dtype=bool)
    best[int(np.argmax(g))] = True
    if _mean_without(g, best) <= 0.0:
        return "drop_best_period"
    if any(_mean_without(g, m) <= 0.0 for m in masks):
        return "drop_best_episode"
    if gamma_equity is None or gamma_equity <= 0.0:
        return "equity_only"
    return None


# --------------------------------------------------------------------------
# The test
# --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Panel:
    used: list[PeriodInputs]
    crosses: list[_Cross]
    n_skipped: int
    n_names: int

    def outcomes(self, kind: Literal["rank", "log1p", "raw"]) -> list[FloatArray]:
        out = []
        for per in self.used:
            ratio = per.payoff / per.cost
            if kind == "rank":
                out.append(_rank_scaled(ratio - 1.0))
            else:
                out.append(np.log1p(ratio) if kind == "log1p" else ratio - 1.0)
        return out


def _prepare(periods: Sequence[PeriodInputs]) -> _Panel:
    big = [p for p in periods if len(p.symbols) >= N_MIN]
    names = sorted({s for p in big for s in p.symbols})
    gid_of = {s: i for i, s in enumerate(names)}
    kept = [(p, _cross(p, gid_of)) for p in big]
    used = [(p, c) for p, c in kept if c is not None]
    return _Panel(
        used=[p for p, _ in used],
        crosses=[c for _, c in used],
        n_skipped=len(periods) - len(used),
        n_names=len(names),
    )


def _no_controls_gamma(panel: _Panel) -> float:
    """Mean raw slope of the outcome rank on the composite rank, nothing controlled."""
    slopes = []
    for per, y in zip(panel.used, panel.outcomes("rank"), strict=True):
        x = _rank_scaled(per.signals[COMPOSITE])
        xc = x - x.mean()
        slopes.append(float(xc @ y / (xc @ xc)))
    return float(np.mean(slopes))


def _ci(fit: _Fit, crit: float) -> Ci90:
    half = crit * math.sqrt(fit.var)
    return Ci90(low=fit.gbar - half, high=fit.gbar + half)


def _empty(panel: _Panel) -> BeyondVolTest:
    return BeyondVolTest(
        status="insufficient_data",
        gamma_bar=None,
        ci90=None,
        p_value=None,
        a_intercept=None,
        a_p_value=None,
        b_slope=None,
        n_periods=len(panel.used),
        n_skipped=panel.n_skipped,
        bandwidth=BANDWIDTH,
        demotion=None,
        gamma_no_controls=None,
        log1p_gamma_bar=None,
        log1p_ci90=None,
        raw_roi_gamma_bar=None,
        per_period=[],
        mde_pay_rate_gap=MDE_PAY_RATE_GAP,
    )


def _status(p: float, a_p: float, demotion: Demotion | None) -> Status:
    if p >= ALPHA:
        return "no_evidence_beyond_trailing_vol"
    if a_p >= ALPHA or demotion is not None:
        return "not_robust"
    return "predicts_beyond_trailing_vol"


def _validate(periods: Sequence[PeriodInputs]) -> None:
    _validate_periods(periods, 1, 1)
    for per in periods:
        if any(not np.all(per.signals[k] > 0.0) for k in _VOL_SIGNALS):
            raise ValueError(f"{per.date}: volatility signals must be positive")


@dataclass(frozen=True, eq=False)
class _Verdict:
    fit: _Fit
    crit: float
    p: float
    a: float
    b: float
    a_p: float
    demotion: Demotion | None


def _equity_bar(gammas: Sequence[float | None], n_decision: int) -> float | None:
    """Mean equity-only gamma over the periods that could be measured; ``None``
    (D3 fires) when fewer than half of the ``n_decision`` periods could."""
    measured = [v for v in gammas if v is not None]
    return float(np.mean(measured)) if 2 * len(measured) >= n_decision else None


def _require_finite(**values: float) -> None:
    for name, v in values.items():
        if not math.isfinite(v):
            raise ValueError(f"{name} is not finite ({v}): the test cannot be run")


def _intercept_test(g: FloatArray, panel: _Panel) -> tuple[float, float, float]:
    """(a, b, p of a) from ``gamma_t = a + b * spy_return_t`` against its own fixed-b null."""
    spy = np.array([per.spy_return for per in panel.used])
    if np.ptp(spy) == 0.0:
        raise ValueError("spy_return is constant: the market-neutral intercept is undefined")
    a, b, t_a = (float(v) for v in _hac_intercept(g, spy, BANDWIDTH))
    _require_finite(intercept_t=t_a)
    return a, b, _p_upper(_null_intercept_t(len(panel.used), NULL_DRAWS, BANDWIDTH), t_a)


def _decide(panel: _Panel, masks: Sequence[BoolArray]) -> _Verdict:
    """The tested quantities: gamma_bar's fixed-b p, the intercept test, the demotions."""
    T = len(panel.used)
    fit = _fit(panel.crosses, panel.outcomes("rank"), panel.n_names)
    _require_finite(gamma_bar=fit.gbar, V=fit.var)
    t = fit.gbar / math.sqrt(fit.var)
    _require_finite(t=t)
    t_null = _null_mean_t(T, NULL_DRAWS, BANDWIDTH)
    a, b, a_p = _intercept_test(fit.g, panel)
    gamma_eq = [_equity_gamma(per, per.payoff / per.cost - 1.0) for per in panel.used]
    eq_bar = _equity_bar(gamma_eq, T)
    demotion = "intercept" if a_p >= ALPHA else _first_demotion(fit.g, masks, eq_bar)
    return _Verdict(
        fit=fit,
        crit=_critical_value(t_null),
        p=_p_upper(t_null, t),
        a=a,
        b=b,
        a_p=a_p,
        demotion=demotion,
    )


def beyond_vol_test(periods: Sequence[PeriodInputs]) -> BeyondVolTest:
    """Run the frozen procedure on the signal test's periods (see module doc)."""
    _validate(periods)
    panel = _prepare(periods)
    masks = _episode_masks(panel.used)
    if len(panel.used) < MIN_PERIODS or len(masks) < MIN_EPISODES:
        return _empty(panel)
    v = _decide(panel, masks)
    log1p = _fit(panel.crosses, panel.outcomes("log1p"), panel.n_names)
    raw = _fit(panel.crosses, panel.outcomes("raw"), panel.n_names)
    return BeyondVolTest(
        status=_status(v.p, v.a_p, v.demotion),
        gamma_bar=v.fit.gbar,
        ci90=_ci(v.fit, v.crit),
        p_value=v.p,
        a_intercept=v.a,
        a_p_value=v.a_p,
        b_slope=v.b,
        n_periods=len(panel.used),
        n_skipped=panel.n_skipped,
        bandwidth=BANDWIDTH,
        demotion=v.demotion if v.p < ALPHA else None,
        gamma_no_controls=_no_controls_gamma(panel),
        log1p_gamma_bar=log1p.gbar,
        log1p_ci90=_ci(log1p, v.crit),
        raw_roi_gamma_bar=raw.gbar,
        per_period=[
            PeriodGamma(date=c.date, gamma=float(g))
            for c, g in zip(panel.crosses, v.fit.g, strict=True)
        ],
        mde_pay_rate_gap=MDE_PAY_RATE_GAP,
    )
