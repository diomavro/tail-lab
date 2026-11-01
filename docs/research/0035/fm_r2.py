"""Round-2 frozen Fama-MacBeth spec (PLAN_FM.md r2) + its inference options.

Pure: works on the signal panel (periods_signals_only.pkl) plus outcome
arrays handed in. Vectorised over R replications so the simulation and a
single real run (R = 1) go through the SAME code.

Spec
  y   = within-period mid-rank of ROI, scaled to [-0.5, 0.5]
  x   = within-period rank of the composite (fragility_score), same scaling
  controls (22, round-1 F1): 2-D tensor RCS on (rank rv20, rank rv_long),
        4 knots +-0.4, +-0.4/3 (15 cols); RCS rank rv_ratio (3);
        within-period z of log rv20, log rv_long and their positive part^2 (4)
  per-period OLS -> gamma_t ; gbar = mean; skip periods with n_t < 30.

Inference
  A  Newey-West, L = floor(4 (T/100)^(2/9)), Kiefer-Vogelsang fixed-b cv.
  B  name-cluster bootstrap-t: resample the N names with replacement (whole
     histories), duplicates count twice, re-run the full per-period OLS on the
     resampled rows; t* = (gbar* - gbar) / se_NW*. Units keep the ranks they
     had in the original cross-section (pairs bootstrap of fixed transforms).
  C  two-way cluster (name, period) on the FM estimator written as the linear
     estimator gbar = sum_it w_it y_it / T (Thompson 2011 / CGM):
       V = G/(G-1) V_name + V_period(NW, lag L) - V_het,
     residuals HC2-adjusted (u / sqrt(1-h)), t vs t(min(N_names, T) - 1).

Demotions (can only turn a pass into not_robust)
  D1 gbar without the best period <= 0, or without the best stress episode
     (run of consecutive periods in which SPY's put paid) <= 0
  D2 gamma_t = a + b r_SPY,t ; a <= 0
  D3 equity names only (frozen NON_EQUITY list, ranks recomputed inside the
     subset): gbar_eq <= 0
"""

from __future__ import annotations

import pickle
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
PANEL = HERE / "periods_signals_only.pkl"
COMP = "fragility_score"
VOLS = ("rv20_high", "rv_long_high", "rv_ratio_high")
N_MIN = 30
T_MIN = 24
ALPHA = 0.05
KNOTS_4 = (-0.4, -0.4 / 3, 0.4 / 3, 0.4)

# Frozen asset-class list. Non-equity = the fund's holdings are not stocks.
# Ambiguous, kept as EQUITY: gdx (gold-miner stocks, trades like gold),
# xle/xom/... (energy stocks), iyr (REIT stocks), coin/mara/mstr (crypto-
# linked stocks). Ambiguous, kept as NON-equity: hyg (high-yield credit,
# equity-like beta), dbc/dba (commodity futures).
NON_EQUITY = frozenset({"tlt", "lqd", "hyg", "gld", "slv", "uso", "ung", "dba", "dbc"})

FArr = np.ndarray


def rank_scaled(a: FArr) -> FArr:
    r = stats.rankdata(a, axis=-1)
    n = a.shape[-1]
    return (r - 1.0) / (n - 1.0) - 0.5


def rcs(x: FArr, knots: tuple[float, ...] = KNOTS_4) -> FArr:
    t = np.asarray(knots, float)
    norm = (t[-1] - t[0]) ** 2
    p3 = lambda z: np.maximum(z, 0.0) ** 3  # noqa: E731
    cols = [x]
    for j in range(len(t) - 2):
        cols.append(
            (
                p3(x - t[j])
                - p3(x - t[-2]) * (t[-1] - t[j]) / (t[-1] - t[-2])
                + p3(x - t[-1]) * (t[-2] - t[j]) / (t[-1] - t[-2])
            )
            / norm
        )
    return np.column_stack(cols)


