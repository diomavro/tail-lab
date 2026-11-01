"""Simulation gate for PLAN_FM.md: Fama-MacBeth "fragility beyond vol" test.

Uses ONLY periods_signals_only.pkl (outcomes blanked). Outcomes are simulated
on the real signal panel. Run:  python fm_sim.py [reps]
"""

from __future__ import annotations

import pickle
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
PANEL = HERE / "periods_signals_only.pkl"
COMP = "fragility_score"
VOLS = ("rv20_high", "rv_long_high", "rv_ratio_high")
N_MIN = 20
ALPHA = 0.05
FArr = np.ndarray


# ---------------------------------------------------------------- the test


def rank_scaled(a: FArr) -> FArr:
    """Cross-sectional mid-rank scaled to [-0.5, 0.5]; works on the last axis."""
    r = stats.rankdata(a, axis=-1)
    n = a.shape[-1]
    return (r - 1.0) / (n - 1.0) - 0.5


def rcs_basis(x: FArr, knots: tuple[float, ...]) -> FArr:
    """Harrell restricted (natural) cubic spline: x plus K-2 nonlinear terms."""
    t = np.asarray(knots, dtype=float)
    k = len(t)
    cols = [x]
    norm = (t[-1] - t[0]) ** 2
    p3 = lambda z: np.maximum(z, 0.0) ** 3  # noqa: E731
    for j in range(k - 2):
        c = (
            p3(x - t[j])
            - p3(x - t[-2]) * (t[-1] - t[j]) / (t[-1] - t[-2])
            + p3(x - t[-1]) * (t[-2] - t[j]) / (t[-1] - t[-2])
        )
        cols.append(c / norm)
    return np.column_stack(cols)


KNOTS_PLAN = (-0.4, 0.0, 0.4)  # 10/50/90th pct of a [-0.5, 0.5] rank
KNOTS_4 = (-0.4, -0.4 / 3, 0.4 / 3, 0.4)
KNOTS_NS = (-0.5, -0.4, 0.0, 0.4, 0.5)  # R ns() reading: boundary at range


def controls(per_vol_ranks: FArr, raw: FArr, spec: str) -> FArr:
    """Control columns (no intercept). per_vol_ranks / raw: (n, 3)."""
    u = per_vol_ranks
    if spec == "none":
        return np.empty((u.shape[0], 0))
    if spec == "plan":
        return np.hstack([rcs_basis(u[:, j], KNOTS_PLAN) for j in range(3)])
    if spec == "plan_ns":
        return np.hstack([rcs_basis(u[:, j], KNOTS_NS) for j in range(3)])
    if spec in ("tensor", "tensor_raw"):
        # additive splines + all pairwise products of the linear ranks and of
        # rank x spline-term + the triple product
        base = [rcs_basis(u[:, j], KNOTS_PLAN) for j in range(3)]
        cols = list(base)
        for a in range(3):
            for b in range(a + 1, 3):
                cols.append((base[a][:, :, None] * base[b][:, None, :]).reshape(len(u), -1))
        cols.append((u[:, 0] * u[:, 1] * u[:, 2])[:, None])
        if spec == "tensor_raw":
            lr = np.log(raw)
            lr = (lr - lr.mean(0)) / lr.std(0)
            cols.append(lr[:, :2])  # log ratio = log rv20 - log rv_long exactly
            cols.append(np.maximum(lr, 0.0) ** 2)
        return np.hstack(cols)
    if spec.startswith("t2d"):
        # rv_ratio_high == rv_long/rv20 EXACTLY, so every vol function is a
        # function of (rv20, rv_long) = of (rank rv20, rank rv_long) within a
        # period: a full 2-D tensor-product spline is the complete control.
        kn = {"t2d": KNOTS_PLAN, "t2d4": KNOTS_4}[spec.split("+")[0]]
        b0 = np.column_stack([np.ones(len(u)), rcs_basis(u[:, 0], kn)])
        b1 = np.column_stack([np.ones(len(u)), rcs_basis(u[:, 1], kn)])
        tp = (b0[:, :, None] * b1[:, None, :]).reshape(len(u), -1)[:, 1:]  # drop const
        cols = [tp, rcs_basis(u[:, 2], kn)]
        if "+tr" in spec:  # plus raw-scale (log) terms for skewed tails
            lr = np.log(raw[:, :2])
            lr = (lr - lr.mean(0)) / lr.std(0)
            cols += [lr, np.maximum(lr, 0.0) ** 2]
        if spec.endswith("+tr2"):  # plus raw LEVEL terms and their product
            zr = (raw[:, :2] - raw[:, :2].mean(0)) / raw[:, :2].std(0)
            cols += [zr, (zr[:, 0] * zr[:, 1])[:, None]]
        return np.hstack(cols)
    if spec == "poly3":
        # full cubic polynomial in the three ranks, all interactions (19 cols)
        from itertools import combinations_with_replacement

        cols = []
        for d in (1, 2, 3):
            for idx in combinations_with_replacement(range(3), d):
                cols.append(np.prod(u[:, list(idx)], axis=1))
        return np.column_stack(cols)
    raise ValueError(spec)


