"""Round-2 simulation gate for fm_r2 (uses ONLY periods_signals_only.pkl).

python fm_r2_sim.py R [world-substring ...]
"""

from __future__ import annotations

import sys
import time
from multiprocessing import Pool

import fm_r2 as F
import numpy as np
from scipy import stats

LOGIT_BASE = np.log(0.2 / 0.8)
TAU = 28 / 365
D1Z = stats.norm.ppf(0.90)  # 0.10-delta put: N(d1) = 0.9
PAN: F.Panel
BOOTS: list[F.BootSet] = []
B_DRAWS = int(__import__("os").environ.get("BDRAWS", 399))
N_BOOTSETS = 4

SECTOR = {}
for k, ss in {
    "index": "spy qqq dia iwm",
    "intl": "efa eem ewj ewz fxi",
    "tech": "aapl amd asml avgo googl meta msft mu nvda mrvl smh tsm xlk smci pltr nflx amzn arkk vrt",
    "energy": "cvx oxy slb vlo xom xle uso ung ccj vst",
    "fin": "jpm kre xlf",
    "crypto": "coin mara mstr",
    "cons": "tsla rivn f ccl dal ba xly xrt xhb iyt",
    "defn": "xlu xlp xlv xbi iyr xli xlb",
    "metal": "fcx gdx gld slv dbc dba",
    "bond": "tlt lqd hyg",
}.items():
    for s in ss.split():
        SECTOR[s] = k


def z(a):
    return (a - a.mean(-1, keepdims=True)) / a.std(-1, keepdims=True)


# --------------------------------------------------------------- generators


def logit_world(lg_fn, R, rng, mu_fn=None, sig_fn=None, xs_corr=0.0, alpha_sd=0.0, alpha_vec=None):
    pan = PAN
    T = pan.T
    sh = np.zeros((R, T))
    sh[:, 0] = rng.standard_normal(R) / np.sqrt(1 - 0.85**2)
    for t in range(1, T):
        sh[:, t] = 0.85 * sh[:, t - 1] + rng.standard_normal(R)
    N = len(pan.names)
    alpha = np.zeros((R, N))
    if alpha_sd:
        alpha += alpha_sd * rng.standard_normal((R, N))
    if alpha_vec is not None:
        alpha += alpha_vec[None, :]
    roi, paid = [], np.empty((R, T), bool)
    for t, p in enumerate(pan.periods):
        lg = np.broadcast_to(lg_fn(p, t, sh), (R, len(p.gid))) + alpha[:, p.gid]
        e = np.sqrt(1 - xs_corr) * rng.logistic(size=lg.shape)
        if xs_corr:
            e = e + np.sqrt(xs_corr) * 1.8 * rng.standard_normal((R, 1))
        pay = (lg + e) > 0
        mu = np.broadcast_to(mu_fn(p) if mu_fn else 0.4, lg.shape)
        sg = np.broadcast_to(sig_fn(p) if sig_fn else 1.0, lg.shape)
        mag = np.minimum(np.exp(mu + sg * rng.standard_normal(lg.shape)), 25.0)
        roi.append(np.where(pay, mag, 0.0) / p.cost - 1.0)
        paid[:, t] = pay[:, p.spy]
    r_spy = 0.05 * rng.standard_normal((R, T))  # unrelated to outcomes here
    return roi, r_spy, paid


def bs_put(sig):
    """Unit spot, r=0: 0.10-delta strike K and premium P at vol sig."""
    s = sig * np.sqrt(TAU)
    lnK = 0.5 * s * s - D1Z * s
    K = np.exp(lnK)
    d1 = D1Z
    d2 = d1 - s
    P = K * stats.norm.cdf(-d2) - stats.norm.cdf(-d1)
    return K, P


def name_resid_beta(rng, R, rho=0.7, spread=0.35):
    """beta_i = 1 + spread*(rho z(mean resid composite) + sqrt(1-rho^2) xi)."""
    pan = PAN
    N = len(pan.names)
    acc, cnt = np.zeros(N), np.zeros(N)
    for p in pan.periods:
        acc[p.gid] += p.xt
        cnt[p.gid] += 1
    rbar = acc / cnt
    zr = (rbar - rbar.mean()) / rbar.std()
    xi = rng.standard_normal((R, N))
    beta = 1 + spread * (rho * zr[None, :] + np.sqrt(1 - rho**2) * xi)
    beta[:, pan.names.index("spy")] = 1.0
    return beta


