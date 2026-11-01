"""Summarise frozen_check_{frozen,d3fix}.txt into frozen_check.txt: rejection
rates unconditional and conditional on sufficient data (>= 3 stress episodes,
which the real panel has by construction of the signal test), CP 95% CIs, and
the MDE interpolated from the power curve."""

import re
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
# beyond-vol pay-rate gap (pp) per a, from fm_r2_gap.py under SPEC_DUMMY=1 SPEC_ABS=1
GAP = {
    0.05: 2.20,
    0.10: 4.37,
    0.15: 6.52,
    0.20: 8.64,
    0.25: 10.72,
    0.30: 12.77,
    0.40: 16.76,
}


def cp(k, n):
    lo = stats.beta.ppf(0.025, k, n - k + 1) if k > 0 else 0.0
    hi = stats.beta.ppf(0.975, k + 1, n - k) if k < n else 1.0
    return lo, hi


def fmt(k, n):
    if n == 0:
        return "n/a"
    lo, hi = cp(k, n)
    return f"{k / n:.3f} [{lo:.3f},{hi:.3f}]"


def parse(path):
    rows = {}
    for line in path.read_text().splitlines():
        parts = line.split(" | ")
        if len(parts) == 7:  # "ALT pay a=... | alpha ..." carries a separator in its name
            parts = [parts[0] + " | " + parts[1], *parts[2:]]
        if len(parts) != 6 or parts[0] == "world":
            continue
        st = dict(re.findall(r"([\w:]+)=(\d+)", parts[5].split("  (")[0]))
        st = {k: int(v) for k, v in st.items()}
        rows[parts[0]] = (st, parts[3], parts[4])
    return rows


def table(rows, out):
    out.append(
        "world | n | P(predicts) uncond | n sufficient | P(predicts | sufficient) | P(p<.05 | sufficient) | mean gbar | sd gbar | statuses"
    )
    res = {}
    for name, (st, mg, sg) in rows.items():
        n = sum(st.values())
        ins = st.get("insufficient_data", 0)
        k = st.get("predicts_beyond_trailing_vol", 0)
        ns = n - ins
        kp = ns - st.get("no_evidence_beyond_trailing_vol", 0)
        res[name] = (k, ns)
        out.append(
            f"{name} | {n} | {fmt(k, n)} | {ns} | {fmt(k, ns)} | {fmt(kp, ns)} | {mg} | {sg} | "
            + ", ".join(f"{a}={b}" for a, b in sorted(st.items()))
        )
    return res


def main():
    out = []
    for mode in ("frozen", "d3fix"):
        p = HERE / f"frozen_check_{mode}.txt"
        head = [
            l
            for l in p.read_text().splitlines()[:4]
            if l.startswith(("module", "MODE"))
        ]
        out += ["", f"=== {mode} ===", *head]
        res = table(parse(p), out)
        if mode == "d3fix":
            a_s = sorted(a for a in GAP if f"ALT pay a={a:.2f} | alpha 0.0" in res)
            pw = [res[f"ALT pay a={a:.2f} | alpha 0.0"] for a in a_s]
            pr = np.array([k / n for k, n in pw])
            gaps = np.array([GAP[a] for a in a_s])
            out += [
                "",
                "power curve (alpha 0.0, conditional on sufficient data): a, beyond-vol gap pp, power",
            ]
            out += [
                f"  a={a:.2f}  gap={g:5.2f}pp  power={fmt(k, n)}"
                for a, g, (k, n) in zip(a_s, gaps, pw)
            ]
            i = int(np.argmax(pr >= 0.80))
            if pr[i] >= 0.80 and i > 0:
                mde = gaps[i - 1] + (0.80 - pr[i - 1]) * (gaps[i] - gaps[i - 1]) / (
                    pr[i] - pr[i - 1]
                )
                out.append(
                    f"MDE (linear interpolation of power in gap, 80%): {mde:.2f} pp"
                )
            else:
                out.append("MDE: 80% not bracketed by the grid")
    (HERE / "frozen_check.txt").write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
