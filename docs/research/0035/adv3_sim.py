"""Adversary r3: reproduce + new nulls under r3 spec (SPEC_DUMMY=1 SPEC_ABS=1 NOBOOT=1). Uses signals-only panel."""
import os, sys, time
assert os.environ.get("SPEC_DUMMY")=="1" and os.environ.get("SPEC_ABS")=="1" and os.environ.get("NOBOOT")=="1"
import numpy as np
from multiprocessing import Pool
import fm_r2 as F, fm_r2_sim as S

S.PAN = F.load_panel(); PAN = S.PAN; S.BOOTS = [None]*S.N_BOOTSETS
N = len(PAN.names); T = PAN.T
if os.environ.get("LOVR"): PAN.L = int(os.environ["LOVR"])
print("L =", PAN.L, "cv =", round(F.cv_fixed_b(T, PAN.L), 4), flush=True)
ETF = set("spy qqq dia iwm efa eem ewj ewz fxi smh xlk arkk xle uso ung xlf kre xly xrt xhb iyt xlu xlp xlv xbi iyr xli xlb gdx gld slv dbc dba tlt lqd hyg".split())
SINGLE = np.array([n not in ETF for n in PAN.names])
acc = np.zeros(N); cnt = np.zeros(N)
for p in PAN.periods: acc[p.gid] += p.xt; cnt[p.gid] += 1
Q = acc/cnt; Q = (Q-Q.mean())/Q.std()   # name trait = mean residual composite

# ---- logit worlds
def both_world(k, asd, phi):
    def g(R, rng):
        s = np.zeros((R, T)); s[:, 0] = rng.standard_normal(R)/np.sqrt(1-phi**2)
        for t in range(1, T): s[:, t] = phi*s[:, t-1] + rng.standard_normal(R)
        s *= np.sqrt(1-phi**2)  # unit marginal variance
        f = lambda p, t, sh: S.LOGIT_BASE + S.Zr(p, 0)[None, :] + k*s[:, t][:, None]*Q[p.gid][None, :]
        return S.logit_world(f, R, rng, alpha_sd=asd)
    return g

