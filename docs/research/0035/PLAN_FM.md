# Plan: does fragility predict put outcomes beyond trailing volatility? (Fama–MacBeth)

**Round 4 (to freeze), 2026-11-01.** The round 4 section is the spec. Rounds 1–3 below are history. The round-1 simulation report is `fm_sim.py`, with output in `run_t.txt` and `run_fb.txt`. The round-1 method review is `fmrev1-3.py`.



## Round 4: the frozen spec

The round 3 changes stand, with the edits below. Dio decided two things on 2026-11-01:
- the NW bandwidth is fixed at 16;
- crash risk a name inherits through its beta to a skewed index counts as fragility.

### 1. One procedure, hard-coded

- The frozen source is `src/tail_lab/research/backtest/beyond_vol.py`. It has no options, flags or menu.
- The sha256 of that file goes into ADR 0035, together with the sha256 of this plan, before `periods_REAL_OUTCOMES.pkl` is opened.

### 2. Inference

- **Bandwidth:** L = 16. It is fixed, not a formula of T, so at T = 81, b = (L+1)/T = 0.21.
- **Variance:** V = max(V_name with HC2 residuals, V_time = NW Bartlett at L = 16 × T/(T−1)).
- **Statistic:** t = γ̄ / √V.
- **p-value:** the share of a simulated fixed-b null distribution that is ≥ t.
  - That distribution is the NW-t at the same L and the same T, computed on iid N(0,1) series of length T.
  - It uses 100,000 draws from `np.random.default_rng(20261101)`, simulated at call time and memoised per (T, L).
- **Decision rule:** p < 0.05. The critical value is the 95% quantile of that same distribution, so the rule and the critical value agree by construction.
- **The a-test** uses the same machinery on the series γ_t regressed on r_SPY,t. Its NW variance is at L = 16, and its fixed-b p-value comes from a simulation of the regression intercept's t, with the same seed and draw count.

### 3. Statuses

`insufficient_data` applies when T < 24 or when there are fewer than 3 stress episodes. Both thresholds are ADR 0033's own.

Otherwise, in this order:
1. `no_evidence_beyond_trailing_vol` when p ≥ 0.05.
2. `not_robust` when p < 0.05 but the a-test p ≥ 0.05, or when D1 or D3 fires. The status records which check failed.
3. `predicts_beyond_trailing_vol` otherwise.

### 4. Disclosed limits, corrected

Each of these limits appears in the concept text and the ADR:
- **Persistent factors on residual-fragility names.** Size is about 0.06 at L = 16 with persistence φ = 0.85. It was 0.11 at L = 3.
- **Skewed index.**
  - At this sample's drift, a negatively skewed index gives a size of about 0.04–0.06. A bear sample would over-pass.
  - Inherited crash exposure counts as fragility. So a pass may mean that high-beta names inherit the index's crash risk; the wording says so.
- **Asset-class effect (N8b at −0.6):** size about 0.06.
- **Sharp vol cliff:** about 0.07.
- **Name vol drifting away from its own mean (Z2m):** about 0.11. That is not realistic, because realised vol mean-reverts.
- **A few heavy-weight names carrying α:** about 0.07. GDX and UNG together carry 13% of the weight.
- **Blinding was not perfect.** rv20 at the next entry is the realised vol of each holding period, so the signals-only panel was not outcome-blind for vol. No spec choice after round 3 used period t+1 signals.
- **Magnitude.** The rank outcome barely sees magnitude. The log1p companion is shown, but it is not tested.
- **Power.** The minimum detectable effect is about 10pp of pay-rate gap at 80% power, at L = 16. The exact figure comes from the round 4 simulation.

### 5. Wording

- **On a pass:** "...beyond trailing vol, possibly through their exposure to market crashes; under model pricing this means they moved more than their trailing vol implied, which real option prices may already reflect."
- **On no evidence:** print the 90% CI and the minimum detectable effect.

### 6. Data

- `PeriodInputs` gains `spy_return: float` (spot at expiry / spot at entry − 1). The collector fills it.
- The frozen non-equity list lives as a constant in `beyond_vol.py`: {tlt, lqd, hyg, gld, slv, uso, ung, dba, dbc}.
- The knots are constants in the same file.

## Round 3 changes (from the round 2 simulation: `fm_r2.py`, `fm_r2_sim.py`, and the `run_*.txt` outputs)

### Changes to the spec

All of these were simulated in round 2.

- **Inference: C2.**
  - The variance of γ̄ is the larger of two estimates. V_name is name-clustered with HC2 residuals. V_time is Newey–West at L = floor(4(T/100)^(2/9)).
  - The test statistic t = γ̄/√V is compared with the one-sided Kiefer–Vogelsang fixed-b critical value, which is 1.754 at T = 81.
  - The p-value comes from the fixed-b distribution.
  - Options A, B and C each failed a null: A failed N8 (0.076–0.091); B failed N0 (0.105); C failed N0 (0.069).