@dataclass(frozen=True)
class Period:
    n: int
    comp: FArr  # raw composite
    x: FArr  # rank-scaled composite
    u: FArr  # (n,3) rank-scaled vols
    raw: FArr  # (n,3) raw vols
    cost: FArr
    regime: str


def load_panel() -> list[Period]:
    sys.path.insert(0, str(Path.home() / "Documents/apps/tail-lab/src"))
    with open(PANEL, "rb") as f:
        periods = pickle.load(f)
    out = []
    for p in periods:  # all 81 slots consecutive (checked: slot 0..80)
        n = len(p.symbols)
        if n < N_MIN:
            continue
        raw = np.column_stack([p.signals[v] for v in VOLS]).astype(float)
        u = np.column_stack([rank_scaled(raw[:, j]) for j in range(3)])
        comp = np.asarray(p.signals[COMP], float)
        out.append(Period(n, comp, rank_scaled(comp), u, raw, np.asarray(p.cost, float), p.regime))
    return out


def contrast(per: Period, spec: str) -> FArr:
    """Row of (X'X)^-1 X' picking the composite's OLS coefficient."""
    X = np.column_stack([np.ones(per.n), per.x, controls(per.u, per.raw, spec)])
    if np.linalg.matrix_rank(X) < X.shape[1]:
        raise ValueError(f"rank-deficient design for {spec}")
    return np.linalg.pinv(X)[1]


def nw_lag(T: int) -> int:
    return int(np.floor(4 * (T / 100.0) ** (2.0 / 9.0)))


def cv_fixed_b(T: int, L: int) -> float:
    """Kiefer-Vogelsang (2005) fixed-b one-sided 5% critical value, Bartlett
    kernel, b = M/T with M = L+1 (their bandwidth convention)."""
    b = (L + 1) / T
    return 1.6449 + 2.1859 * b + 0.3142 * b**2 - 0.3427 * b**3


def fm_tstat(g: FArr, L: int | None = None) -> tuple[FArr, FArr]:
    """g: (R, T) per-period slopes. Returns (t, one-sided p) per row."""
    R, T = g.shape
    L = nw_lag(T) if L is None else L
    gbar = g.mean(1)
    e = g - gbar[:, None]
    s = (e * e).sum(1) / T
    for lag in range(1, L + 1):
        w = 1.0 - lag / (L + 1.0)
        s += 2.0 * w * (e[:, lag:] * e[:, :-lag]).sum(1) / T
    se = np.sqrt(np.maximum(s, 1e-300) / T)
    t = gbar / se
    return t, stats.t.sf(t, df=T - 1)


# ------------------------------------------------------------ outcome worlds

LOGIT_BASE = np.log(0.2 / 0.8)  # ~20% of puts pay something


def z(a: FArr) -> FArr:
    return (a - a.mean()) / a.std()


@dataclass
class WorldCtx:
    rng: np.random.Generator
    R: int
    T: int
    shock: FArr  # (R, T) persistent common shock, used by some worlds


