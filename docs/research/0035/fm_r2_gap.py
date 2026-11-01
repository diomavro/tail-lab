"""Pay-rate gap (top vs bottom composite quintile) implied by ALT pay a, in pp,
net of the same gap at a = 0 (the vol-driven part)."""
import numpy as np, fm_r2 as F, fm_r2_sim as M
M.PAN = F.load_panel()
def gap(a):
    g = []
    for t, p in enumerate(M.PAN.periods):
        pr = 1 / (1 + np.exp(-M.alt(a)(p, t, None)))
        g.append(pr[p.x >= 0.3].mean() - pr[p.x <= -0.3].mean())
    return np.mean(g)
g0 = gap(0.0)
print(f"vol-only gap at a=0: {100*g0:.2f} pp")
for a in (0.05, 0.10, 0.15, 0.20, 0.30, 0.40):
    print(f"a={a:.2f}: total gap {100*gap(a):.2f} pp, beyond-vol gap {100*(gap(a)-g0):.2f} pp")