def controls_f1(raw: FArr) -> FArr:
    """22 control columns from the raw (n, 3) vols of one cross-section."""
    n = raw.shape[0]
    u = np.column_stack([rank_scaled(raw[:, j]) for j in range(3)])
    b0 = np.column_stack([np.ones(n), rcs(u[:, 0])])
    b1 = np.column_stack([np.ones(n), rcs(u[:, 1])])
    tp = (b0[:, :, None] * b1[:, None, :]).reshape(n, -1)[:, 1:]
    if SPEC_ABS:  # spec change: absolute-level splines (frozen pooled 5/27.5/50/72.5/95% knots)
        a0 = rcs(np.log(raw[:, 0]), tuple(np.log(ABS_KNOTS_RV20)))
        a1 = rcs(np.log(raw[:, 1]), tuple(np.log(ABS_KNOTS_RVLONG)))
        return np.hstack([tp, rcs(u[:, 2]), a0, a1])
    lr = np.log(raw[:, :2])
    lr = (lr - lr.mean(0)) / lr.std(0)
    return np.hstack([tp, rcs(u[:, 2]), lr, np.maximum(lr, 0.0) ** 2])


SPEC_ABS = __import__("os").environ.get("SPEC_ABS") == "1"
ABS_KNOTS_RV20 = (0.11, 0.19, 0.28, 0.40, 0.78)
ABS_KNOTS_RVLONG = (0.15, 0.25, 0.34, 0.44, 0.68)


SPEC_DUMMY = __import__("os").environ.get("SPEC_DUMMY") == "1"


def design(raw: FArr, comp: FArr, noneq: FArr | None = None) -> FArr:
    X = np.column_stack([np.ones(len(comp)), rank_scaled(comp), controls_f1(raw)])
    if SPEC_DUMMY and noneq is not None and noneq.any():
        X = np.column_stack([X, noneq.astype(float)])  # spec change: asset-class dummy
    return X


def nw_lag(T: int) -> int:
    return int(np.floor(4 * (T / 100.0) ** (2.0 / 9.0)))


def cv_fixed_b(T: int, L: int) -> float:
    """Kiefer-Vogelsang (2005) one-sided 5% cv, Bartlett, b = (L+1)/T."""
    b = (L + 1) / T
    return 1.6449 + 2.1859 * b + 0.3142 * b**2 - 0.3427 * b**3


def nw_var_mean(g: FArr, L: int) -> FArr:
    """NW variance of the mean of g along the last axis (any leading shape)."""
    T = g.shape[-1]
    e = g - g.mean(-1, keepdims=True)
    s = (e * e).sum(-1) / T
    for lag in range(1, L + 1):
        s = s + 2.0 * (1.0 - lag / (L + 1.0)) * (e[..., lag:] * e[..., :-lag]).sum(-1) / T
    return np.maximum(s, 1e-300) / T


@dataclass
class Per:
    idx: int
    syms: tuple[str, ...]
    gid: FArr  # global name id per row
    raw: FArr
    comp: FArr
    x: FArr
    X: FArr  # (n, 24)
    w: FArr  # contrast row for x
    H: FArr  # hat matrix
    xt: FArr  # residualised x
    Hc: FArr  # controls-only hat (null-restricted fit)
    cost: FArr
    regime: str
    eq: FArr  # bool mask equity rows
    w_eq: FArr  # contrast on the equity-only re-ranked design
    spy: int


@dataclass
class Panel:
    periods: list[Per]
    names: list[str]
    skipped: int
    L: int = field(init=False)

    def __post_init__(self) -> None:
        self.L = nw_lag(len(self.periods))

    @property
    def T(self) -> int:
        return len(self.periods)


def load_panel(path: Path = PANEL) -> Panel:
    assert path.name == "periods_signals_only.pkl", "only the blinded panel is allowed"
    sys.path.insert(0, str(Path.home() / "Documents/apps/tail-lab/src"))
    with open(path, "rb") as f:
        ps = pickle.load(f)
    names = sorted({s for p in ps for s in p.symbols})
    gmap = {s: i for i, s in enumerate(names)}
    out, skipped = [], 0
    for p in ps:
        n = len(p.symbols)
        if n < N_MIN:
            skipped += 1
            continue
        raw = np.column_stack([p.signals[v] for v in VOLS]).astype(float)
        comp = np.asarray(p.signals[COMP], float)
        eq = np.array([s not in NON_EQUITY for s in p.symbols])
        X = design(raw, comp, ~eq)
        if np.linalg.matrix_rank(X) < X.shape[1]:
            skipped += 1
            continue
        pinv = np.linalg.pinv(X)
        H = X @ pinv
        Xc = np.delete(X, 1, axis=1)
        xt = X[:, 1] - Xc @ np.linalg.lstsq(Xc, X[:, 1], rcond=None)[0]
        Hc = Xc @ np.linalg.pinv(Xc)
        Xe = design(raw[eq], comp[eq])
        out.append(
            Per(
                len(out),
                p.symbols,
                np.array([gmap[s] for s in p.symbols]),
                raw,
                comp,
                X[:, 1],
                X,
                pinv[1],
                H,
                xt,
                Hc,
                np.asarray(p.cost, float),
                p.regime,
                eq,
                np.linalg.pinv(Xe)[1],
                p.symbols.index("spy"),
            )
        )
    return Panel(out, names, skipped)