- **Controls.** The 15-column vol tensor and the 3-column rv_ratio spline stay.
  - The four within-period z-terms are replaced by absolute-level restricted cubic splines in log rv20 and log rv_long. The knots are frozen at the pooled quantiles: rv20 at .11, .19, .28, .40 and .78; rv_long at .15, .25, .34, .44 and .68.
  - A non-equity dummy is added for {tlt, lqd, hyg, gld, slv, uso, ung, dba, dbc}. This list is frozen; gdx, iyr and the crypto equities count as equity.
- **D2 becomes a test:** γ_t = a + b·r_SPY,t. A pass also requires the one-sided fixed-b HAC test of a to pass.
  - This needs SPY's return over each period. `PeriodInputs` gains `spy_return` (expiry spot / entry spot − 1), filled by the collector.
- **D1 and D3 stay.** They are free in power terms (≤ 0.005) but protect almost nothing.

### Measured size and power under the recommended spec

| Case | Result |
|---|---|
| N0–N7, X1–X7, N8, Z1, Z3, Z4 | size 0.018–0.054 |
| Worst sharp threshold | 0.065 |
| N9 at −15%/yr drift | 0.074 |
| Z2m (name vol moving away from its own mean) | 0.108 |
| Power, pay-rate gap of 8.6pp | 0.81 |
| Minimum detectable effect, at 80% power | about 8.5pp of pay-rate gap |

### Known limits, accepted and disclosed rather than patched

- **Bear samples.** The test over-passes at a −15%/yr drift (0.074). This sample's drift is positive, and at +15%/yr the size is 0.004, so the drift biases against a pass. The sentence prints the drift-adjusted a regardless.
- **Name-level vol traits that use information outside the trailing window.** These bias γ̄ by about ±0.7 sd. The realistic direction is mean reversion (Z2: size 0.009, which biases against a pass); the anti-reverting mirror (Z2m: 0.108) is not realistic. The concept text says that only trailing vol is controlled.
- **A sharp vol cliff.** One cliff threshold gives a size of 0.065.
- **Magnitude.** The rank outcome barely sees magnitude: power is 0.26 at the largest magnitude-only effect tested. The log1p companion is shown, so a magnitude story is visible but not tested.

## What round 1 established

These are measured facts, not opinions.

- **The signals carry two dimensions of vol, not three.** `rv_ratio = rv_long / rv20` exactly.
- **Vol controls explain most of the composite.** The plan's controls explained a median 54% of the composite rank in each period; a full 2-D vol surface explains 68%. Identification therefore rests on the remaining third.
- **The plan's spec fails its own simulation.** It falsely passes 8–31% of the time in most vol-only null worlds, and its t(T−1) critical value is wrong even on pure noise.
- **Name effects inflate the size.** Persistent name-level outcome effects (α_i, independent of fragility and vol) push the size to 0.10–0.21. Residual fragility is 63% between-name variance, so a per-period error underestimates the noise.
- **Market drift tilts γ_t.** The composite is mostly market co-movement once vol is controlled, so γ_t ≈ −c·(market move in period t). A bull sample tilts against a pass; a bear sample would give false passes.
- **The bottom of residual fragility is non-equity ETFs** (UNG, GDX, SLV, USO, TLT, GLD, LQD).
- **The rank outcome measures how often a put finishes in the money, not how much it pays.**

## The test, as frozen for round 2

The test is pure and runs on `Sequence[PeriodInputs]`. It reads nothing from the store.

### Data and settings

- **Confirmatory setting:** Δ0.10, 4-week rolls, a 4-year signal window, top_k 5, as of the snapshot of 2026-10-31. These match `_WARM_SIGNAL_TEST`.
- **Every other rail setting** is computed and shown, but labelled *exploratory*, with no p-value and no status. They show only γ̄ and its 90% CI.
- **Each period** uses the eligible names exactly as the signal test already defines them. SPY stays in the cross-section if it is eligible. A period with n_t < 30 is skipped and counted.

### Outcome

- **Decision outcome:** y = the rank of ROI within the period, scaled to [−0.5, 0.5], with ties at mid-rank. "Paid" is not the same thing as "finished in the money".
- **Effect-size companion:** log1p(payoff/cost), reported as γ̄ with a 90% CI. It carries no p-value.
- **Raw-ROI slope:** reported as γ̄ only. It carries no p-value and is never part of the decision.

### Regressors