def return_world(
    R, rng, drift=0.0, beta_mode="resid", sector=False, price_vol="true", realvol=None, ar_mis=0.0, tail_k=0.0
):
    """One-factor (+ optional sector factors) world. True total vol = rv20,
    unless realvol(p) gives another realised vol. Pricing at price_vol."""
    pan = PAN
    T, N = pan.T, len(pan.names)
    beta = name_resid_beta(rng, R) if beta_mode == "resid" else np.ones((R, N))
    tail = tail_k * (name_resid_beta(rng, R) - 1.0) / 0.35  # extra DOWNSIDE loading ~ resid composite
    secs = sorted(set(SECTOR.values()))
    sid = np.array([secs.index(SECTOR[s]) for s in pan.names])
    phi, sdf, sdmu = 0.95, 0.15, 0.30
    mu_s = rng.standard_normal((R, len(secs))) * sdmu
    mis = rng.standard_normal((R, N)) * ar_mis  # AR(1) log-vol mispricing per name
    roi, paid, r_spy = [], np.empty((R, T), bool), np.empty((R, T))
    for t, p in enumerate(pan.periods):
        rv20 = p.raw[:, 0]
        sig_true = realvol(p) if realvol is not None else rv20
        if ar_mis:
            if t:
                mis = 0.8 * mis + np.sqrt(1 - 0.64) * ar_mis * rng.standard_normal((R, N))
            sig_true = sig_true[None, :] * np.exp(mis[:, p.gid])
        sig_true = np.broadcast_to(sig_true, (R, len(p.gid)))
        sm = 0.9 * rv20[p.spy]
        b = np.minimum(beta[:, p.gid], 0.95 * sig_true / sm)
        b = np.maximum(b, 0.0)
        m = drift * TAU + sm * np.sqrt(TAU) * rng.standard_normal((R, 1))
        var_rest = sig_true**2 - (b * sm) ** 2
        lr = b * m
        if sector:
            mu_s = phi * mu_s + np.sqrt(1 - phi**2) * sdmu * rng.standard_normal(mu_s.shape)
            f = mu_s * TAU + sdf * np.sqrt(TAU) * rng.standard_normal(mu_s.shape)
            fs = np.minimum(sdf**2, 0.6 * var_rest)  # sector variance share
            lr = lr + np.sqrt(fs / sdf**2) * f[:, sid[p.gid]]
            var_rest = var_rest - fs
        lr = lr - 0.5 * sig_true**2 * TAU + np.sqrt(var_rest * TAU) * rng.standard_normal(lr.shape)
        if tail_k:
            lr = lr + tail[:, p.gid] * np.minimum(m, 0.0)
        sp = rv20 if price_vol == "trailing" else sig_true
        K, P = bs_put(sp)
        payoff = np.maximum(K - np.exp(lr), 0.0) / P
        roi.append(payoff / p.cost - 1.0)
        paid[:, t] = payoff[:, p.spy] > 0
        r_spy[:, t] = np.exp(lr[:, p.spy]) - 1.0
    return roi, r_spy, paid


# ------------------------------------------------------------------ worlds


def Zr(p, j):
    return z(p.raw[:, j])


def U(p, j):
    return F.rank_scaled(p.raw[:, j])


def w_n0(p, t, sh):
    return np.full(len(p.gid), LOGIT_BASE)


def w_n1(p, t, sh):
    return LOGIT_BASE + Zr(p, 0)


def w_n2(p, t, sh):
    zr = Zr(p, 2)
    return LOGIT_BASE + 0.6 * (zr + 0.5 * zr**2 - 0.5)


def w_n3(p, t, sh):
    return LOGIT_BASE + 1.2 * (np.abs(Zr(p, 0)) - 0.8)


def w_n4(p, t, sh):
    return LOGIT_BASE + 1.5 * (p.raw[:, 1] > 0.45) - 0.3