def simulate(
    panel: list[Period],
    logit_fn: Callable[[Period, int, WorldCtx], FArr],
    mu_fn: Callable[[Period, int, WorldCtx], FArr] | None,
    sig_fn: Callable[[Period, int, WorldCtx], FArr] | None,
    specs: list[str],
    R: int,
    seed: int,
    xs_corr: float = 0.0,
    strength: float = 1.0,
) -> dict[str, FArr]:
    """Returns one-sided p (R,) per spec. Outcomes ROI = payoff/cost - 1,
    payoff = 0 unless the put pays; |payoff| lognormal(mu, sig).
    xs_corr: equicorrelation of the latent pay index inside a period."""
    rng = np.random.default_rng(seed)
    T = len(panel)
    # regime-persistent AR(1) shock, phi = 0.85
    sh = np.zeros((R, T))
    sh[:, 0] = rng.standard_normal(R) / np.sqrt(1 - 0.85**2)
    for t in range(1, T):
        sh[:, t] = 0.85 * sh[:, t - 1] + rng.standard_normal(R)
    ctx = WorldCtx(rng, R, T, sh)
    C = {s: [contrast(p, s) for p in panel] for s in specs}
    G = {s: np.empty((R, T)) for s in specs}
    G_raw = np.empty((R, T))
    for t, p in enumerate(panel):
        lg = LOGIT_BASE + strength * (logit_fn(p, t, ctx) - LOGIT_BASE)  # (n,)|(R,n)
        lg = np.broadcast_to(lg, (R, p.n))
        # latent-threshold pay draw with within-period equicorrelation
        e = np.sqrt(1 - xs_corr) * rng.logistic(size=(R, p.n))
        if xs_corr > 0:
            e = e + np.sqrt(xs_corr) * 1.8 * rng.standard_normal((R, 1))
        pay = (lg + e) > 0
        mu = np.broadcast_to(0.4 + strength * (mu_fn(p, t, ctx) - 0.4) if mu_fn else 0.4, (R, p.n))
        sg = np.broadcast_to(sig_fn(p, t, ctx) if sig_fn else 1.0, (R, p.n))
        mag = np.minimum(np.exp(mu + sg * rng.standard_normal((R, p.n))), 25.0)
        roi = np.where(pay, mag, 0.0) / p.cost - 1.0
        y = rank_scaled(roi)
        for s in specs:
            G[s][:, t] = y @ C[s][t]
        G_raw[:, t] = roi @ C[specs[0]][t]
    out = {s: fm_tstat(G[s])[0] for s in specs}  # NW t-stats
    out["_raw_roi_secondary"] = fm_tstat(G_raw)[0]
    return out


def ci(k: int, n: int) -> tuple[float, float]:
    lo = stats.beta.ppf(0.025, k, n - k + 1) if k > 0 else 0.0
    hi = stats.beta.ppf(0.975, k + 1, n - k) if k < n else 1.0
    return lo, hi


# ---------------------------------------------------------------- the worlds
# Vol effects are on the RAW vol scale (z-scored within period) unless noted;
# strength ~1 logit per SD => pay rate ~10% to ~40% across the vol range.

Z = lambda p, j: z(p.raw[:, j])  # noqa: E731
LZ = lambda p, j: z(np.log(p.raw[:, j]))  # noqa: E731


def w_n1(p, t, c):
    return LOGIT_BASE + 1.0 * Z(p, 0)


def w_n2(p, t, c):
    zr = Z(p, 2)
    return LOGIT_BASE + 0.6 * (zr + 0.5 * zr**2 - 0.5)


def w_n3(p, t, c):
    return LOGIT_BASE + 1.2 * (np.abs(Z(p, 0)) - 0.8)


def w_n4(p, t, c):
    return LOGIT_BASE + 1.5 * (p.raw[:, 1] > 0.45) - 0.3


def w_n5(p, t, c):
    return (w_n1(p, t, c) + w_n2(p, t, c) + w_n3(p, t, c) + w_n4(p, t, c)) / 4