# ------------------------------------------------------------ bootstrap draws


@dataclass
class BootSet:
    W: list[FArr]  # per period (B, n_t): weight of each original row in gamma*_t
    ok: FArr  # (B, T) period usable in that draw
    lev_max: FArr  # (B, T) max leverage among resampled rows (diagnostic)


def make_bootset(pan: Panel, B: int, seed: int) -> BootSet:
    rng = np.random.default_rng(seed)
    N = len(pan.names)
    draws = rng.integers(0, N, size=(B, N))
    T = pan.T
    W = [np.zeros((B, p.X.shape[0])) for p in pan.periods]
    ok = np.zeros((B, T), bool)
    lev = np.full((B, T), np.nan)
    for t, p in enumerate(pan.periods):
        pos = np.full(N, -1)
        pos[p.gid] = np.arange(len(p.gid))
        for b in range(B):
            rows = pos[draws[b]]
            rows = rows[rows >= 0]
            if len(rows) < N_MIN:
                continue
            Xb = p.X[rows]
            if np.linalg.matrix_rank(Xb) < Xb.shape[1]:
                continue
            pv = np.linalg.pinv(Xb)
            lev[b, t] = np.max(np.einsum("ij,ji->i", Xb, pv))
            np.add.at(W[t][b], rows, pv[1])
            ok[b, t] = True
    return BootSet(W, ok, lev)


# ------------------------------------------------------------------ the test


@dataclass
class Out:
    gbar: FArr
    tA: FArr
    tC: FArr
    pB: FArr  # bootstrap-t one-sided p
    tBse: FArr  # gbar / sd(gbar*) (diagnostic)
    tC2: FArr  # gbar / sqrt(max(V_name, V_time))
    pW: FArr
    a: FArr
    t_a: FArr
    D1: FArr
    D2: FArr
    D3: FArr
    g: FArr