def w_n4_38(p, t, sh): return LOGIT_BASE + 1.5 * (p.raw[:, 1] > 0.38) - 0.3
def w_n4_55(p, t, sh): return LOGIT_BASE + 1.5 * (p.raw[:, 1] > 0.55) - 0.3
class n4thr:
    def __init__(self, thr, j=1):
        self.thr, self.j = thr, j

    def __call__(self, p, t, sh):
        return LOGIT_BASE + 1.5 * (p.raw[:, self.j] > self.thr) - 0.3


def w_n4s(p, t, sh):
    return LOGIT_BASE + 1.5 / (1 + np.exp(-(p.raw[:, 1] - 0.45) / 0.05)) - 0.3


def w_n5(p, t, sh):
    return (w_n1(p, t, sh) + w_n2(p, t, sh) + w_n3(p, t, sh) + w_n4(p, t, sh)) / 4


def sig_n5(p):
    return 0.5 + 1.5 * p.raw[:, 0]


def w_n6(p, t, sh):
    return LOGIT_BASE + 0.8 * Zr(p, 1)


def mu_n6(p):
    return 0.4 + 0.5 * Zr(p, 0)


def w_n7(p, t, sh):
    s = sh[:, t][:, None]
    return LOGIT_BASE + 0.5 * s + (0.8 + 0.6 * s) * Zr(p, 0)[None, :]


def w_x1(p, t, sh):
    return LOGIT_BASE + 0.8 * Zr(p, 0) * Zr(p, 1)


def w_x2(p, t, sh):
    return LOGIT_BASE + 0.8 * Zr(p, 0) * Zr(p, 2)


def w_x3(p, t, sh):
    return LOGIT_BASE + (Zr(p, 0) + Zr(p, 1) + Zr(p, 2))


def w_x4(p, t, sh):
    return LOGIT_BASE + 3.0 * np.maximum(p.raw[:, 0] - 0.6, 0.0) / 0.3


def w_x5(p, t, sh):
    s = sh[:, t][:, None]
    return LOGIT_BASE + 0.8 * s * Zr(p, 0)[None] - 0.8 * s * Zr(p, 2)[None] + 0.6 * Zr(p, 1)[None]


def w_x7(p, t, sh):
    """Vol-only, aligned with the composite: kNN(10) vol-smoother of x minus
    the F1 fit of x (what F1 cannot model locally)."""
    u = np.column_stack([U(p, 0), U(p, 1)])
    d2 = ((u[:, None, :] - u[None, :, :]) ** 2).sum(-1)
    nn = np.argsort(d2, axis=1)[:, :10]
    fk = p.x[nn].mean(1)
    Xc = np.delete(p.X, 1, axis=1)
    fa = Xc @ np.linalg.lstsq(Xc, p.x, rcond=None)[0]
    d = fk - fa
    return LOGIT_BASE + d / (d.std() + 1e-12)


def alt(a):
    def f(p, t, sh):
        return LOGIT_BASE + 0.8 * z(U(p, 0)) + 0.5 * z(U(p, 1)) + a * z(p.x)

    return f


def mag_alt(b):
    def f(p):
        return 0.4 + b * z(p.x)

    return f


def class_alpha(v):
    return np.array([v if s in F.NON_EQUITY else 0.0 for s in PAN.names])


def name_mean_vol_alpha(c):
    acc = np.zeros(len(PAN.names))
    cnt = np.zeros(len(PAN.names))
    for p in PAN.periods:
        acc[p.gid] += np.log(p.raw[:, 1])
        cnt[p.gid] += 1
    m = acc / cnt
    return c * (m - m.mean()) / m.std()


def name_mean_rv20():
    acc = np.zeros(len(PAN.names))
    cnt = np.zeros(len(PAN.names))
    for p in PAN.periods:
        acc[p.gid] += p.raw[:, 0]
        cnt[p.gid] += 1
    return acc / cnt