def sig_n5(p, t, c):
    return 0.5 + 1.5 * p.raw[:, 0]  # heteroscedastic in raw rv20


def w_n6(p, t, c):
    return LOGIT_BASE + 0.8 * Z(p, 1)


def mu_n6(p, t, c):
    return 0.4 + 0.5 * Z(p, 0)


def w_n7(p, t, c):
    s = c.shock[:, t][:, None]  # persistent level AND slope shock
    return LOGIT_BASE + 0.5 * s + (0.8 + 0.6 * s) * Z(p, 0)[None, :]


# adversarial nulls
def w_a_inter(p, t, c):
    return LOGIT_BASE + 0.8 * Z(p, 0) * Z(p, 1)


def w_a_inter_r(p, t, c):
    return LOGIT_BASE + 0.8 * Z(p, 0) * Z(p, 2)


def w_a_sum(p, t, c):  # logistic of an additive index => non-additive p
    return LOGIT_BASE + 1.0 * (Z(p, 0) + Z(p, 1) + Z(p, 2))


def w_a_hockey(p, t, c):  # only extreme RAW rv20 names pay more
    return LOGIT_BASE + 3.0 * np.maximum(p.raw[:, 0] - 0.6, 0.0) / 0.3


def w_a_slopes(p, t, c):  # period-varying slopes on ALL three vols, persistent
    s = c.shock[:, t][:, None]
    return LOGIT_BASE + (s * 0.8) * Z(p, 0)[None] - (s * 0.8) * Z(p, 2)[None] + 0.6 * Z(p, 1)[None]


def w_a_worst(p, t, c):
    """Vol-only, adversarially aligned with the composite: the part of the
    composite rank that a local (k=10 nearest neighbours in (rank rv20, rank
    rv_long)) vol smoother explains BEYOND the plan's additive spline fit."""
    q = p.u[:, :2]
    d2 = ((q[:, None, :] - q[None, :, :]) ** 2).sum(-1)
    nn = np.argsort(d2, axis=1)[:, :10]
    fk = p.x[nn].mean(1)
    Xa = np.column_stack([np.ones(p.n), controls(p.u, p.raw, "plan")])
    fa = Xa @ np.linalg.lstsq(Xa, p.x, rcond=None)[0]
    d = fk - fa
    return LOGIT_BASE + 1.0 * d / (d.std() + 1e-12)


def w_n4s(p, t, c):  # smooth threshold (logistic ramp, width 0.05 in raw vol)
    return LOGIT_BASE + 1.5 / (1 + np.exp(-(p.raw[:, 1] - 0.45) / 0.05)) - 0.3


def w_a_xscorr(p, t, c):  # N1 but with within-period correlated pay draws
    return w_n1(p, t, c)


def alt(a: float):
    # base = smooth vol effect every controlled spec models (rank-linear), so
    # power is not inflated by leakage; a = logit per SD of the composite rank
    def f(p, t, c):
        return LOGIT_BASE + 0.8 * z(p.u[:, 0]) + 0.5 * z(p.u[:, 1]) + a * z(p.x)

    return f


def w_n0(p, t, c):  # no vol effect at all (pure noise baseline)
    return np.full(p.n, LOGIT_BASE)


