# 36. Measure the implied-vol surface in log-forward moneyness

Date: 2026-11-05

## Status

**Proposed** (Dio asked for it on 2026-11-04: "use log moneyness and related concepts to help us with the volatility surface"). This needs a human merge because it touches `docs/adr/`.

## Context

Before this ADR, every module measured moneyness against **spot**, as `K / S` or as percent OOM. Nothing computed a forward, and nothing fitted a surface.

The one smile fit, `research/surface/smile.py`, returns dσ/dK in vol per *dollar* of strike. That number is not comparable:

- **Across tenors**, because the forward drifts from spot by `(r − q) T`.
- **Across names**, because it is measured per dollar of strike.

The call wing has been collected since ADR 0031 (2026-10-27), so a forward can now be read from the market itself.

## Decision

1. **Coordinates.** The surface is measured per expiry in two coordinates:
   - `k = ln(K / F)`, so at-the-money is `k = 0` at every tenor;
   - `w = σ² T`, the total implied variance.

   No standardised moneyness `k / (σ_ATM √T)` is computed, because nothing consumes it yet.

2. **Forward: parity first, carry second.**
   - **Parity** is `F = K + e^{rT}(C − P)`, taken as the median of the 3 strikes nearest the carry forward.
     - It is refused, and carry is used instead, when those strikes disagree by more than `0.20% + 0.40% × T`.
     - It needs no dividend yield. That matters because production has no `tiingo_eod` snapshot (measured 2026-11-05).
   - **Carry** is `F = S e^{(r−q)T}`. It is labelled `carry_q0` when q is unknown.
   - **r** is the flat `DEFAULT_RATE`, the same rate every other route uses. The plan had proposed the point-in-time bill rate; near the money the parity forward barely depends on r.

3. **Measured biases are stated, not hidden.**
   - **Parity is European, the options are American.** An American binomial check (S=100, r=4.3%, q=1.3%, σ=18%) puts the parity forward 1–3 bp low at 30 days and 10–19 bp low at six months, varying by strike. `tests/test_research_vol_surface_coords.py` pins this: biased low, and within tolerance.
   - **Inverting American OTM puts with a European model reads IV slightly high**: +14 to +26 bp of vol at K/S 0.90–0.95, T = 0.5. This is accepted.

4. **Inputs.**
   - Out-of-the-money mids only: puts below F, calls above.
   - A quote must meet the marks bar: bid > 0 and relative spread ≤ `marks.MAX_RELATIVE_SPREAD`.
   - IV is our own Black inversion on the forward. Cboe's `iv` column is never used.

5. **Fit: raw SVI with Lee's bound inside the fit.**
   - The slice is fitted by the quasi-explicit method (Zeliade 2009).
   - The inner problem is solved exactly by enumerating its active faces: `|d| ≤ c` and `c + |d| ≤ 2σ`.
   - The bound is Lee's `b(1+|ρ|) ≤ 2`, not Zeliade's looser 4σ. With 4σ, the fit would emit slices our own Lee check refuses.
   - Minimum variance `a + bσ√(1−ρ²) ≥ 0` is enforced by projection.

6. **A fit is not a certificate.** Each slice reports three diagnostics:
   - **Butterfly:** Durrleman `g(k) ≥ 0` on the *observed* k-range. Non-finite or non-positive points count as `n_invalid` and fail.
   - **Lee:** the wing-slope bound, checked on the fitted parameters.
   - **Calendar:** whether w ever decreases in T on the intersection of adjacent slices' observed ranges.

   The Gatheral–Jacquier (2014) slice is the pinned counterexample: its minimum g is −0.0329.

7. **One wing gets a quadratic, not SVI.**
   - A session without the call wing leaves ρ, b and the right wing unidentified. Freezing ρ from a later fit would leak the future.
   - Such a slice gets a local quadratic in k instead. As the one exception to OTM-only, it keeps puts up to the stored band edge, so the fit straddles k = 0.
   - It refuses with `no_strikes_above_forward` unless at least 2 strikes sit above F.
   - The SVI fields and the risk reversal are reported as not evaluated.

8. **Outputs.** All are in k-units:
   - ATM vol, ATM skew dσ/dk, and ATM curvature;
   - the ATM term structure;
   - on two-wing slices only, the 25Δ risk reversal σ(25Δ call) − σ(25Δ put), using forward Black delta, solved inside the observed range.

   These are the regime-signal candidates.

9. **Scope of v1.**
   - **Data:** only the forward-collected Cboe chain is read. The optionsDX archive is not.
   - **Unchanged:** these stay on spot moneyness:
     - the strike rules, ranking, sweep grid and `hypothesis.RuleSpec` hashes, because moving them would split verdict memory again (ADR 0015);
     - `smile.py` and `alpha_bound`, because they feed a published bound.
   - **Not consumed yet:** no backtest reads the surface.
   - **Not replaced yet:** `MODEL_PRICED_MAX_MONEYNESS_PCT` stays as it is (ADR 0018). Replacing it with a measured Durrleman depth needs its own ADR.

## Consequences

- `GET /api/putlab/vol-surface?symbol=&as_of=` serves one session's slices. It will be read by a Smile section inside the Surface tab (ADR 0021's desk word), which ships separately.
- **Measured on SPY, session 2026-11-04 (22 expiries):**
  - every forward came from parity;
  - fit RMSE was 0.06–0.39 vol points, and butterfly and Lee held on every slice;
  - 3 short-dated adjacent pairs showed calendar violations;
  - ATM skew dσ/dk ran from −1.37 at 5 days to −0.42 at five months.
- A session takes about 5 s to fit, so the route memoises each answer for 10 minutes.