def worlds():
    W = {}
    L = lambda f, **k: ("logit", f, k)  # noqa: E731
    W["N0 pure noise"] = L(w_n0)
    W["N1 linear rv20"] = L(w_n1)
    W["N2 convex rv_ratio"] = L(w_n2)
    W["N3 V-shape rv20"] = L(w_n3)
    W["N4 threshold rv_long sharp"] = L(w_n4)
    W["N4s threshold rv_long smooth"] = L(w_n4s)
    for thr in (0.25, 0.30, 0.35, 0.40, 0.42, 0.45, 0.48, 0.50, 0.60):
        W[f"N4t thr={thr:.2f}"] = L(n4thr(thr))
        W[f"N4u rv20 thr={thr:.2f}"] = L(n4thr(thr, 0))
    W["N4b threshold rv_long sharp at 0.38"] = L(w_n4_38)
    W["N4c threshold rv_long sharp at 0.55"] = L(w_n4_55)
    W["N5 mix + het"] = L(w_n5, sig_fn=sig_n5)
    W["N6 pay(rv_long)+size(rv20)"] = L(w_n6, mu_fn=mu_n6)
    W["N7 persistent level+slope shock"] = L(w_n7)
    W["X1 rv20*rv_long"] = L(w_x1)
    W["X2 rv20*rv_ratio"] = L(w_x2)
    W["X3 logit additive"] = L(w_x3)
    W["X4 raw-rv20 hockey"] = L(w_x4)
    W["X5 persistent slopes"] = L(w_x5)
    W["X6 N1 + xs corr 0.3"] = L(w_n1, xs_corr=0.3)
    W["X7 kNN beyond F1"] = L(w_x7)
    for sd in (0.1, 0.2, 0.3):
        W[f"N8 alpha sd {sd}"] = L(w_n1, alpha_sd=sd)
    for v in (-0.3, -0.6):
        W[f"N8b alpha non-equity {v:+}"] = L(w_n1, alpha_vec=("class", v))
    for d in (-0.15, -0.075, 0.0, 0.15):
        W[f"N9 one-factor drift {d:+.2f}"] = ("ret", None, dict(drift=d))
    for k in (0.5, 1.0):
        for d in (-0.15, 0.0, 0.15):
            W[f"ALT-tail downside k={k} drift {d:+.2f}"] = ("ret", None, dict(beta_mode="one", drift=d, tail_k=k))
    # new adversarial nulls
    W["Z1 persistent sector drifts"] = ("ret", None, dict(beta_mode="one", sector=True))
    W["Z2 priced at rv20, vol reverts to name mean"] = (
        "ret",
        None,
        dict(beta_mode="one", price_vol="trailing", realvol="namemean"),
    )
    W["Z3 AR(1) name vol mispricing sd .2"] = (
        "ret",
        None,
        dict(beta_mode="one", price_vol="trailing", ar_mis=0.2),
    )
    W["Z2m mirror: vol moves away from name mean"] = (
        "ret", None, dict(beta_mode="one", price_vol="trailing", realvol="namemean_mirror"))
    W["Z4 alpha = +0.4 z(name mean log rv_long)"] = L(w_n1, alpha_vec=("meanvol", 0.4))
    W["Z4m alpha = -0.4 z(name mean log rv_long)"] = L(w_n1, alpha_vec=("meanvol", -0.4))
    return W


def alts():
    W = {}
    for asd in (0.0, 0.2):
        tag = f" | alpha {asd}"
        for a in (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40):
            W[f"ALT pay a={a:.2f}{tag}"] = ("logit", alt(a), dict(sig_fn=sig_n5, alpha_sd=asd))
        for b in (0.1, 0.2, 0.4):
            W[f"ALT mag b={b:.1f}{tag}"] = (
                "logit",
                alt(0.0),
                dict(sig_fn=sig_n5, alpha_sd=asd, mu_fn=mag_alt(b)),
            )
    return W


def gen(spec, R, rng):
    kind, f, kw = spec
    kw = dict(kw)
    if kind == "logit":
        av = kw.pop("alpha_vec", None)
        if av is not None:
            av = class_alpha(av[1]) if av[0] == "class" else name_mean_vol_alpha(av[1])
        return logit_world(f, R, rng, alpha_vec=av, **kw)
    if kw.get("realvol") == "namemean":
        nm = name_mean_rv20()
        kw["realvol"] = lambda p: np.sqrt(p.raw[:, 0] * nm[p.gid])
    elif kw.get("realvol") == "namemean_mirror":
        nm = name_mean_rv20()
        kw["realvol"] = lambda p: p.raw[:, 0] * np.sqrt(p.raw[:, 0] / nm[p.gid])
    return return_world(R, rng, **kw)