- **Regressor of interest:** x = the composite rank, scaled to [−0.5, 0.5].
- **Controls:** 22 columns, from round-1 fix F1. All are rank-scaled except where stated.
  - A tensor product of restricted cubic splines on (rank rv20, rank rv_long), with knots at ±0.4 and ±0.133. That gives 3 columns per variable and 15 tensor columns.
  - A restricted cubic spline of rank rv_ratio (3 columns).
  - Within-period z-scores of log rv20 and log rv_long, plus each one's positive part squared (4 columns).

### Estimation and decision

- **Per-period OLS** gives γ_t. γ̄ = mean γ_t.
- **Decision p:** to be chosen in round 2 between these, by simulation:
  - **(A)** Newey–West at lag L = floor(4(T/100)^(2/9)), compared against the Kiefer–Vogelsang fixed-b one-sided critical value.
  - **(B)** A name-cluster bootstrap: resample names with replacement, keeping their whole histories, re-run the full FM and take the bootstrap-t. A name drawn twice counts twice in each period's cross-section.
  - **(C)** Two-way clustering (name and period) on the pooled equivalent.

  Pick the one that holds size ≤ 0.06 in every null, N8 and N9 included.
- **Pass:** one-sided p < 0.05, T ≥ 24, and none of the demotions below fires.

### Demotions

These can only turn a pass into "not robust". They can never create a pass.

- **D1:** γ̄ with the single best period dropped is ≤ 0, or with the best stress episode dropped (the ADR 0033 episodes) is ≤ 0.
- **D2:** the market-neutral γ. Regress γ_t = a + b·r_SPY,t, where r_SPY,t is SPY's return over the period, available as spot at entry and expiry. If a ≤ 0, the pass reflects the sample's market path, not fragility.
- **D3:** equity names only (drop non-equity ETFs from a frozen list in code). If the sign of γ̄ flips or it falls to ≤ 0, the result is driven by asset class.

### Statuses

- `insufficient_data`
- `predicts_beyond_trailing_vol`
- `not_robust` (a demotion fired, and the sentence names which one)
- `no_evidence_beyond_trailing_vol`

### Reported numbers

- γ̄
- the 90% CI
- the decision p
- T, the number of periods skipped, L
- the minimum detectable effect in plain units: the pay-rate gap between the top and bottom quintile of the composite at 80% power, from the simulation and stored as a constant with its derivation
- the γ̄ with no controls (no p)
- a, b from D2
- per-period γ_t

## Round 2 simulation gate

Run on `periods_signals_only.pkl` only. Use at least 2000 reps per world.

- **All round-1 worlds again** against the frozen spec, N0–N7 and X1–X7.
- **New worlds:**
  - **N8:** name effects, α_i with sd 0.1, 0.2 and 0.3 on the logit scale, independent of x and vol.
  - **N8b:** α_i correlated with asset class only.
  - **N9:** a one-factor world. Each name's return is β_i·m_t + ε, with β_i drawn so that it correlates with the real composite's residual. Puts are priced at the true vol, and the market drift is −15%, 0 and +15% per year. This is a null for "fragility" because there is no tail asymmetry. Check: the decision size, and whether D2 removes the drift bias.
- **Alternatives:**
  - A pay-rate effect, sized tiny to large.
  - A magnitude-only alternative: fragile names pay larger when they pay, but no more often.
  - Each with and without name effects.
- **A fresh adversary designs at least three new nulls** against the frozen spec before the spec is accepted.

## Pre-registration

- Before the real outcomes are opened, the frozen spec is hashed (sha256 of this file plus the implementation function's source), and the hash goes in ADR 0035.
- The ADR also states that the choice of rank outcome and controls was made with knowledge of three facts: about 80% of name-periods are at −1, the vol-ratio screen won, and the composite's p was 0.079.

## Wording

- **On a pass:** "Fragility predicted put outcomes beyond trailing vol: …. Under model pricing this means fragile names moved more than their trailing vol implied. Real puts on such names may already price that in."
- **On no evidence:** "No evidence that fragility predicts beyond trailing vol (γ̄ = …, 90% CI […]). This test would detect a pay-rate gap of about X pp 80% of the time."
- **Always:** "Beyond this vol surface only: a vol effect shaped like fragility cannot be told apart from it."

## Surfacing, ADR, out of scope

- **Surfacing:** a `beyond_vol` field on `SignalTestResult`, a sentence after the vol clause in the Bake-off headline, a `fama_macbeth` concept, and a γ_t chart.
- **ADR:** 0035, which amends ADR 0033 §3. The PR waits for Dio, because `docs/adr/` is a guarded path.
- **Out of scope:** real quotes, a point-in-time universe, and changing the signal test's own verdict.