# ---- return worlds (copy of S.return_world with hooks)
def ret_world(R, rng, m_fn=None, beta_mode="resid", name_drift_sd=0.0, earn_sd=0.0, price_vol="true"):
    pan = PAN
    beta = S.name_resid_beta(rng, R) if beta_mode == "resid" else np.ones((R, N))
    mu_i = name_drift_sd*rng.standard_normal((R, N)); mu_i[:, pan.names.index("spy")] = 0
    off = rng.integers(0, 91, size=(R, N))
    roi, paid, r_spy = [], np.empty((R, T), bool), np.empty((R, T))
    for t, p in enumerate(pan.periods):
        rv20 = p.raw[:, 0]
        sig_true = np.broadcast_to(rv20, (R, len(p.gid)))
        sm = 0.9*rv20[p.spy]
        b = np.maximum(np.minimum(beta[:, p.gid], 0.95*sig_true/sm), 0.0)
        m = (m_fn(t, R, rng, sm) if m_fn else sm*np.sqrt(S.TAU)*rng.standard_normal((R, 1)))
        var_rest = sig_true**2 - (b*sm)**2
        lr = b*m + mu_i[:, p.gid]*S.TAU - 0.5*sig_true**2*S.TAU + np.sqrt(var_rest*S.TAU)*rng.standard_normal((R, len(p.gid)))
        if earn_sd:
            o = off[:, p.gid]; has = ((t*28+o)//91) != (((t+1)*28+o)//91)
            js = earn_sd*np.where(rv20 > 0.5, 2.0, 1.0)[None, :]
            lr = lr + np.where(has & SINGLE[p.gid][None, :], js*rng.standard_normal(lr.shape) - 0.5*js**2, 0.0)
        K, P = S.bs_put(rv20)
        payoff = np.maximum(K - np.exp(lr), 0.0)/P
        roi.append(payoff/p.cost - 1.0); paid[:, t] = payoff[:, p.spy] > 0
        r_spy[:, t] = np.exp(lr[:, p.spy]) - 1.0
    return roi, r_spy, paid

def ret_world_tail(R, rng, k, m_fn):
    # ALT-tail with skewed market: reuse S.return_world but swap its market draw via drift=0 and a patched rng? simpler: inline
    old = S.return_world
    return _tail(R, rng, k, m_fn)

def _tail(R, rng, k, m_fn):
    pan = PAN; tail = k*(S.name_resid_beta(rng, R) - 1.0)/0.35
    roi, paid, r_spy = [], np.empty((R, T), bool), np.empty((R, T))
    for t, p in enumerate(pan.periods):
        rv20 = p.raw[:, 0]; sm = 0.9*rv20[p.spy]
        sig = np.broadcast_to(rv20, (R, len(p.gid))); b = np.minimum(1.0, 0.95*sig/sm)
        m = m_fn(t, R, rng, sm)
        lr = b*m - 0.5*sig**2*S.TAU + np.sqrt((sig**2-(b*sm)**2)*S.TAU)*rng.standard_normal(sig.shape) + tail[:, p.gid]*np.minimum(m, 0.0)
        K, P = S.bs_put(rv20); payoff = np.maximum(K-np.exp(lr), 0.0)/P
        roi.append(payoff/p.cost-1.0); paid[:, t] = payoff[:, p.spy] > 0; r_spy[:, t] = np.exp(lr[:, p.spy])-1.0
    return roi, r_spy, paid

def m_skew(mu_ann):   # neg-skewed 4-week market: 88% calm, 12% selloff; mean = mu_ann*TAU
    def f(t, R, rng, sm):
        crash = rng.random((R, 1)) < 0.12
        calm = 0.035*rng.standard_normal((R, 1)); dn = -0.08 + 0.04*rng.standard_normal((R, 1))
        x = np.where(crash, dn, calm + 0.012)
        return x - (0.88*0.012 - 0.12*0.08) + mu_ann*S.TAU
    return f
def m_2022(t, R, rng, sm):   # 2020-04-30 start: periods ~21..32 are 2022 (bear -30%/yr); else +20%/yr
    d = -0.30 if 21 <= t <= 32 else 0.20
    return d*S.TAU + sm*np.sqrt(S.TAU)*rng.standard_normal((R, 1))

def heavy_alpha(p_out, big, base_sd, idx=None):
    def g(R, rng):
        a = base_sd*rng.standard_normal((R, N))
        if idx is None:
            out = rng.random((R, N)) < p_out
        else:
            out = np.zeros((R, N), bool); out[:, idx] = True
        a = np.where(out, big*np.where(rng.random((R, N)) < 0.5, -1.0, 1.0), a)
        return S.logit_world(S.w_n1, R, rng, alpha_vec=None) if False else _lw_alpha(R, rng, a)
    return g
def _lw_alpha(R, rng, a):
    # logit_world with per-replication alpha matrix: pass alpha_sd=0 and add a via lg_fn
    f = lambda p, t, sh: S.w_n1(p, t, sh)[None, :] + a[:, p.gid]
    return S.logit_world(f, R, rng)
TOPW = [PAN.names.index(s) for s in ("gdx", "ung", "amzn", "fxi", "ccj")]

WORLDS = {
 # reproductions (fresh seeds)
 "rep N0": lambda R, rng: S.logit_world(S.w_n0, R, rng),
 "rep N8 sd0.3": lambda R, rng: S.logit_world(S.w_n1, R, rng, alpha_sd=0.3),
 "rep N8b -0.6": lambda R, rng: S.logit_world(S.w_n1, R, rng, alpha_vec=S.class_alpha(-0.6)),
 "rep N9 -0.15": lambda R, rng: S.return_world(R, rng, drift=-0.15),
 "rep N4t 0.42": lambda R, rng: S.logit_world(S.n4thr(0.42), R, rng),
 # new
 "NEW-A both a.3 k.15 phi.85": both_world(0.15, 0.3, 0.85),
 "NEW-A both a.3 k.25 phi.85": both_world(0.25, 0.3, 0.85),
 "NEW-A time-only k.25 phi.85": both_world(0.25, 0.0, 0.85),
 "NEW-A both a.4 k.25 phi.5": both_world(0.25, 0.4, 0.5),
 "NEW-A time-only k.25 phi.5": both_world(0.25, 0.0, 0.5),
 "NEW-A name-only a.4": both_world(0.0, 0.4, 0.5),
 "NEW-B skew bull +0.10": lambda R, rng: ret_world(R, rng, m_fn=m_skew(0.10)),
 "NEW-B skew flat 0.00": lambda R, rng: ret_world(R, rng, m_fn=m_skew(0.0)),
 "NEW-B 2022 block": lambda R, rng: ret_world(R, rng, m_fn=m_2022),
 "NEW-B skew bull +0.05": lambda R, rng: ret_world(R, rng, m_fn=m_skew(0.05)),
 "NEW-B skew bull +0.15": lambda R, rng: ret_world(R, rng, m_fn=m_skew(0.15)),
 "ALT pay a=0.15": lambda R, rng: S.logit_world(S.alt(0.15), R, rng, sig_fn=S.sig_n5),
 "ALT pay a=0.20": lambda R, rng: S.logit_world(S.alt(0.20), R, rng, sig_fn=S.sig_n5),
 "ALT-tail k=0.5 drift +0.15": lambda R, rng: S.return_world(R, rng, beta_mode="one", drift=0.15, tail_k=0.5),
 "ALT-tail k=0.5 skew +0.10": lambda R, rng: ret_world_tail(R, rng, 0.5, m_skew(0.10)),
 "NEW-E heavy alpha 10% at +-1.0, base .15": heavy_alpha(0.10, 1.0, 0.15),
 "NEW-E heavy alpha 5% at +-1.5, base .15": heavy_alpha(0.05, 1.5, 0.15),
 "NEW-E alpha +-1.0 on 5 top-weight names (random sign)": heavy_alpha(0, 1.0, 0.15, TOPW),
 "NEW-C earnings jumps 6%/12%": lambda R, rng: ret_world(R, rng, beta_mode="one", earn_sd=0.06),
 "NEW-D name drift sd 0.30/yr": lambda R, rng: ret_world(R, rng, beta_mode="one", name_drift_sd=0.30),
 "NEW-D name drift sd 0.50/yr": lambda R, rng: ret_world(R, rng, beta_mode="one", name_drift_sd=0.50),
}

def run(args):
    name, R, seed = args
    rng = np.random.default_rng(seed); out = {k: [] for k in ("C2", "C2a", "C2a+dem", "gbar", "sev", "svn", "svt")}
    for c in range(4):
        roi, r_spy, paid = WORLDS[name](R//4, rng)
        o = F.run_test(PAN, roi, r_spy, paid, None, None); d = F.decide(PAN, o)
        for k in ("C2", "C2a", "C2a+dem"): out[k].append(d[k])
        cv = F.cv_fixed_b(T, PAN.L)
        out.setdefault("Csum_fb", []).append(o.tC > cv)
        ta2 = np.empty(len(o.gbar))
        for r in range(len(o.gbar)):
            Z = np.column_stack([np.ones(T), r_spy[r], np.minimum(r_spy[r], 0.0)]); Zi = np.linalg.inv(Z.T@Z)
            cf = Zi@Z.T@o.g[r]; sc = Z*(o.g[r]-Z@cf)[:, None]; Sm = sc.T@sc
            for lag in range(1, PAN.L+1):
                G = sc[lag:].T@sc[:-lag]; Sm += (1-lag/(PAN.L+1))*(G+G.T)
            ta2[r] = cf[0]/np.sqrt((Zi@Sm@Zi)[0, 0])
        out.setdefault("C2a_kink", []).append(o.tC2 > cv)
        out["C2a_kink"][-1] = out["C2a_kink"][-1] & (ta2 > cv)
        out["gbar"].append(o.gbar); out["sev"].append(o.gbar/o.tC2)
        out["svt"].append(np.sqrt(F.nw_var_mean(o.g, PAN.L)))
    return name, {k: np.concatenate(v) for k, v in out.items() if v}

if __name__ == "__main__":
    R = int(sys.argv[1]); filt = sys.argv[2:]
    todo = [(n, R, 424242+17*i) for i, n in enumerate(WORLDS) if not filt or any(f in n for f in filt)]
    print("world | C2 | C2a | C2a+dem | Csum_fb | C2a_kink | mean gbar | sd gbar | rms se(max V) | rms se(NW) | sd/rms_se")
    with Pool(4) as pool:
        for name, r in pool.imap(run, todo):
            rs = np.sqrt((r["sev"]**2).mean()); rt = np.sqrt((r["svt"]**2).mean())
            print(f"{name} | " + " | ".join(S.fmt(r[k]) for k in ("C2", "C2a", "C2a+dem", "Csum_fb", "C2a_kink")) +
                  f" | {r['gbar'].mean():+.4f} | {r['gbar'].std():.4f} | {rs:.4f} | {rt:.4f} | {r['gbar'].std()/rs:.2f}", flush=True)