NOBOOT = __import__("os").environ.get("NOBOOT") == "1"


def run_world(args):
    name, _, R, seed = args
    spec = {**worlds(), **alts()}[name]
    rng = np.random.default_rng(seed)
    t0 = time.time()
    chunk = R // N_BOOTSETS
    acc = {}
    extra = {"gbar": [], "a": [], "D1": [], "D2": [], "D3": [], "rawA": [], "rawC": [], "rawB": []}
    for c in range(N_BOOTSETS):
        roi, r_spy, paid = gen(spec, chunk, rng)
        wild = np.where(np.random.default_rng(900 + c).random((399, len(PAN.names))) < 0.5, -1.0, 1.0)
        o = F.run_test(PAN, roi, r_spy, paid, None if NOBOOT else BOOTS[c], wild)
        d = F.decide(PAN, o)
        for k, v in d.items():
            acc.setdefault(k, []).append(v)
        extra["gbar"].append(o.gbar)
        extra["a"].append(o.a)
        extra["D1"].append(o.D1)
        extra["D2"].append(o.D2)
        extra["D3"].append(o.D3)
        extra["eps"] = extra.get("eps", []) + [paid.mean()]
    res = {k: np.concatenate(v) for k, v in acc.items()}
    ex = {
        k: (np.concatenate(v) if k != "eps" else float(np.mean(v))) for k, v in extra.items() if v
    }
    return name, res, ex, time.time() - t0


def ci(k, n):
    lo = stats.beta.ppf(0.025, k, n - k + 1) if k > 0 else 0.0
    hi = stats.beta.ppf(0.975, k + 1, n - k) if k < n else 1.0
    return lo, hi


def fmt(v):
    k, n = int(v.sum()), len(v)
    lo, hi = ci(k, n)
    return f"{k / n:.3f} [{lo:.3f},{hi:.3f}]"


def main():
    global PAN, BOOTS
    R = int(sys.argv[1])
    filt = sys.argv[2:]
    PAN = F.load_panel()
    t0 = time.time()
    BOOTS = [None] * N_BOOTSETS if NOBOOT else [F.make_bootset(PAN, B_DRAWS, 777 + i) for i in range(N_BOOTSETS)]
    okf = np.nan if NOBOOT else np.mean([b.ok.mean() for b in BOOTS])
    print(
        f"SPEC_DUMMY={F.SPEC_DUMMY} NOBOOT={NOBOOT} T={PAN.T} L={PAN.L} cvA={F.cv_fixed_b(PAN.T, PAN.L):.4f} N={len(PAN.names)} "
        f"bootsets {N_BOOTSETS}x{B_DRAWS} built {time.time() - t0:.0f}s, usable period-draws {okf:.4f}",
        flush=True,
    )
    allw = {**worlds(), **alts()}
    todo = [
        (n, None, R, 5000 + i)
        for i, (n, s) in enumerate(allw.items())
        if not filt or any(f in n for f in filt)
    ]
    cols = ["A", "B", "C", "C2", "W", "a_tA", "A+dem", "B+dem", "C+dem", "C2+dem", "W+dem", "C2a", "C2a+dem", "Wa+dem"]
    print("world|" + "|".join(cols) + "|mean gbar|sd gbar|mean a|D1|D2|D3|P(D any | C pass)|SPY-paid rate")
    with Pool(4) as pool:
        for name, res, ex, dt in pool.imap(run_world, todo):
            dem = ex["D1"] | ex["D2"] | ex["D3"]
            cp = res["C"]
            dem_given = dem[cp].mean() if cp.any() else float("nan")
            print(
                f"{name}|"
                + "|".join(fmt(res[c]) for c in cols)
                + f"|{ex['gbar'].mean():+.4f}|{ex['gbar'].std():.4f}|{ex['a'].mean():+.4f}|{ex['D1'].mean():.3f}"
                f"|{ex['D2'].mean():.3f}|{ex['D3'].mean():.3f}|{dem_given:.3f}|{ex['eps']:.3f}"
                f"  ({dt:.0f}s)",
                flush=True,
            )


if __name__ == "__main__":
    main()