NULLS = {
    "N0 pure noise": (w_n0, None, None, 0.0),
    "N1 linear rv20": (w_n1, None, None, 0.0),
    "N2 convex rv_ratio": (w_n2, None, None, 0.0),
    "N3 V-shape rv20": (w_n3, None, None, 0.0),
    "N4 threshold rv_long (sharp)": (w_n4, None, None, 0.0),
    "N4s threshold rv_long (smooth)": (w_n4s, None, None, 0.0),
    "N5 mix + het(raw rv20)": (w_n5, None, sig_n5, 0.0),
    "N6 pay(rv_long)+size(rv20)": (w_n6, mu_n6, None, 0.0),
    "N7 persistent level+slope shock": (w_n7, None, None, 0.0),
    "X1 interaction rv20*rv_long": (w_a_inter, None, None, 0.0),
    "X2 interaction rv20*rv_ratio": (w_a_inter_r, None, None, 0.0),
    "X3 logit additive (p non-additive)": (w_a_sum, None, None, 0.0),
    "X4 raw-rv20 hockey stick": (w_a_hockey, None, None, 0.0),
    "X5 persistent slopes on all vols": (w_a_slopes, None, None, 0.0),
    "X6 N1 + within-period corr 0.3": (w_a_xscorr, None, None, 0.3),
    "X7 adversarial: kNN-proj beyond plan": (w_a_worst, None, None, 0.0),
}
ALTS = {
    "ALT tiny (+0.05 logit/SD comp)": (alt(0.05), None, sig_n5, 0.0),
    "ALT small (+0.10 logit/SD comp)": (alt(0.10), None, sig_n5, 0.0),
    "ALT medium (+0.20 logit/SD comp)": (alt(0.20), None, sig_n5, 0.0),
    "ALT large (+0.40 logit/SD comp)": (alt(0.40), None, sig_n5, 0.0),
}
ALT0 = {"ALT zero (base only, size check)": (alt(0.0), None, sig_n5, 0.0)}

SPECS = ["plan", "plan_ns", "none", "tensor", "poly3", "t2d4", "t2d4+tr", "t2d4+tr2"]


def collinearity(panel: list[Period]) -> None:
    print("Per-period R^2 of composite rank on controls (median [min, max]); resid SD of x")
    for s in SPECS[:1] + SPECS[1:2] + SPECS[3:]:
        r2, sd = [], []
        for p in panel:
            X = np.column_stack([np.ones(p.n), controls(p.u, p.raw, s)])
            res = p.x - X @ np.linalg.lstsq(X, p.x, rcond=None)[0]
            r2.append(1 - res.var() / p.x.var())
            sd.append(res.std())
        r2 = np.array(r2)
        print(
            f"  {s:11s} k={X.shape[1] - 1:2d}  R2 {np.median(r2):.3f} "
            f"[{r2.min():.3f}, {r2.max():.3f}]  resid SD {np.median(sd):.3f} (x SD {p.x.std():.3f})"
        )
    # pairwise rank correlations
    cs = np.array([[np.corrcoef(p.x, p.u[:, j])[0, 1] for j in range(3)] for p in panel])
    print("  median corr(comp rank, vol rank) rv20/rv_long/rv_ratio:", np.median(cs, 0).round(3))


def main() -> None:
    global CVMODE
    R = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    CVMODE = sys.argv[2] if len(sys.argv) > 2 else "t"  # "t" (plan) | "fixedb"
    panel = load_panel()
    print(
        f"T={len(panel)} periods, n {min(p.n for p in panel)}..{max(p.n for p in panel)}, NW lag {nw_lag(len(panel))}"
    )
    collinearity(panel)
    T = len(panel)
    global CV
    CV = stats.t.isf(ALPHA, T - 1) if CVMODE == "t" else cv_fixed_b(T, nw_lag(T))
    print(f"critical value mode={CVMODE}: {CV:.4f}")
    names = SPECS + ["_raw_roi_secondary"]
    print(f"\nR={R} reps; rejection rate at alpha={ALPHA} [95% Clopper-Pearson]")
    print("world|strength|" + "|".join(n if n[0] != "_" else "raw_roi(plan)" for n in names))
    runs = [(nm, w, st) for st in (1.0, 0.5) for nm, w in NULLS.items()]
    runs += [(nm, w, 1.0) for nm, w in {**ALT0, **ALTS}.items()]
    for i, (name, (lf, mf, sf, xc), st) in enumerate(runs):
        res = simulate(panel, lf, mf, sf, SPECS, R, seed=1000 + i, xs_corr=xc, strength=st)
        cells = []
        for s in names:
            k = int((res[s] > CV).sum())
            lo, hi = ci(k, R)
            cells.append(f"{k / R:.3f} [{lo:.3f},{hi:.3f}]")
        print(f"{name}|{st}|" + "|".join(cells), flush=True)


if __name__ == "__main__":
    main()
