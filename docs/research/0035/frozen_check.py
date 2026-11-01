"""Frozen-spec check: drive the REPO's beyond_vol_test through planted outcomes.

Signals: periods_signals_only.pkl (real signals, outcomes zeroed). The real-
outcome pickle is never touched. Outcome worlds are the round-2 / adversary-3
generators (fm_r2_sim.py, adv3_sim.py), unchanged; each returns per-period ROI
= payoff / cost - 1 against the panel's real cost, so we hand the module
payoff = (ROI + 1) * cost -- exactly the ROI the module recomputes and ranks --,
spy_payoff = the SPY row's payoff (drives the stress episodes) and
spy_return = the world's simulated market return.

  SPEC_DUMMY=1 SPEC_ABS=1 NOBOOT=1 python frozen_check.py REPS [filter ...]
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import pickle
import sys
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

assert os.environ.get("SPEC_DUMMY") == "1" and os.environ.get("SPEC_ABS") == "1"
assert os.environ.get("NOBOOT") == "1"

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path.home() / "Documents/apps/tail-lab/src"))

import adv3_sim as A  # noqa: E402  (loads the signals-only panel; asserts env)
import fm_r2_sim as S  # noqa: E402

from tail_lab.research.backtest import beyond_vol as BV  # noqa: E402

SIGNALS_ONLY = HERE / "periods_signals_only.pkl"
assert SIGNALS_ONLY.name == "periods_signals_only.pkl"
with open(SIGNALS_ONLY, "rb") as f:
    BASE = pickle.load(f)
PAN = S.PAN
assert len(BASE) == PAN.T and all(p.symbols == q.syms for p, q in zip(BASE, PAN.periods))
SPY_ROW = [p.symbols.index("spy") for p in BASE]
CHUNK = 125
TARGET = "predicts_beyond_trailing_vol"
# MODE=frozen: the module exactly as written. MODE=d3fix: BV.N_MIN patched to 30
# at runtime. On this panel every period has >= 43 names, so that changes ONLY
# _equity_gamma's subset check: one period has 39 equity names (< 40), which as
# frozen makes the equity mean unmeasurable in every replication, so D3 fires on
# every significant result and the status can never be "predicts".
MODE = os.environ.get("MODE", "frozen")
assert MODE in ("frozen", "d3fix")
if MODE == "d3fix":
    BV.N_MIN = 30

L = S.logit_world


def W():
    w = {
        "N0 pure noise": lambda R, g: L(S.w_n0, R, g),
        "N1 linear rv20": lambda R, g: L(S.w_n1, R, g),
        "N3 V-shape rv20": lambda R, g: L(S.w_n3, R, g),
        "N6 pay(rv_long)+size(rv20)": lambda R, g: L(S.w_n6, R, g, mu_fn=S.mu_n6),
        "N8 alpha sd 0.3": lambda R, g: L(S.w_n1, R, g, alpha_sd=0.3),
        "N8b alpha non-equity -0.6": lambda R, g: L(S.w_n1, R, g, alpha_vec=S.class_alpha(-0.6)),
        "N9 one-factor drift +0.00": lambda R, g: S.return_world(R, g, drift=0.0),
        "N9 one-factor drift +0.15": lambda R, g: S.return_world(R, g, drift=0.15),
        "N9 one-factor drift -0.15": lambda R, g: S.return_world(R, g, drift=-0.15),
        "A both a.3 k.15 phi.85": A.both_world(0.15, 0.3, 0.85),
        "A both a.3 k.25 phi.85": A.both_world(0.25, 0.3, 0.85),
        "A time-only k.25 phi.85": A.both_world(0.25, 0.0, 0.85),
        "B skew drift 0.00": lambda R, g: A.ret_world(R, g, m_fn=A.m_skew(0.0)),
        "B skew drift +0.10": lambda R, g: A.ret_world(R, g, m_fn=A.m_skew(0.10)),
        "B 2022 block": lambda R, g: A.ret_world(R, g, m_fn=A.m_2022),
        "E alpha +-1.0 on 5 heaviest names": A.heavy_alpha(0, 1.0, 0.15, A.TOPW),
        "Z2m vol moves away from name mean": lambda R, g: S.gen(
            ("ret", None, dict(beta_mode="one", price_vol="trailing", realvol="namemean_mirror")),
            R,
            g,
        ),
    }
    for thr in (0.25, 0.30, 0.35, 0.40, 0.42, 0.45, 0.48, 0.50, 0.60):
        w[f"N4t rv_long thr={thr:.2f}"] = (lambda t: lambda R, g: L(S.n4thr(t), R, g))(thr)
        w[f"N4u rv20 thr={thr:.2f}"] = (lambda t: lambda R, g: L(S.n4thr(t, 0), R, g))(thr)
    for a in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40):
        w[f"ALT pay a={a:.2f} | alpha 0.0"] = (
            lambda a_: lambda R, g: L(S.alt(a_), R, g, sig_fn=S.sig_n5)
        )(a)
    for a in (0.20, 0.30):
        w[f"ALT pay a={a:.2f} | alpha 0.2"] = (
            lambda a_: lambda R, g: L(S.alt(a_), R, g, sig_fn=S.sig_n5, alpha_sd=0.2)
        )(a)
    return w


WORLDS = W()


def periods_for(roi, r_spy, r):
    out = []
    for t, base in enumerate(BASE):
        pay = (roi[t][r] + 1.0) * base.cost
        out.append(
            dataclasses.replace(
                base,
                payoff=pay,
                spy_payoff=float(pay[SPY_ROW[t]]),
                spy_return=float(r_spy[r, t]),
            )
        )
    return out


def task(args):
    name, n, seed = args
    rng = np.random.default_rng(seed)
    roi, r_spy, _paid = WORLDS[name](n, rng)
    st, raw, gb = [], [], []
    for r in range(n):
        res = BV.beyond_vol_test(periods_for(roi, r_spy, r))
        st.append(
            res.status
            if res.demotion is None or res.status != "not_robust"
            else f"not_robust:{res.demotion}"
        )
        raw.append(res.p_value is not None and res.p_value < BV.ALPHA)
        gb.append(res.gamma_bar if res.gamma_bar is not None else np.nan)
    return name, st, raw, gb


def cp(k, n):
    lo = stats.beta.ppf(0.025, k, n - k + 1) if k > 0 else 0.0
    hi = stats.beta.ppf(0.975, k + 1, n - k) if k < n else 1.0
    return lo, hi


def fmt(k, n):
    lo, hi = cp(k, n)
    return f"{k / n:.3f} [{lo:.3f},{hi:.3f}]"


def main():
    reps = int(sys.argv[1])
    filt = sys.argv[2:]
    names = [n for n in WORLDS if not filt or any(f in n for f in filt)]
    todo = [
        (n, CHUNK, 20261102 + 1000 * i + c)
        for i, n in enumerate(WORLDS)
        if n in names
        for c in range(reps // CHUNK)
    ]
    print("module sha256", hashlib.sha256(Path(BV.__file__).read_bytes()).hexdigest())
    print(
        f"MODE={MODE} repo beyond_vol_test, L={BV.BANDWIDTH}, N_MIN={BV.N_MIN}, T={len(BASE)}, reps/world={reps // CHUNK * CHUNK}"
    )
    print("world | P(status=predicts) [CP95] | P(p<0.05) [CP95] | mean gbar | sd gbar | statuses")
    acc = {n: ([], [], []) for n in names}
    left = Counter(n for n, _, _ in todo)
    t0 = time.time()
    with Pool(4) as pool:
        for name, st, raw, gb in pool.imap_unordered(task, todo):
            acc[name][0].extend(st)
            acc[name][1].extend(raw)
            acc[name][2].extend(gb)
            left[name] -= 1
            if left[name] == 0:
                s, rw, g = acc[name]
                n = len(s)
                k = sum(x == TARGET for x in s)
                g = np.array(g, float)
                cnt = Counter(s)
                print(
                    f"{name} | {fmt(k, n)} | {fmt(sum(rw), n)} | {np.nanmean(g):+.4f} | {np.nanstd(g):.4f} | "
                    + ", ".join(f"{a}={b}" for a, b in sorted(cnt.items()))
                    + f"  ({time.time() - t0:.0f}s)",
                    flush=True,
                )


if __name__ == "__main__":
    main()