def run_test(pan: Panel, roi: list[FArr], r_spy: FArr, spy_paid: FArr, boot: BootSet | None, wild: FArr | None = None) -> Out:
    """roi: per period (R, n_t). r_spy, spy_paid: (R, T)."""
    T, L = pan.T, pan.L
    R = roi[0].shape[0]
    N = len(pan.names)
    g = np.empty((R, T))
    geq = np.empty((R, T))
    Y = []
    for t, p in enumerate(pan.periods):
        y = rank_scaled(roi[t])
        Y.append(y)
        g[:, t] = y @ p.w
        geq[:, t] = rank_scaled(roi[t][:, p.eq]) @ p.w_eq
    gbar = g.mean(1)
    tA = gbar / np.sqrt(nw_var_mean(g, L))

    # C: two-way cluster
    Sname = np.zeros((R, N))
    Vhet = np.zeros(R)
    for t, p in enumerate(pan.periods):
        u = Y[t] - Y[t] @ p.H.T
        h = np.diag(p.H)
        e = u / np.sqrt(1.0 - h) + (g[:, t] - gbar)[:, None] * p.xt[None, :]
        c = e * p.w[None, :] / T
        Sname[:, p.gid] += c
        Vhet += (c * c).sum(1)
    Gn = len({i for p in pan.periods for i in p.gid})
    Vname = (Sname**2).sum(1) * Gn / (Gn - 1)
    Vtime = nw_var_mean(g, L) * T / (T - 1)
    V = Vname + Vtime - Vhet
    V = np.where(V > 0, V, np.maximum(Vname, Vtime))
    tC = gbar / np.sqrt(V)
    tC2 = gbar / np.sqrt(np.maximum(Vname, Vtime))

    # W: wild name-cluster bootstrap-t (Rademacher per name, null imposed)
    pW = np.full(R, np.nan)
    if wild is not None:
        Bw = wild.shape[0]
        gw = np.empty((R, Bw, T))
        for t, p in enumerate(pan.periods):
            et = (Y[t] - Y[t] @ p.Hc.T) * p.w[None, :]
            gw[:, :, t] = et @ wild[:, p.gid].T
        tw = gw.mean(2) / np.sqrt(nw_var_mean(gw, L))
        pW = (1 + (tw >= tA[:, None]).sum(1)) / (Bw + 1)

    # B: bootstrap-t
    pB = np.full(R, np.nan)
    tBse = np.full(R, np.nan)
    if boot is not None:
        Bn = boot.ok.shape[0]
        for r0 in range(0, R, 64):
            r1 = min(R, r0 + 64)
            gs = np.empty((r1 - r0, Bn, T))
            for t in range(T):
                gs[:, :, t] = Y[t][r0:r1] @ boot.W[t].T
            okm = boot.ok[None, :, :]
            cnt = boot.ok.sum(1)[None, :]
            gsb = np.where(okm, gs, 0.0).sum(2) / cnt
            if boot.ok.all():
                v = nw_var_mean(gs, L)
            else:  # drop unusable periods per draw (compress then NW)
                v = np.empty(gsb.shape)
                for b in range(Bn):
                    v[:, b] = nw_var_mean(gs[:, b, boot.ok[b]], L)
            tstar = (gsb - gbar[r0:r1, None]) / np.sqrt(v)
            pB[r0:r1] = (1 + (tstar >= tA[r0:r1, None]).sum(1)) / (Bn + 1)
            tBse[r0:r1] = gbar[r0:r1] / gsb.std(1, ddof=1)

    # D2: gamma_t = a + b r_spy  (HAC t for a, diagnostic / spec-change test)
    a = np.empty(R)
    t_a = np.empty(R)
    for r in range(R):
        Z = np.column_stack([np.ones(T), r_spy[r]])
        ZtZi = np.linalg.inv(Z.T @ Z)
        coef = ZtZi @ Z.T @ g[r]
        res = g[r] - Z @ coef
        sc = Z * res[:, None]
        S = sc.T @ sc
        for lag in range(1, L + 1):
            G_ = sc[lag:].T @ sc[:-lag]
            S += (1 - lag / (L + 1)) * (G_ + G_.T)
        Vb = ZtZi @ S @ ZtZi
        a[r] = coef[0]
        t_a[r] = coef[0] / np.sqrt(Vb[0, 0])
    D2 = a <= 0

    # D1
    tot = g.sum(1)
    drop_best = (tot - g.max(1)) / (T - 1)
    start = spy_paid & ~np.concatenate([np.zeros((R, 1), bool), spy_paid[:, :-1]], 1)
    run_id = np.cumsum(start, 1) * spy_paid  # 0 = not in an episode
    worst_after = np.full(R, np.inf)
    for k in range(1, int(run_id.max(initial=0)) + 1):
        m = run_id == k
        ln = m.sum(1)
        has = ln > 0
        after = np.where(has, (tot - (g * m).sum(1)) / np.maximum(T - ln, 1), np.inf)
        worst_after = np.minimum(worst_after, after)
    D1 = (drop_best <= 0) | (worst_after <= 0)
    D3 = geq.mean(1) <= 0
    return Out(gbar, tA, tC, pB, tBse, tC2, pW, a, t_a, D1, D2, D3, g)


def decide(pan: Panel, o: Out) -> dict[str, FArr]:
    """Raw pass and final (after demotions) for each inference option."""
    T = pan.T
    dfC = min(len({i for p in pan.periods for i in p.gid}), T) - 1
    raw = {
        "A": o.tA > cv_fixed_b(T, pan.L),
        "B": o.pB < ALPHA,
        "C": o.tC > stats.t.isf(ALPHA, dfC),
        "Bse": o.tBse > stats.norm.isf(ALPHA),
        "C2": o.tC2 > cv_fixed_b(T, pan.L),
        "W": o.pW < ALPHA,
        "a_tA": o.t_a > cv_fixed_b(T, pan.L),  # spec-change candidate: test a
    }
    raw["C2a"] = raw["C2"] & raw["a_tA"]  # spec change: D2 as a test on a
    raw["Wa"] = raw["W"] & raw["a_tA"]
    dem = o.D1 | o.D2 | o.D3
    out = {}
    for k, v in raw.items():
        out[k] = v & (T >= T_MIN)
        out[k + "+dem"] = out[k] & ~dem
    return out
